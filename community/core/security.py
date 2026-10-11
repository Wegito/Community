"""Redirect safety. Access checks live in community.core.authz."""

from urllib.parse import urlsplit


def safe_next(target):
    """Return `target` if it is a safe local path to redirect to, else None.

    Only paths like "/chores?x=1" are allowed: no scheme or host, no "//" or
    backslash (browsers read "/\\evil.org" as "//evil.org"), no control characters.
    """
    if not target or not target.startswith('/') or target.startswith('//'):
        return None
    if '\\' in target or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in target):
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return None
    return target
