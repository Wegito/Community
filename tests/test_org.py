from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from community.core.authz.service import permissions_for
from community.core.org.cli import org_cli
from community.core.org.models import RankHistory, Semester
from community.core.org.services import (
    OrgError,
    change_rank,
    create_semester,
    current_rank,
    current_semester,
    rank_by_key,
    semester_for,
    suggest_next_semester,
)
from community.core.time import today_local
from community.extensions import db

from .factories import give_permissions, make_member, make_user


@pytest.mark.parametrize(
    ('day', 'code', 'start', 'end'),
    [
        (date(2026, 10, 11), 'WS26/27', date(2026, 10, 1), date(2027, 3, 31)),
        (date(2027, 2, 1), 'WS26/27', date(2026, 10, 1), date(2027, 3, 31)),
        (date(2027, 4, 1), 'SS27', date(2027, 4, 1), date(2027, 9, 30)),
        (date(2099, 12, 31), 'WS99/00', date(2099, 10, 1), date(2100, 3, 31)),
    ],
)
def test_semester_for(day, code, start, end):
    s = semester_for(day)
    assert (s['code'], s['starts_on'], s['ends_on']) == (code, start, end)


def test_create_semester_rejects_overlap_and_duplicates(app):
    create_semester('WS26/27', 'WS', date(2026, 10, 1), date(2027, 3, 31))
    with pytest.raises(OrgError, match='gibt es schon'):
        create_semester('WS26/27', 'WS', date(2030, 10, 1), date(2031, 3, 31))
    with pytest.raises(OrgError, match='Überschneidet'):
        create_semester('SS27', 'SS', date(2027, 3, 1), date(2027, 9, 30))
    create_semester('SS27', 'SS', date(2027, 4, 1), date(2027, 9, 30))
    assert suggest_next_semester()['code'] == 'WS27/28'
    assert current_semester(date(2027, 5, 1)).code == 'SS27'


def test_rank_gives_permissions(app):
    member = make_member()
    assert permissions_for(member) == frozenset()
    change_rank(member, rank_by_key('fux'), date(2026, 1, 1))
    db.session.commit()
    assert 'tasks.view_all' in permissions_for(member)
    assert 'members.contact.view' not in permissions_for(member)


def test_rank_change_closes_previous_period(app):
    member = make_member()
    change_rank(member, rank_by_key('fux'), date(2026, 1, 1))
    change_rank(member, rank_by_key('bursch'), date(2026, 6, 1))
    db.session.commit()
    rows = db.session.scalars(select(RankHistory).order_by(RankHistory.valid_from)).all()
    assert [(r.rank.key, r.valid_from, r.valid_to) for r in rows] == [
        ('fux', date(2026, 1, 1), date(2026, 5, 31)),
        ('bursch', date(2026, 6, 1), None),
    ]
    assert current_rank(member, date(2026, 5, 31)).key == 'fux'
    assert current_rank(member, date(2026, 6, 1)).key == 'bursch'


def test_future_rank_change_applies_on_its_date(app):
    member = make_member()
    change_rank(member, rank_by_key('fux'), date(2020, 1, 1))
    change_rank(member, rank_by_key('bursch'), today_local() + timedelta(days=7))
    db.session.commit()
    assert current_rank(member).key == 'fux'


def test_rank_change_rules(app):
    target = make_member()
    actor = make_user(email='senior@example.org', name='Senior Person')
    change_rank(target, rank_by_key('fux'), date(2026, 1, 1))
    with pytest.raises(OrgError, match='bereits'):
        change_rank(target, rank_by_key('fux'), date(2026, 3, 1))
    with pytest.raises(OrgError, match='nach dem Beginn'):
        change_rank(target, rank_by_key('bursch'), date(2026, 1, 1))
    with pytest.raises(OrgError, match='eigenen Rang'):
        change_rank(actor.member, rank_by_key('alter_herr'), date(2026, 3, 1), changed_by=actor)


def test_only_one_open_rank_per_member(app):
    member = make_member()
    fux, bursch = rank_by_key('fux'), rank_by_key('bursch')
    db.session.add_all(
        [
            RankHistory(member_id=member.id, rank_id=fux.id, valid_from=date(2026, 1, 1)),
            RankHistory(member_id=member.id, rank_id=bursch.id, valid_from=date(2026, 2, 1)),
        ]
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_rank_change_page(client, login):
    senior = make_user(email='senior@example.org', name='Senior Person')
    give_permissions(senior.member, 'members.rank.change')
    target = make_member('Neu Fuchs')
    login(senior)
    url = f'/org/members/{target.public_id}/rank'
    assert 'kein Rang' in client.get(url).get_data(as_text=True)
    response = client.post(
        url, data={'rank_id': rank_by_key('fux').id, 'valid_from': '2026-10-01', 'convent_ref': 'CC 1.10.'}
    )
    assert response.status_code == 302
    page = client.get(url).get_data(as_text=True)
    assert 'CC 1.10.' in page and 'eingetragen von Senior Person' in page


def test_own_rank_page_has_no_form(client, login):
    senior = make_user(email='senior@example.org', name='Senior Person')
    give_permissions(senior.member, 'members.rank.change')
    login(senior)
    url = f'/org/members/{senior.member.public_id}/rank'
    assert 'nicht selbst ändern' in client.get(url).get_data(as_text=True)
    client.post(url, data={'rank_id': rank_by_key('alter_herr').id, 'valid_from': '2030-01-01'})
    assert current_rank(senior.member).key == 'bursch'


def test_semester_page(client, login):
    user = make_user(perms=['org.semesters.manage'])
    login(user)
    page = client.get('/org/semesters').get_data(as_text=True)
    assert semester_for(today_local())['code'] in page
    client.post(
        '/org/semesters', data={'code': 'SS27', 'kind': 'SS', 'starts_on': '2027-04-01', 'ends_on': '2027-09-30'}
    )
    semester = db.session.scalar(select(Semester))
    client.post(f'/org/semesters/{semester.id}/status', data={'status': 'active'})
    db.session.refresh(semester)
    assert semester.status == 'active'


def test_rename_rank(client, login):
    user = make_user(perms=['system.admin'])
    login(user)
    spefux = rank_by_key('spefux')
    client.post(
        '/org/ranks', data={'rank_id': spefux.id, f'r{spefux.id}-label': 'Speefux', f'r{spefux.id}-abbreviation': 'SF'}
    )
    db.session.refresh(spefux)
    assert spefux.label == 'Speefux'
    assert spefux.key == 'spefux'


def test_cli_set_rank(app):
    user = make_user(perms=[])
    runner = app.test_cli_runner()
    result = runner.invoke(org_cli, ['set-rank', '--email', user.login_email, '--rank', 'bursch'])
    assert result.exit_code == 0, result.output
    assert current_rank(user.member).key == 'bursch'
    assert runner.invoke(org_cli, ['set-rank', '--email', user.login_email, '--rank', 'nope']).exit_code != 0


# --- Rank history after an Austritt, wrong entries (review findings 4, 14, 27) ----


def history(member):
    rows = db.session.scalars(
        select(RankHistory).where(RankHistory.member_id == member.id).order_by(RankHistory.valid_from)
    ).all()
    return [(r.rank.key, r.valid_from, r.valid_to) for r in rows]


def test_new_rank_after_a_wiederaufnahme_starts_after_the_last_one(app):
    from community.core.identity.models import ExitReason
    from community.modules.members.services import exit_member, reactivate_member

    member = make_member()
    change_rank(member, rank_by_key('bursch'), date(2000, 1, 1))
    exit_member(member, today_local(), ExitReason.AUSTRITT, recorded_by=None)
    reactivate_member(member, actor=None)
    db.session.commit()
    for day in (today_local(), date(2010, 1, 1)):
        with pytest.raises(OrgError, match=f'nach dem Ende des letzten Rangs \\({today_local():%d.%m.%Y}\\)'):
            change_rank(member, rank_by_key('alter_herr'), day)
    tomorrow = today_local() + timedelta(days=1)
    change_rank(member, rank_by_key('alter_herr'), tomorrow)
    db.session.commit()
    assert history(member) == [('bursch', date(2000, 1, 1), today_local()), ('alter_herr', tomorrow, None)]


def test_rank_page_suggests_the_day_after_the_last_rank(client, login):
    from community.core.identity.models import ExitReason
    from community.modules.members.services import exit_member, reactivate_member

    senior = make_user(email='senior@example.org', name='Senior Person')
    give_permissions(senior.member, 'members.rank.change')
    member = make_member('Rudi Rückkehr')
    change_rank(member, rank_by_key('bursch'), date(2000, 1, 1))
    exit_member(member, today_local(), ExitReason.AUSTRITT, recorded_by=None)
    reactivate_member(member, actor=None)
    db.session.commit()
    login(senior)
    page = client.get(f'/org/members/{member.public_id}/rank').get_data(as_text=True)
    assert f'value="{(today_local() + timedelta(days=1)).isoformat()}"' in page


def test_former_members_get_no_rank(client, login):
    from community.core.identity.models import ExitReason
    from community.modules.members.services import exit_member

    member = make_member('Ex Mitglied')
    change_rank(member, rank_by_key('bursch'), date(2000, 1, 1))
    exit_member(member, today_local() - timedelta(days=1), ExitReason.AUSTRITT, recorded_by=None)
    db.session.commit()
    with pytest.raises(OrgError, match='kein aktives Mitglied'):
        change_rank(member, rank_by_key('alter_herr'), today_local())

    # Someone who may change ranks but not see former members gets a 404, as on /m/<public_id>.
    consenior = make_user(email='consenior@example.org', name='Con Senior', perms=['members.rank.change'])
    login(consenior)
    url = f'/org/members/{member.public_id}/rank'
    assert client.get(url).status_code == 404
    data = {'rank_id': rank_by_key('alter_herr').id, 'valid_from': today_local().isoformat()}
    assert client.post(url, data=data).status_code == 404
    client.post('/logout')

    # Whoever records exits sees the history, but there is no form.
    schriftwart = make_user(
        email='sw@example.org', name='Sabine Schrift', perms=['members.rank.change', 'members.exit.record']
    )
    login(schriftwart)
    page = client.get(url).get_data(as_text=True)
    assert 'Bursch' in page and 'Rang ändern' not in page and 'Wiederaufnahme' in page
    client.post(url, data=data)
    assert history(member) == [('bursch', date(2000, 1, 1), today_local() - timedelta(days=1))]


def test_rank_that_has_not_begun_can_be_replaced_the_same_day(client, login):
    senior = make_user(email='senior@example.org', name='Senior Person')
    give_permissions(senior.member, 'members.rank.change')
    member = make_member('Neu Ling')
    change_rank(member, rank_by_key('spefux'), today_local())  # e.g. created by the Fuxmajor
    db.session.commit()
    login(senior)
    url = f'/org/members/{member.public_id}/rank'
    response = client.post(url, data={'rank_id': rank_by_key('fux').id, 'valid_from': today_local().isoformat()})
    assert response.status_code == 302
    assert history(member) == [('fux', today_local(), None)]


def test_replacing_a_wrong_rank_restores_the_previous_period(app):
    member = make_member()
    change_rank(member, rank_by_key('bursch'), date(2000, 1, 1))
    change_rank(member, rank_by_key('alter_herr'), today_local())
    change_rank(member, rank_by_key('bursch'), today_local())
    db.session.commit()
    assert history(member) == [('bursch', date(2000, 1, 1), None)]
    # A rank that began before today stays in the history.
    with pytest.raises(OrgError, match='nach dem Beginn'):
        change_rank(member, rank_by_key('alter_herr'), date(2000, 1, 1))
