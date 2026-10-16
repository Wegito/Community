"""Database helpers shared by all modules."""

from datetime import UTC

from sqlalchemy import Column, DateTime, event
from sqlalchemy.engine import Engine
from sqlalchemy.types import TypeDecorator

from community.core.time import utcnow


class UTCDateTime(TypeDecorator):
    """Stores aware datetimes as naive UTC and returns them as aware UTC.

    Naive datetimes are rejected so local times cannot slip in unconverted.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError('UTCDateTime needs a timezone-aware datetime')
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class TimestampMixin:
    """Adds `created_at`, set to the current UTC time on insert."""

    created_at = Column(UTCDateTime, default=utcnow)


@event.listens_for(Engine, 'connect')
def _sqlite_pragmas(dbapi_connection, connection_record):
    """Per-connection SQLite settings: WAL, lock wait and foreign keys."""
    if type(dbapi_connection).__module__.split('.')[0] not in {'sqlite3', 'pysqlite2'}:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute('PRAGMA foreign_keys=ON')
    cursor.execute('PRAGMA busy_timeout=5000')
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.close()
