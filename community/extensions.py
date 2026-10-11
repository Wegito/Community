"""Flask extension instances, initialised in create_app()."""

from pathlib import Path

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import MetaData

# Deterministic constraint names, so migrations can alter them later
# (SQLite needs named constraints for batch operations).
NAMING_CONVENTION = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}

MIGRATIONS_DIR = str(Path(__file__).resolve().parent.parent / 'migrations')

db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING_CONVENTION))
migrate = Migrate(directory=MIGRATIONS_DIR, render_as_batch=True, compare_type=True)
login_manager = LoginManager()
csrf = CSRFProtect()
