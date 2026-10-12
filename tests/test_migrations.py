"""Migrations must work on databases that already contain data, not only on empty ones."""

from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from community import create_app
from community.config import TestingConfig
from community.extensions import MIGRATIONS_DIR, db


def _head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config()
    config.set_main_option('script_location', MIGRATIONS_DIR)
    return ScriptDirectory.from_config(config).get_current_head()


HEAD = _head()


def test_upgrade_and_downgrade_with_data(tmp_path):
    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp_path / "data.db"}'

    app = create_app(Config)
    with app.app_context():
        try:
            upgrade(revision='0001')
            with db.engine.begin() as conn:
                conn.execute(
                    text(
                        'INSERT INTO "user" (id, email, name, password_hash, role) '
                        "VALUES (1, 'Max@Example.org', 'Max', 'x', 'admin')"
                    )
                )
                conn.execute(text("INSERT INTO resource (id, name) VALUES (1, 'Saal')"))
                conn.execute(
                    text(
                        'INSERT INTO booking (id, resource_id, user_id, start, "end") '
                        "VALUES (1, 1, 1, '2030-01-01 10:00:00', '2030-01-01 12:00:00')"
                    )
                )
                conn.execute(text("INSERT INTO announcement (title, body, author_id) VALUES ('a', 'b', 1)"))
                conn.execute(text("INSERT INTO chore (title, assigned_to_id) VALUES ('Küche', 1)"))

            upgrade()
            downgrade(revision='0001')
            upgrade()

            with db.engine.connect() as conn:
                assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar() == HEAD
                assert conn.execute(text('PRAGMA foreign_key_check')).all() == []
                leftovers = conn.execute(text("SELECT name FROM sqlite_master WHERE name LIKE '_alembic_tmp%'")).all()
                assert leftovers == []
                assert conn.execute(text('SELECT status FROM booking WHERE id = 1')).scalar() == 'active'
                login_email, first, last, ref = conn.execute(
                    text(
                        'SELECT u.login_email, m.first_name, m.last_name, m.payment_ref '
                        'FROM "user" u JOIN member m ON m.id = u.member_id WHERE u.id = 1'
                    )
                ).one()
                assert (login_email, first, last) == ('max@example.org', 'Max', '')
                assert ref.startswith('M-')
                # The old admin flag became the sysadmin role.
                assert conn.execute(
                    text(
                        'SELECT r.key FROM member_role mr JOIN role r ON r.id = mr.role_id '
                        'JOIN "user" u ON u.member_id = mr.member_id WHERE u.id = 1'
                    )
                ).scalars().all() == ['custom.sysadmin']
            # The app's connections still enforce foreign keys after migrating.
            assert db.session.execute(text('PRAGMA foreign_keys')).scalar() == 1
        finally:
            db.session.remove()
            db.engine.dispose()


def test_downgrade_restores_only_current_admins(tmp_path):
    """Back to M0, role='admin' goes only to members whose sysadmin role is active today (finding 15)."""
    from datetime import date, timedelta

    today = date.today()  # noqa: DTZ011 (the migration uses the server's local date too)

    class Config(TestingConfig):
        SQLALCHEMY_DATABASE_URI = f'sqlite:///{tmp_path / "admins.db"}'

    def set_role_dates(conn, user_id, valid_from, valid_to=None):
        conn.execute(
            text(
                'UPDATE member_role SET valid_from = :valid_from, valid_to = :valid_to '
                'WHERE member_id = (SELECT member_id FROM "user" WHERE id = :user_id)'
            ),
            {'valid_from': valid_from, 'valid_to': valid_to, 'user_id': user_id},
        )

    app = create_app(Config)
    with app.app_context():
        try:
            upgrade(revision='0003')
            with db.engine.begin() as conn:
                for user_id in (1, 2, 3, 4, 5):
                    conn.execute(
                        text(
                            'INSERT INTO "user" (id, email, name, password_hash, role) '
                            "VALUES (:id, :email, 'Name', 'x', 'admin')"
                        ),
                        {'id': user_id, 'email': f'u{user_id}@example.org'},
                    )
            upgrade()
            with db.engine.begin() as conn:
                # 1 stays admin; 2's role ended last year; 3's starts tomorrow; 4 left the club.
                set_role_dates(conn, 2, date(2025, 1, 1), date(2025, 6, 30))
                set_role_dates(conn, 3, today + timedelta(days=1))
                conn.execute(
                    text('UPDATE member SET status = \'ended\' WHERE id = (SELECT member_id FROM "user" WHERE id = 4)')
                )
                # 5's role was revoked (yesterday was its last day) and given again from today.
                set_role_dates(conn, 5, date(2025, 1, 1), today - timedelta(days=1))
                conn.execute(
                    text(
                        'INSERT INTO member_role (member_id, role_id, valid_from) '
                        'SELECT member_id, role_id, :today FROM member_role '
                        'WHERE member_id = (SELECT member_id FROM "user" WHERE id = 5)'
                    ),
                    {'today': today},
                )
            downgrade(revision='0005')
            with db.engine.connect() as conn:
                roles = dict(conn.execute(text('SELECT id, role FROM "user" ORDER BY id')).all())
            assert roles == {1: 'admin', 2: 'resident', 3: 'resident', 4: 'resident', 5: 'admin'}
        finally:
            db.session.remove()
            db.engine.dispose()
