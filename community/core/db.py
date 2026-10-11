"""Database helpers shared by all modules."""

from sqlalchemy import event
from sqlalchemy.engine import Engine


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
