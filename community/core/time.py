"""Time helpers: store UTC, show Europe/Berlin."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo('Europe/Berlin')


def utcnow() -> datetime:
    """Current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


def to_local(value: datetime) -> datetime:
    """Convert an aware datetime to Berlin time. Naive values are treated as UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(LOCAL_TZ)


def local_to_utc(value: datetime) -> datetime:
    """Interpret a naive datetime entered in a form as Berlin time and convert it to UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=LOCAL_TZ)
    return value.astimezone(UTC)


def format_datetime(value, fmt='%d.%m.%Y %H:%M'):
    """Jinja filter `dt`: Berlin time, German format. Empty for None."""
    if value is None:
        return ''
    return to_local(value).strftime(fmt)


def format_date(value: date | None, fmt='%d.%m.%Y'):
    """Jinja filter `d`: German date format. Empty for None."""
    if value is None:
        return ''
    return value.strftime(fmt)
