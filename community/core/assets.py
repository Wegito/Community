"""Static file URLs with a content hash, e.g. /static/app.css?v=3f2a9c01b7e4.

Flask sends static files without a max-age, so browsers may reuse a cached copy for a while
(heuristic freshness). The hash changes with the file, so after a deploy every page loads the
app.css that matches its templates.
"""

import hashlib
from functools import lru_cache
from pathlib import Path

from flask import current_app, url_for


@lru_cache(maxsize=32)
def _digest(path, mtime_ns):
    """Short SHA-256 of a file; cached per modification time, so a rebuilt file gets a new hash."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12]


def asset_url(filename):
    """For templates: url_for('static', ...) plus ?v=<hash of the file's content>."""
    path = Path(current_app.static_folder) / filename
    try:
        version = _digest(str(path), path.stat().st_mtime_ns)
    except OSError:  # missing file: the plain URL (a 404 for the browser), not an error page for every page
        return url_for('static', filename=filename)
    return url_for('static', filename=filename, v=version)
