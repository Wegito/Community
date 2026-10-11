"""Migrations must work on databases that already contain data, not only on empty ones."""

from flask_migrate import downgrade, upgrade
from sqlalchemy import text

from community import create_app
from community.config import TestingConfig
from community.extensions import db


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
                        "VALUES (1, 'Max@Example.org', 'Max', 'x', 'resident')"
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
                assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar() == '0003'
                assert conn.execute(text('PRAGMA foreign_key_check')).all() == []
                leftovers = conn.execute(text("SELECT name FROM sqlite_master WHERE name LIKE '_alembic_tmp%'")).all()
                assert leftovers == []
                assert conn.execute(text('SELECT status FROM booking WHERE id = 1')).scalar() == 'active'
                assert conn.execute(text('SELECT email FROM "user" WHERE id = 1')).scalar() == 'max@example.org'
            # The app's connections still enforce foreign keys after migrating.
            assert db.session.execute(text('PRAGMA foreign_keys')).scalar() == 1
        finally:
            db.session.remove()
            db.engine.dispose()
