"""Tasks and Putzplan. Still backed by the old `chore` table until M2-2."""

from flask import Blueprint

bp = Blueprint('tasks', __name__, template_folder='templates')

from . import routes  # noqa: E402,F401
