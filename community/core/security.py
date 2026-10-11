"""Redirect safety and security headers. Access checks live in community.core.authz."""

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


# Sent with every response, unless the view set its own value (token pages send
# Referrer-Policy: no-referrer). A Content-Security-Policy is still on the backlog.
SECURITY_HEADERS = {
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'same-origin',
    'X-Frame-Options': 'DENY',
}


def init_security_headers(app):
    @app.after_request
    def add_security_headers(response):
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
