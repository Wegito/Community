"""Laundry rules: who pays what, recording and undoing entries, the monthly overview."""

import calendar
import io
from datetime import date, datetime, timedelta

from flask import current_app
from sqlalchemy import func, select

from community.core.org.services import current_rank
from community.core.platform import audit
from community.core.time import LOCAL_TZ, local_to_utc, to_local, today_local, utcnow
from community.extensions import db

from .models import DRY_PROGRAMS, LaundryMachine, LaundryPrice, LaundryUse, MachineStatus, PriceKind, PriceTier


class LaundryError(Exception):
    """A rule was broken; the message is shown to the user (German)."""


def tier_for(member, day=None):
    """Members with a rank pay the member price; everyone else (residents) the resident price."""
    return PriceTier.MEMBER if current_rank(member, day) is not None else PriceTier.RESIDENT


def price_on(tier, kind, day):
    price = db.session.scalar(
        select(LaundryPrice.price_cents)
        .where(LaundryPrice.tier == tier, LaundryPrice.kind == kind, LaundryPrice.valid_from <= day)
        .order_by(LaundryPrice.valid_from.desc())
        .limit(1)
    )
    if price is None:
        raise LaundryError('Für diesen Tag ist kein Preis hinterlegt. Bitte beim Consenior melden.')
    return price


def current_prices(day=None):
    """{(tier, kind): cents} valid on `day` (missing pairs are left out)."""
    day = day or today_local()
    result = {}
    for tier in PriceTier.LABELS:
        for kind in PriceKind.LABELS:
            try:
                result[(tier, kind)] = price_on(tier, kind, day)
            except LaundryError:
                pass
    return result


def defect_machines():
    return db.session.scalars(
        select(LaundryMachine).where(LaundryMachine.status == MachineStatus.DEFECT).order_by(LaundryMachine.sort_order)
    ).all()


def find_submission(submission_id):
    return db.session.scalar(select(LaundryUse).where(LaundryUse.submission_id == submission_id))


def record_use(
    member,
    *,
    washes,
    dried,
    submission_id,
    dry_program=None,
    container=None,
    recorded_by=None,
    source='qr',
    used_at=None,
):
    """Save one entry with today's prices. Returns (entry, created). Commits nothing.

    The same submission_id twice (double tap, reload) returns the first entry instead of a copy.
    """
    existing = find_submission(submission_id)
    if existing is not None:
        return existing, False
    if washes not in (0, 1, 2):
        raise LaundryError('Bitte „eine“ oder „beide“ Maschinen wählen.')
    if not washes and not dried:
        raise LaundryError('Bitte Waschen oder Trocknen auswählen.')
    if dried and dry_program not in DRY_PROGRAMS:
        raise LaundryError('Bitte ein Trocknerprogramm wählen.')
    used_at = used_at or utcnow()
    day = to_local(used_at).date()
    tier = tier_for(member, day)
    use = LaundryUse(
        member_id=member.id,
        used_at=used_at,
        washes=washes,
        dried=dried,
        dry_program=dry_program if dried else None,
        container=(container or '').strip() or None,
        tier=tier,
        wash_price_cents=price_on(tier, PriceKind.WASH, day),
        dry_price_cents=price_on(tier, PriceKind.DRY, day),
        source=source,
        submission_id=submission_id,
        recorded_by_user_id=getattr(recorded_by, 'id', None),
    )
    db.session.add(use)
    db.session.flush()
    if source != 'import':
        audit.record(
            'laundry.use',
            use,
            actor=recorded_by,
            diff={'washes': washes, 'dried': dried, 'total_cents': use.total_cents},
        )
    return use, True


def can_undo(use, user, now=None):
    """The person who saved an entry may take it back for a few minutes (typo, wrong button)."""
    now = now or utcnow()
    limit = timedelta(minutes=current_app.config['LAUNDRY_UNDO_MINUTES'])
    return (
        not use.is_void
        and use.recorded_by_user_id == user.id
        and use.member_id == user.member_id
        and now - use.created_at <= limit
    )


def void_use(use, *, by, reason):
    """Cancel an entry (it stays in the list, crossed out, and costs nothing). Commits nothing."""
    if use.is_void:
        return use
    use.voided_at = utcnow()
    use.voided_by_user_id = by.id
    use.void_reason = (reason or '').strip() or None
    audit.record('laundry.void', use, diff={'reason': use.void_reason, 'total_cents': use.total_cents})
    return use


# --- Months ----------------------------------------------------------------------


def parse_month(value):
    """'2026-10' -> (2026, 10); anything else -> the current month (Berlin)."""
    try:
        year, month = (int(part) for part in str(value).split('-'))
        date(year, month, 1)
        return year, month
    except (TypeError, ValueError):
        today = today_local()
        return today.year, today.month


def month_bounds(year, month):
    """UTC start (inclusive) and end (exclusive) of a calendar month in Berlin time."""
    start = local_to_utc(datetime(year, month, 1, tzinfo=LOCAL_TZ))
    last_day = calendar.monthrange(year, month)[1]
    end = local_to_utc(datetime(year, month, last_day, tzinfo=LOCAL_TZ) + timedelta(days=1))
    return start, end


def shift_month(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def uses_in_month(year, month, *, member=None, include_void=True):
    start, end = month_bounds(year, month)
    query = select(LaundryUse).where(LaundryUse.used_at >= start, LaundryUse.used_at < end)
    if member is not None:
        query = query.where(LaundryUse.member_id == member.id)
    if not include_void:
        query = query.where(LaundryUse.voided_at.is_(None))
    return db.session.scalars(query.order_by(LaundryUse.used_at.desc(), LaundryUse.id.desc())).all()


def monthly_summary(year, month):
    """Per person: washes, dryer runs and amount in the month (cancelled entries left out)."""
    rows = {}
    for use in uses_in_month(year, month, include_void=False):
        row = rows.setdefault(
            use.member_id,
            {'member': use.member, 'tiers': set(), 'entries': 0, 'washes': 0, 'dries': 0, 'total_cents': 0},
        )
        row['tiers'].add(use.tier)
        row['entries'] += 1
        row['washes'] += use.washes
        row['dries'] += 1 if use.dried else 0
        row['total_cents'] += use.total_cents
    result = sorted(rows.values(), key=lambda r: (r['member'].last_name.lower(), r['member'].first_name.lower()))
    totals = {key: sum(r[key] for r in result) for key in ('entries', 'washes', 'dries', 'total_cents')}
    return result, totals


def months_with_entries(member=None):
    query = select(LaundryUse.used_at)
    if member is not None:
        query = query.where(LaundryUse.member_id == member.id)
    first = db.session.scalar(query.order_by(LaundryUse.used_at).limit(1))
    return to_local(first).date() if first else None


def count_entries():
    return db.session.scalar(select(func.count(LaundryUse.id)))


# --- Excel -----------------------------------------------------------------------


def export_month_xlsx(year, month):
    """The month as an Excel file: sheet "Abrechnung" (per person) and "Liste" (every entry)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font

    rows, totals = monthly_summary(year, month)
    wb = Workbook()
    ws = wb.active
    ws.title = 'Abrechnung'
    ws.append(['Name', 'Preisgruppe', 'Einträge', 'Wäschen', 'Trocknen', 'Betrag (€)'])
    for row in rows:
        tiers = ', '.join(PriceTier.LABELS[t] for t in sorted(row['tiers']))
        ws.append(
            [row['member'].full_name, tiers, row['entries'], row['washes'], row['dries'], row['total_cents'] / 100]
        )
    ws.append(['Summe', '', totals['entries'], totals['washes'], totals['dries'], totals['total_cents'] / 100])
    _format_sheet(ws, Font, money_column='F')

    ws = wb.create_sheet('Liste')
    ws.append(['Zeit', 'Name', 'Waschen', 'Trocknen', 'Programm', 'Wanne/Box', 'Betrag (€)', 'Storniert'])
    for use in reversed(uses_in_month(year, month)):
        ws.append(
            [
                to_local(use.used_at).replace(tzinfo=None),
                use.member.full_name,
                {0: '', 1: 'eine', 2: 'beide'}[use.washes],
                'ja' if use.dried else '',
                use.dry_program or '',
                use.container or '',
                0 if use.is_void else use.total_cents / 100,
                'storniert' if use.is_void else '',
            ]
        )
    for cell in ws['A'][1:]:
        cell.number_format = 'DD.MM.YYYY HH:MM'
    _format_sheet(ws, Font, money_column='G')

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _format_sheet(ws, font_class, *, money_column):
    for cell in ws[1]:
        cell.font = font_class(bold=True)
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = ws.dimensions
    for cell in ws[money_column][1:]:
        cell.number_format = '#,##0.00 €'
    for column in ws.columns:
        width = max(len(str(cell.value or '')) for cell in column)
        ws.column_dimensions[column[0].column_letter].width = min(max(width + 2, 10), 40)


# --- Import of the old Google Form list ------------------------------------------


def import_google_form_xlsx(path, *, dry_run=True):
    """Import the sheet "LISTE" of the old Waschliste (Google Form export).

    Rows are matched to members by email (contact or login email). Rows without a known email
    are reported, not guessed. Running it twice imports nothing twice.
    """
    from openpyxl import load_workbook

    from community.core.identity.models import Member, User

    wb = load_workbook(path, read_only=True, data_only=True)
    if 'LISTE' not in wb.sheetnames:
        raise LaundryError('Die Datei hat kein Blatt „LISTE“.')
    rows = wb['LISTE'].iter_rows(values_only=True)
    header = [str(h or '').strip().lower() for h in next(rows, [])]
    try:
        col = {name: header.index(name) for name in ('timestamp', 'waschen', 'trocknen', 'email address')}
    except ValueError as error:
        raise LaundryError(f'Spalte fehlt: {error}') from None
    col['modus'] = header.index('trocknen modus') if 'trocknen modus' in header else None
    col['transport'] = header.index('transport') if 'transport' in header else None

    by_email = {m.email: m for m in db.session.scalars(select(Member).where(Member.email.is_not(None)))}
    for user in db.session.scalars(select(User)):
        by_email.setdefault(user.login_email, user.member)

    stats = {'imported': 0, 'duplicates': 0, 'no_email': 0, 'unknown_email': {}, 'invalid': 0}
    for row in rows:
        stamp = row[col['timestamp']] if col['timestamp'] < len(row) else None
        if not isinstance(stamp, datetime):
            continue
        email = str(row[col['email address']] or '').strip().lower()
        if not email:
            stats['no_email'] += 1
            continue
        member = by_email.get(email)
        if member is None:
            stats['unknown_email'][email] = stats['unknown_email'].get(email, 0) + 1
            continue
        washes = {'eine': 1, 'beide': 2}.get(str(row[col['waschen']] or '').strip().lower(), 0)
        dried = bool(row[col['trocknen']])
        program = str(row[col['modus']]).strip() if col['modus'] is not None and row[col['modus']] else None
        if dried and program not in DRY_PROGRAMS:
            program = DRY_PROGRAMS[1]  # "Schranktrocken +", the most used program, when none was given
        container = (
            str(row[col['transport']]).strip()[:80] if col['transport'] is not None and row[col['transport']] else None
        )
        used_at = local_to_utc(stamp.replace(tzinfo=None, microsecond=0))
        submission_id = f'import:{used_at:%Y%m%d%H%M%S}:{email}'
        try:
            _, created = record_use(
                member,
                washes=washes,
                dried=dried,
                dry_program=program,
                container=container,
                submission_id=submission_id[:64],
                source='import',
                used_at=used_at,
            )
        except LaundryError:
            stats['invalid'] += 1
            continue
        stats['imported' if created else 'duplicates'] += 1
    if dry_run:
        db.session.rollback()
    else:
        audit.record('laundry.import', diff={k: v for k, v in stats.items() if k != 'unknown_email'})
        db.session.commit()
    return stats
