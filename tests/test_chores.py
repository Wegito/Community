from datetime import date

from sqlalchemy import select

from community.extensions import db
from community.modules.tasks.models import Chore

from .factories import make_admin, make_user


def make_chore(title='Küche putzen', assigned_to=None, due_date=None):
    chore = Chore(title=title, assigned_to_id=assigned_to.id if assigned_to else None, due_date=due_date)
    db.session.add(chore)
    db.session.commit()
    return chore


def test_unknown_chore_is_404(client, login):
    make_admin()
    login('admin@example.org')
    assert client.get('/chores/edit/999').status_code == 404
    assert client.post('/chores/edit/999', data={'title': 'x'}).status_code == 404


def test_member_cannot_edit_chore(client, login):
    max_ = make_user()
    chore = make_chore(assigned_to=max_)
    login(max_)
    assert client.get(f'/chores/edit/{chore.id}').status_code == 403
    assert client.post(f'/chores/edit/{chore.id}', data={'title': 'gehackt'}).status_code == 403
    db.session.refresh(chore)
    assert chore.title == 'Küche putzen'


def test_admin_creates_chore_with_assignee_and_without_due_date(client, login):
    make_admin()
    max_ = make_user()
    login('admin@example.org')
    response = client.post('/chores/new', data={'title': 'Bad putzen', 'assigned_to_id': str(max_.id), 'due_date': ''})
    assert response.status_code == 302
    chore = db.session.scalar(select(Chore))
    assert chore.title == 'Bad putzen'
    assert chore.assigned_to_id == max_.id
    assert chore.due_date is None
    page = client.get('/chores').get_data(as_text=True)
    assert 'Bad putzen' in page
    assert 'Zuständig: Max' in page


def test_admin_edits_chore_and_clears_assignee(client, login):
    make_admin()
    max_ = make_user()
    chore = make_chore(assigned_to=max_)
    login('admin@example.org')
    response = client.post(
        f'/chores/edit/{chore.id}', data={'title': 'Flur', 'assigned_to_id': 'None', 'due_date': '2026-11-01'}
    )
    assert response.status_code == 302
    db.session.refresh(chore)
    assert (chore.title, chore.assigned_to_id, chore.due_date) == ('Flur', None, date(2026, 11, 1))


def test_member_cannot_create_chore(client, login):
    make_user()
    login('max@example.org')
    client.post('/chores/new', data={'title': 'x'})
    assert db.session.scalar(select(Chore)) is None


def test_assignee_toggles_done(client, login):
    max_ = make_user()
    chore = make_chore(assigned_to=max_)
    login(max_)
    assert client.post(f'/chores/{chore.id}/toggle').status_code == 302
    db.session.refresh(chore)
    assert chore.is_done is True
    client.post(f'/chores/{chore.id}/toggle')
    db.session.refresh(chore)
    assert chore.is_done is False


def test_other_member_cannot_toggle(client, login):
    max_ = make_user()
    make_user(email='moritz@example.org', name='Moritz')
    chore = make_chore(assigned_to=max_)
    login('moritz@example.org')
    assert client.post(f'/chores/{chore.id}/toggle').status_code == 403
    page = client.get('/chores').get_data(as_text=True)
    assert 'Bearbeiten' not in page
    assert f'/chores/{chore.id}/toggle' not in page


def test_undated_chores_are_listed_last(client, login):
    """N15: chores without a due date come after dated ones (SQLite sorts NULL first by default)."""
    make_admin()
    db.session.add_all([Chore(title='Ohne Datum'), Chore(title='Mit Datum', due_date=date(2026, 11, 1))])
    db.session.commit()
    login('admin@example.org')
    page = client.get('/chores').get_data(as_text=True)
    assert page.index('Mit Datum') < page.index('Ohne Datum')
