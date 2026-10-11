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
