"""Main navigation in layout.html: everyday pages and the "Verwaltung" group.

A link is shown only when its route lets the current user in: the permission of the route's
@require_perm (roadmap §6.2), so the menu never offers a page that answers 403 and needs no
second list of permission codes. A new module adds its pages here (endpoint names only).
"""

from dataclasses import dataclass

from flask import current_app, request
from flask_login import current_user

from community.core.authz import can


@dataclass(frozen=True)
class NavLink:
    label: str
    endpoint: str
    # Other endpoints that belong to this entry, marked as the current page (aria-current).
    also: tuple = ()

    @property
    def is_current(self):
        return request.endpoint == self.endpoint or request.endpoint in self.also


MAIN = (
    NavLink('Ankündigungen', 'announcements.list', also=('announcements.create',)),
    NavLink('Putzplan', 'tasks.list', also=('tasks.create', 'tasks.edit')),
    NavLink('Buchungen', 'bookings.calendar'),
    NavLink(
        'Mitglieder',
        'members.directory',
        also=(
            'members.detail',
            'members.edit',
            'members.new',
            'members.invite',
            'members.invited',
            'members.record_exit',
            'org.change_rank',
        ),
    ),
    NavLink('Ämter', 'org.offices', also=('org.office', 'org.assign_office')),
    NavLink('Wäsche', 'laundry.mine', also=('laundry.new',)),
)

# "Verwaltung": shown only to someone who may open at least one of these pages.
ADMIN = (
    NavLink(
        'Kasse',
        'finance.overview',
        also=(
            'finance.card',
            'finance.journal',
            'finance.booking',
            'finance.new_booking',
            'finance.accounts',
            'finance.edit_account',
            'finance.edit_category',
            'finance.umlage',
            'finance.recurring_list',
            'finance.recurring',
        ),
    ),
    NavLink('Import', 'members.import_upload', also=('members.import_confirm',)),
    NavLink('Gruppen', 'org.groups', also=('org.group',)),
    NavLink('Semester', 'org.semesters', also=('org.edit_semester',)),
    NavLink('Ränge', 'org.ranks'),
    NavLink('Ohne Rang', 'org.first_ranks'),
    NavLink('Ressourcen', 'bookings.resources'),
    NavLink('Wäsche-Abrechnung', 'laundry.overview', also=('laundry.all_entries',)),
    NavLink('Wäsche verwalten', 'laundry.settings', also=('laundry.qr_sheet',)),
    NavLink('Protokoll', 'admin.audit'),
    NavLink('Verein', 'admin.club'),
    NavLink('Jobs', 'admin.jobs'),
)


def may_open(endpoint):
    """Whether the current user passes the access declaration of the route `endpoint`."""
    kind, code = getattr(current_app.view_functions[endpoint], '_access', (None, None))
    if kind == 'public':
        return True
    if not current_user.is_authenticated:
        return False
    return kind == 'login' or (kind == 'perm' and can(code))


def nav_links():
    """For layout.html: the links the current user may open, as {'main': [...], 'admin': [...]}."""
    return {
        'main': [link for link in MAIN if may_open(link.endpoint)],
        'admin': [link for link in ADMIN if may_open(link.endpoint)],
    }
