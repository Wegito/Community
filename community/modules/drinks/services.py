"""Getränke (M6): products and prices, Striche from the Bierwart's Strichliste or a device, and the
numbers for the start page (my Deckel, Bierkönig, Fass-Barometer).

Rules:
- Striche are never edited or deleted; a mistake gets a correction row (the negative quantity).
- The price valid on the day of the Striche is copied into the row (D11); a new price never
  changes old Striche.
- Billing onto the Kontokarte comes later (M6-5), once the Bierwart's Excel is known.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from community.core.identity.models import Member, MemberStatus
from community.core.org.services import current_semester
from community.core.platform import audit
from community.core.time import LOCAL_TZ, local_day_start, local_to_utc, today_local, utcnow
from community.extensions import db

from .models import DrinkConsumption, DrinkPrice, DrinkProduct, DrinkSource

NAME_MAX = 80
MAX_QUANTITY = 50  # per row; more is a typo
API_BATCH_MAX = 500
FUTURE_TOLERANCE = timedelta(minutes=5)


class DrinksError(Exception):
    """A rule was broken; the message is shown to the user (German)."""


# --- Catalog -----------------------------------------------------------------------------------


def products(include_archived=False):
    query = select(DrinkProduct)
    if not include_archived:
        query = query.where(DrinkProduct.archived_at.is_(None))
    return db.session.scalars(query.order_by(DrinkProduct.sort_order, DrinkProduct.name)).all()


def price_on(product, day):
    """The unit price in cents on `day`, or None if the product has no price yet."""
    return db.session.scalar(
        select(DrinkPrice.price_cents)
        .where(DrinkPrice.product_id == product.id, DrinkPrice.valid_from <= day)
        .order_by(DrinkPrice.valid_from.desc())
        .limit(1)
    )


def _clean_name(name, product=None):
    name = (name or '').strip()
    if not name:
        raise DrinksError('Bitte einen Namen angeben.')
    if len(name) > NAME_MAX:
        raise DrinksError(f'Der Name darf höchstens {NAME_MAX} Zeichen haben.')
    query = select(DrinkProduct.id).where(func.lower(DrinkProduct.name) == name.lower())
    if product is not None:
        query = query.where(DrinkProduct.id != product.id)
    if db.session.scalar(query) is not None:
        raise DrinksError(f'„{name}“ gibt es schon.')
    return name


def create_product(name, price_cents, *, counts_as_beer, volume_ml=None, valid_from=None, actor):
    """Commits nothing."""
    product = DrinkProduct(
        name=_clean_name(name),
        counts_as_beer=bool(counts_as_beer),
        volume_ml=volume_ml or None,
        sort_order=len(products(include_archived=True)),
    )
    db.session.add(product)
    db.session.flush()
    set_price(product, price_cents, valid_from or today_local(), actor=actor)
    audit.record('drinks.product.create', product, actor=actor, diff={'name': product.name})
    return product


def update_product(product, *, name, counts_as_beer, volume_ml, archived, actor):
    before = audit.snapshot(product, ('name', 'counts_as_beer', 'volume_ml', 'archived_at'))
    product.name = _clean_name(name, product)
    product.counts_as_beer = bool(counts_as_beer)
    product.volume_ml = volume_ml or None
    if archived and product.archived_at is None:
        product.archived_at = utcnow()
    elif not archived:
        product.archived_at = None
    diff = audit.changes(product, ('name', 'counts_as_beer', 'volume_ml', 'archived_at'), before)
    if diff:
        audit.record('drinks.product.update', product, actor=actor, diff=diff)
    return diff


def set_price(product, price_cents, valid_from, *, actor):
    """The product costs `price_cents` from `valid_from` on. Same day twice: the newer price wins."""
    if not isinstance(price_cents, int) or isinstance(price_cents, bool) or price_cents < 0:
        raise DrinksError('Der Preis darf nicht negativ sein.')
    row = db.session.scalar(
        select(DrinkPrice).where(DrinkPrice.product_id == product.id, DrinkPrice.valid_from == valid_from)
    )
    if row is None:
        row = DrinkPrice(product_id=product.id, valid_from=valid_from, price_cents=price_cents)
        db.session.add(row)
    else:
        row.price_cents = price_cents
    db.session.flush()
    db.session.expire(product, ['prices'])
    audit.record('drinks.price.set', product, actor=actor, diff={'price_cents': price_cents, 'valid_from': valid_from})
    return row


# --- Striche -----------------------------------------------------------------------------------


def find_by_key(key):
    return db.session.scalar(select(DrinkConsumption).where(DrinkConsumption.idempotency_key == key))


def record(member, product, quantity, occurred_at, *, source, recorded_by=None, api_client=None, key=None, note=None):
    """Save Striche. Returns (row, created); the same key twice returns the first row. Commits nothing."""
    existing = find_by_key(key) if key else None
    if existing is not None:
        return existing, False
    if not isinstance(quantity, int) or isinstance(quantity, bool) or not 0 < quantity <= MAX_QUANTITY:
        raise DrinksError(f'Die Anzahl muss zwischen 1 und {MAX_QUANTITY} liegen.')
    if member.status != MemberStatus.ACTIVE:
        raise DrinksError(f'{member.full_name} ist kein aktives Mitglied.')
    if not product.is_active:
        raise DrinksError(f'„{product.name}“ wird nicht mehr verkauft.')
    day = occurred_at.astimezone(LOCAL_TZ).date()
    price = price_on(product, day)
    if price is None:
        raise DrinksError(f'„{product.name}“ hat am {day:%d.%m.%Y} noch keinen Preis.')
    row = DrinkConsumption(
        member_id=member.id,
        product_id=product.id,
        quantity=quantity,
        unit_price_cents=price,
        occurred_at=occurred_at,
        source=source,
        idempotency_key=key,
        recorded_by_user_id=getattr(recorded_by, 'id', None),
        api_client_id=getattr(api_client, 'id', None),
        note=(note or '').strip() or None,
    )
    db.session.add(row)
    db.session.flush()
    return row, True


def evening_time(day):
    """When Striche entered for `day` happened: now for today, otherwise 21:00 that evening (UTC)."""
    if day == today_local():
        return utcnow()
    return local_to_utc(datetime(day.year, day.month, day.day, 21, 0, tzinfo=LOCAL_TZ))


@dataclass(frozen=True)
class SheetResult:
    created: int
    duplicates: int


def record_sheet(day, counts, *, token, recorded_by):
    """The Bierwart's Strichliste for one evening: {(member, product): quantity}. The form's token makes
    a second submit of the same sheet store nothing new. Commits nothing."""
    if day > today_local():
        raise DrinksError('Striche für die Zukunft gibt es nicht.')
    created = duplicates = 0
    occurred_at = evening_time(day)
    for (member, product), quantity in counts.items():
        if not quantity:
            continue
        _, new = record(
            member,
            product,
            quantity,
            occurred_at,
            source=DrinkSource.SHEET,
            recorded_by=recorded_by,
            key=f'sheet:{token}:{member.id}:{product.id}',
        )
        created += new
        duplicates += not new
    if created:
        audit.record('drinks.sheet.record', None, actor=recorded_by, diff={'day': day, 'rows': created, 'token': token})
    return SheetResult(created, duplicates)


def correct(row, *, corrected_by, reason):
    """Cancel Striche with a row of the negative quantity. Commits nothing."""
    reason = (reason or '').strip()
    if not reason:
        raise DrinksError('Bitte einen Grund für die Korrektur angeben.')
    if row.quantity < 0:
        raise DrinksError('Eine Korrektur kann nicht korrigiert werden.')
    if row.corrected_by is not None:
        raise DrinksError('Diese Striche sind schon korrigiert.')
    fix = DrinkConsumption(
        member_id=row.member_id,
        product_id=row.product_id,
        quantity=-row.quantity,
        unit_price_cents=row.unit_price_cents,
        occurred_at=row.occurred_at,
        source=row.source,
        corrects_id=row.id,
        recorded_by_user_id=getattr(corrected_by, 'id', None),
        note=reason[:200],
    )
    db.session.add(fix)
    db.session.flush()
    db.session.expire(row, ['corrected_by'])
    audit.record(
        'drinks.consumption.correct', row, actor=corrected_by, diff={'quantity': row.quantity, 'reason': reason}
    )
    return fix


def recent(*, member=None, limit=200):
    query = select(DrinkConsumption)
    if member is not None:
        query = query.where(DrinkConsumption.member_id == member.id)
    return db.session.scalars(
        query.order_by(DrinkConsumption.occurred_at.desc(), DrinkConsumption.id.desc()).limit(limit)
    ).all()


# --- Numbers -----------------------------------------------------------------------------------


def month_range(day=None):
    day = day or today_local()
    first = day.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    return first, following


def semester_range(day=None):
    """(first day, day after the last) of the current semester; the last 6 months without one."""
    day = day or today_local()
    semester = current_semester(day)
    if semester is not None:
        return semester.starts_on, semester.ends_on + timedelta(days=1)
    return day - timedelta(days=182), day + timedelta(days=1)


def _between(start, end):
    return DrinkConsumption.occurred_at >= local_day_start(start), DrinkConsumption.occurred_at < local_day_start(end)


@dataclass(frozen=True)
class Deckel:
    beers: int
    drinks: int
    total_cents: int


def deckel(member, start, end):
    """A member's Striche from `start` to before `end` (dates): beers, all drinks, amount."""
    row = db.session.execute(
        select(
            func.coalesce(func.sum(DrinkConsumption.quantity).filter(DrinkProduct.counts_as_beer.is_(True)), 0),
            func.coalesce(func.sum(DrinkConsumption.quantity), 0),
            func.coalesce(func.sum(DrinkConsumption.quantity * DrinkConsumption.unit_price_cents), 0),
        )
        .join(DrinkProduct, DrinkProduct.id == DrinkConsumption.product_id)
        .where(DrinkConsumption.member_id == member.id, *_between(start, end))
    ).one()
    return Deckel(*row)


def beer_ranking(start, end, limit=None):
    """[(member, beers)] by beers, most first; only counts, never amounts (owner, 2026-10-16)."""
    beers = func.sum(DrinkConsumption.quantity).label('beers')
    query = (
        select(Member, beers)
        .join(DrinkConsumption, DrinkConsumption.member_id == Member.id)
        .join(DrinkProduct, DrinkProduct.id == DrinkConsumption.product_id)
        .where(DrinkProduct.counts_as_beer.is_(True), Member.status == MemberStatus.ACTIVE, *_between(start, end))
        .group_by(Member.id)
        .having(beers > 0)
        .order_by(beers.desc(), Member.first_name, Member.last_name)
    )
    if limit:
        query = query.limit(limit)
    return [(member, count) for member, count in db.session.execute(query)]


def beer_totals(start, end):
    """(beers, litres) of the whole Bund; litres only from products with a volume."""
    beers, millilitres = db.session.execute(
        select(
            func.coalesce(func.sum(DrinkConsumption.quantity), 0),
            func.coalesce(func.sum(DrinkConsumption.quantity * func.coalesce(DrinkProduct.volume_ml, 0)), 0),
        )
        .join(DrinkProduct, DrinkProduct.id == DrinkConsumption.product_id)
        .where(DrinkProduct.counts_as_beer.is_(True), *_between(start, end))
    ).one()
    return beers, round(millilitres / 1000, 1)


def next_milestone(count, step=100):
    """The next round number above `count` (100, 200, ...)."""
    return (count // step + 1) * step


def last_evening():
    """The latest day with Striche, or None."""
    latest = db.session.scalar(select(func.max(DrinkConsumption.occurred_at)))
    if latest is None:
        return None
    return latest.astimezone(LOCAL_TZ).date()


def count_rows(start, end):
    return db.session.scalar(select(func.count()).select_from(DrinkConsumption).where(*_between(start, end)))


# --- Devices (API) -----------------------------------------------------------------------------


class Rejected(Exception):
    """One API item is not accepted; `reason` is a fixed code from the API contract (§7.3)."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _parse_time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        raise Rejected('invalid_occurred_at') from None
    if moment.tzinfo is None:
        raise Rejected('invalid_occurred_at')  # an offset is required: the tablet's clock zone is unknown
    if moment > utcnow() + FUTURE_TOLERANCE:
        raise Rejected('occurred_at_in_future')
    return moment


def _same(row, member, product, quantity, moment, corrects):
    return (
        row.member_id == member.id
        and row.product_id == product.id
        and row.quantity == quantity
        and row.occurred_at == moment
        and row.corrects_id == (corrects.id if corrects is not None else None)
    )


def ingest_item(client, item):
    """One consumption sent by a device. Returns 'accepted' or 'duplicate'; raises Rejected."""
    if not isinstance(item, dict):
        raise Rejected('invalid_item')
    event_id = item.get('client_event_id')
    if not isinstance(event_id, str) or not 1 <= len(event_id) <= 64:
        raise Rejected('invalid_client_event_id')
    member = db.session.scalar(select(Member).where(Member.public_id == str(item.get('member_id'))))
    if member is None:
        raise Rejected('unknown_member')
    if member.status != MemberStatus.ACTIVE:
        raise Rejected('inactive_member')
    product_id = item.get('product_id')
    product = db.session.get(DrinkProduct, product_id) if isinstance(product_id, int) else None
    if product is None:
        raise Rejected('unknown_product')
    if not product.is_active:
        raise Rejected('inactive_product')
    quantity = item.get('quantity')
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity == 0 or abs(quantity) > MAX_QUANTITY:
        raise Rejected('invalid_quantity')
    moment = _parse_time(item.get('occurred_at'))
    corrects = None
    if quantity < 0:
        corrects = find_by_key(f'api:{client.id}:{item.get("corrects_client_event_id")}')
        if corrects is None or corrects.quantity != -quantity or corrects.member_id != member.id:
            raise Rejected('invalid_correction')
        if corrects.product_id != product.id:
            raise Rejected('invalid_correction')
    key = f'api:{client.id}:{event_id}'
    existing = find_by_key(key)
    if existing is not None:
        if _same(existing, member, product, quantity, moment, corrects):
            return 'duplicate'
        raise Rejected('client_event_id_reused')
    if corrects is not None:
        if corrects.corrected_by is not None:
            raise Rejected('already_corrected')
        fix = DrinkConsumption(
            member_id=member.id,
            product_id=product.id,
            quantity=quantity,
            unit_price_cents=corrects.unit_price_cents,
            occurred_at=moment,
            source=DrinkSource.API,
            idempotency_key=key,
            corrects_id=corrects.id,
            api_client_id=client.id,
        )
        db.session.add(fix)
        db.session.flush()
        db.session.expire(corrects, ['corrected_by'])
        return 'accepted'
    try:
        record(member, product, quantity, moment, source=DrinkSource.API, api_client=client, key=key)
    except DrinksError:
        raise Rejected('no_price') from None
    return 'accepted'


def ingest(client, items):
    """A batch from a device: one result per item, in order. Rejected items do not stop the others.
    Commits nothing."""
    if not isinstance(items, list) or not 1 <= len(items) <= API_BATCH_MAX:
        raise DrinksError(f'"items" muss eine Liste mit 1 bis {API_BATCH_MAX} Einträgen sein.')
    results = []
    for item in items:
        event_id = item.get('client_event_id') if isinstance(item, dict) else None
        savepoint = db.session.begin_nested()
        try:
            status = ingest_item(client, item)
        except Rejected as error:
            savepoint.rollback()
            results.append({'client_event_id': event_id, 'status': 'rejected', 'reason': error.reason})
        else:
            savepoint.commit()
            results.append({'client_event_id': event_id, 'status': status})
    accepted = sum(1 for result in results if result['status'] == 'accepted')
    if accepted:
        audit.record('drinks.api.ingest', client, diff={'accepted': accepted, 'items': len(items)})
    return results
