"""`flask seed demo`: a made-up club to try the app on your own computer (roadmap M8-4).

Only in development, and only on a database without members, so it can never mix with real data.
Every name, email and amount is invented (CLAUDE.md rule 10). All demo logins share one password.
"""

from datetime import date, timedelta

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import func, select

from community.extensions import db

DEMO_PASSWORD = 'demo-passwort-123'  # noqa: S105 (public on purpose: made-up demo logins only)


def _semester_around(day):
    """The current semester, created if missing (WS: October to March, SS: April to September)."""
    from community.core.org.services import create_semester, current_semester

    semester = current_semester(day)
    if semester is not None:
        return semester
    if 4 <= day.month <= 9:
        return create_semester(f'SS{day:%y}', 'SS', date(day.year, 4, 1), date(day.year, 9, 30), status='active')
    first = day.year if day.month >= 10 else day.year - 1
    code = f'WS{first % 100:02d}/{(first + 1) % 100:02d}'
    return create_semester(code, 'WS', date(first, 10, 1), date(first + 1, 3, 31), status='active')


def build_demo(today):
    """Create the demo club. Returns [(role, login email)] for the summary. Commits."""
    from community.core.authz.models import SYSADMIN
    from community.core.authz.service import grant_member_role
    from community.core.identity.services import create_member, create_user
    from community.core.org.services import add_to_group, assign_office, change_rank, groups, office_by_key, rank_by_key
    from community.core.platform.notify import notify
    from community.core.time import utcnow
    from community.modules.announcements.models import Announcement
    from community.modules.bookings.models import Resource
    from community.modules.bookings.services import create_booking
    from community.modules.finance import cosign
    from community.modules.finance.models import FinCategory
    from community.modules.finance.models import JournalKind as K
    from community.modules.finance.services import Line, account_by_key, member_account, post_journal, reverse
    from community.modules.laundry.models import DRY_PROGRAMS
    from community.modules.laundry.services import record_use
    from community.modules.tasks.models import Chore

    semester = _semester_around(today)
    since = min(semester.starts_on, today)
    people = {}

    def person(key, first, last, email, rank, *, login=True, joined=date(2023, 10, 1), **fields):
        member = create_member(first, last, email=email, joined_on=joined, **fields)
        user = create_user(member, email, password=DEMO_PASSWORD) if login else None
        if rank:
            change_rank(member, rank_by_key(rank), joined)
        people[key] = (member, user)
        return member

    person('senior', 'Lukas', 'Becker', 'senior@demo.de', 'bursch', biername='Lupo', phone='0151 0000001')
    person('consenior', 'Jonas', 'Wagner', 'consenior@demo.de', 'bursch', biername='Jojo')
    person('kassenwart', 'Felix', 'Schulz', 'kasse@demo.de', 'bursch', biername='Groschen')
    person('kassenpruefer', 'Moritz', 'Braun', 'pruefer@demo.de', 'bursch', biername='Lupe')
    person('bierwart', 'Paul', 'Hoffmann', 'bier@demo.de', 'bursch', biername='Hopfen')
    person('fuxmajor', 'Max', 'Klein', 'fm@demo.de', 'bursch', biername='Mentor')
    person('fux1', 'Noah', 'Weber', 'noah@demo.de', 'fux', joined=today - timedelta(days=180))
    person('fux2', 'Elias', 'Fischer', 'elias@demo.de', 'fux', joined=today - timedelta(days=180))
    person('spefux', 'Ben', 'Neumann', None, 'spefux', login=False, joined=today - timedelta(days=10))
    person('ah', 'Thomas', 'Schmidt', 'ah@demo.de', 'alter_herr', login=False, joined=date(1995, 10, 1))
    admin = person('admin', 'Ada', 'Admin', 'admin@demo.de', None)
    grant_member_role(admin, SYSADMIN, reason='flask seed demo')
    member = {key: value[0] for key, value in people.items()}
    user = {key: value[1] for key, value in people.items()}

    for key in ('senior', 'consenior', 'kassenwart', 'kassenpruefer', 'fuxmajor', 'bierwart'):
        assign_office(office_by_key(key), member[key], since)
    residents = next(g for g in groups() if g.kind == 'residence')
    for key in ('consenior', 'bierwart', 'fux1', 'fux2'):
        add_to_group(residents, member[key], since)

    db.session.add_all(
        [
            Announcement(
                title='Semesterantrittskneipe am Freitag',
                body='Freitag, 20 Uhr im Kneipsaal. Pflichtveranstaltung für die Aktivitas.',
                author_id=user['senior'].id,
            ),
            Announcement(
                title='Hausputz am Samstag',
                body='Samstag ab 10 Uhr. Die Gruppen hängen in der Küche aus.',
                author_id=user['senior'].id,
            ),
            Chore(title='Küche putzen', assigned_to_id=user['fux1'].id, due_date=today + timedelta(days=2)),
            Chore(title='Leergut wegbringen', assigned_to_id=user['bierwart'].id, due_date=today + timedelta(days=4)),
            Chore(title='Flur saugen', assigned_to_id=user['fux2'].id, due_date=today + timedelta(days=6)),
        ]
    )
    hall, projector = Resource(name='Kneipsaal'), Resource(name='Beamer')
    db.session.add_all([hall, projector])
    db.session.commit()
    start = (utcnow() + timedelta(days=3)).replace(hour=17, minute=0, second=0, microsecond=0)
    create_booking(hall.id, user['consenior'].id, start, start + timedelta(hours=5))
    create_booking(projector.id, user['senior'].id, start + timedelta(days=1), start + timedelta(days=1, hours=3))

    # The Kasse: made-up amounts, posted by the Kassenwart.
    category = {c.name: c for c in db.session.scalars(select(FinCategory))}
    kneipen = category.get('Kneipen')
    herrenabend = category.get('Herrenabend', kneipen)

    def card(key):
        return member_account(member[key])

    def book(kind, title, days_ago, lines, beleg=None):
        journal, _ = post_journal(
            kind, title, today - timedelta(days=days_ago), lines, posted_by=user['kassenwart'], beleg=beleg
        )
        return journal

    book(
        K.OPENING,
        'Eröffnung der Konten',
        10,
        [
            Line(account_by_key('giro'), account_by_key('eigenkapital'), 420000),
            Line(account_by_key('bar'), account_by_key('eigenkapital'), 35000),
        ],
    )
    active = ('senior', 'consenior', 'kassenpruefer', 'bierwart', 'fuxmajor', 'fux1', 'fux2')
    book(K.SPLIT, 'Aktivenbeitrag', 9, [Line(card(k), account_by_key('beitraege'), 750) for k in active], 'B1')
    book(
        K.CHARGE,
        'Semesterantrittskneipe',
        6,
        [Line(card(k), account_by_key('verschiedenes'), 1200, kneipen) for k in ('fux1', 'fux2', 'bierwart')],
        'B2',
    )
    book(K.EXPENSE, 'Einkauf Kneipe auf KK', 7, [Line(account_by_key('verschiedenes'), card('fux1'), 4280, kneipen)])
    book(K.MANUAL, 'Getränkelieferung', 8, [Line(account_by_key('kueche'), account_by_key('giro'), 81240)], 'G1')
    book(K.PAYMENT, 'Überweisung', 5, [Line(account_by_key('giro'), card('consenior'), 3000)], 'G2')
    book(K.PAYMENT, 'Bareinzahlung', 4, [Line(account_by_key('bar'), card('fux2'), 1950)], 'K1')
    wrong = book(
        K.CHARGE, 'Herrenabend', 4, [Line(card('bierwart'), account_by_key('verschiedenes'), 2500, herrenabend)]
    )
    reverse(wrong, reversed_by=user['kassenwart'], reason='falsche Person, war der Fuxmajor')
    book(K.CHARGE, 'Herrenabend', 4, [Line(card('fuxmajor'), account_by_key('verschiedenes'), 2500, herrenabend)])
    # The Kassenwart's own Auslage waits for the Kassenprüfer (push-TAN).
    with current_app.test_request_context():
        cosign.request_booking(
            K.EXPENSE,
            'Auslage Kneipeneinkauf',
            today - timedelta(days=1),
            [Line(account_by_key('verschiedenes'), card('kassenwart'), 3650, kneipen)],
            requested_by=user['kassenwart'],
        )
        notify(member['senior'], 'office_assigned', f'Du bist ab {since:%d.%m.%Y} Senior.')

    # Laundry of the last days.
    for index, (key, washes, dried, hours) in enumerate(
        [('fux1', 1, True, 26), ('fux2', 2, False, 20), ('consenior', 1, True, 15), ('fux1', 2, True, 9)]
    ):
        record_use(
            member[key],
            washes=washes,
            dried=dried,
            submission_id=f'demo-{index}',
            dry_program=DRY_PROGRAMS[1] if dried else None,
            recorded_by=user[key],
            used_at=utcnow() - timedelta(hours=hours),
        )
    db.session.commit()
    return [
        ('Admin (alles außer Kasse)', 'admin@demo.de'),
        ('Senior', 'senior@demo.de'),
        ('Kassenwart', 'kasse@demo.de'),
        ('Kassenprüfer', 'pruefer@demo.de'),
        ('Fuxmajor', 'fm@demo.de'),
        ('Fux (normales Mitglied)', 'noah@demo.de'),
    ]


@click.group('seed')
def seed_cli():
    """Example data."""


@seed_cli.command('demo')
@with_appcontext
def demo():
    """Fill an empty development database with a made-up club."""
    from community.core.authz.models import RolePermission
    from community.core.authz.service import seed_grants, sync_permissions
    from community.core.bootstrap import _database_is_current
    from community.core.identity.models import Member
    from community.core.time import today_local

    if current_app.config.get('ENV_NAME') not in ('development', 'testing'):
        raise click.ClickException('Demo data only in development (APP_ENV=development).')
    if not _database_is_current():
        raise click.ClickException('The database is not up to date. Run `flask db upgrade` first.')
    if db.session.scalar(select(func.count()).select_from(Member)):
        raise click.ClickException('The database already has members; demo data goes into an empty one only.')
    sync_permissions()
    if db.session.scalar(select(RolePermission.role_id).limit(1)) is None:
        seed_grants()
    logins = build_demo(today_local())
    click.echo(f'Demo club ready. Every login has the password: {DEMO_PASSWORD}')
    for role, email in logins:
        click.echo(f'  {role:<28} {email}')
