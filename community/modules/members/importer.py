"""Bulk member import (roadmap §3.3 "Bulk"): CSV or Excel file → preview → members without login.

Nothing is stored between the preview and the import. The preview page carries the checked
rows in a signed form field (itsdangerous, salt 'member-import', valid for an hour), and the
import checks them again, because the database may have changed in the meantime.
"""

import csv
import io
import re
import warnings
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import NamedTuple

from email_validator import EmailNotValidError, validate_email
from flask import current_app
from itsdangerous import BadData, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from community.core.identity.models import InvitationChannel, Member, User, normalize_email
from community.core.org import services as org
from community.core.platform import audit
from community.core.time import today_local
from community.extensions import db

from . import services

# Members per file. Also keeps the signed preview well below Flask's MAX_FORM_MEMORY_SIZE.
MAX_ROWS = 500
# Rows read at all, empty ones included (Excel sheets often carry formatted empty rows),
# and columns read per row.
MAX_LINES = 20_000
MAX_COLUMNS = 100
# An .xlsx is a zip archive; refuse archives that unpack to more than this (zip bombs).
MAX_XLSX_UNPACKED = 20 * 1024 * 1024

SIGNATURE_SALT = 'member-import'
SIGNATURE_MAX_AGE = 3600  # seconds

# Fields in template order, with the German column name used in the template and in messages.
COLUMNS = {
    'first_name': 'Vorname',
    'last_name': 'Nachname',
    'biername': 'Biername',
    'email': 'E-Mail',
    'phone': 'Telefon',
    'rank': 'Rang',
    'joined_on': 'Eintritt',
}
# Column names (compared trimmed, case-insensitive, spaces as "_") → field.
HEADER_ALIASES = {
    'vorname': 'first_name',
    'first_name': 'first_name',
    'nachname': 'last_name',
    'last_name': 'last_name',
    'biername': 'biername',
    'e-mail': 'email',
    'email': 'email',
    'telefon': 'phone',
    'phone': 'phone',
    'rang': 'rank',
    'rank': 'rank',
    'eintritt': 'joined_on',
    'joined_on': 'joined_on',
}
# Column sizes of the member table.
MAX_LENGTH = {'first_name': 80, 'last_name': 80, 'biername': 80, 'email': 255, 'phone': 40}

_GERMAN_DATE = re.compile(r'^(\d{1,2})\.(\d{1,2})\.(\d{4})$')
_ISO_DATE = re.compile(r'^(\d{4})-(\d{1,2})-(\d{1,2})$')
_PHONE = re.compile(services.PHONE_PATTERN)

TOO_MANY_ROWS = f'Die Datei hat zu viele Zeilen: höchstens {MAX_ROWS} Mitglieder je Datei.'
NOT_XLSX = 'Die Excel-Datei konnte nicht gelesen werden. Bitte als .xlsx (Excel 2007 oder neuer) oder CSV speichern.'
PREVIEW_INVALID = 'Die Vorschau ist ungültig. Bitte die Datei erneut hochladen.'
PREVIEW_EXPIRED = 'Die Vorschau ist älter als eine Stunde. Bitte die Datei erneut hochladen.'
EMAIL_INVALID = 'E-Mail-Adresse ist ungültig.'
EMAIL_TAKEN = 'E-Mail-Adresse gehört schon zu einem Mitglied.'


class MemberImportError(Exception):
    """The import cannot go ahead; the message is shown to the user (German)."""


class ImportFileError(MemberImportError):
    """The file cannot be read as a member list."""


class PreviewInvalid(MemberImportError):
    """The signed preview is forged, damaged, expired or belongs to someone else."""


@dataclass
class ImportRow:
    """One member from the file: the cells as text, the parsed rank and date, and what is wrong."""

    line: int  # row number in the file; the header is row 1
    values: dict  # field -> trimmed text ('' when empty); the email lowercased
    rank: object = None  # Rank or None
    joined_on: date | None = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    @property
    def is_valid(self):
        return not self.errors

    def payload(self):
        """The row as it travels in the signed preview."""
        data = {name: self.values[name] for name in COLUMNS}
        data['rank'] = self.rank.key if self.rank else ''
        data['joined_on'] = self.joined_on.isoformat() if self.joined_on else ''
        return {'line': self.line, **data}


class Preview(NamedTuple):
    rows: list  # ImportRow, in file order
    unknown_columns: list  # column names that were ignored

    @property
    def valid_rows(self):
        return [row for row in self.rows if row.is_valid]


class ImportResult(NamedTuple):
    members: list
    ranked: int  # members that got a rank
    invited: int  # email invitations queued


# --- Reading the file ------------------------------------------------------------


def text(value):
    """A cell as trimmed text: whole numbers without ".0", dates as dd.mm.yyyy, None as ''."""
    if value is None:
        return ''
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.strftime('%d.%m.%Y')
    return str(value).strip()


def _table(rows):
    """(row number, cells) of the non-empty rows; numbers count every row from 1, like Excel."""
    table = []
    for number, cells in enumerate(rows, start=1):
        if number > MAX_LINES:
            raise ImportFileError(TOO_MANY_ROWS)
        cells = list(cells)[:MAX_COLUMNS]
        if any(text(cell) for cell in cells):
            if len(table) > MAX_ROWS:  # the header plus MAX_ROWS members
                raise ImportFileError(TOO_MANY_ROWS)
            table.append((number, cells))
    return table


def _read_csv(data):
    """UTF-8 with or without BOM; the separator (; or ,) is the one the header line uses more."""
    try:
        content = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ImportFileError(
            'Die CSV-Datei ist nicht UTF-8-kodiert. In Excel „CSV UTF-8“ wählen oder als .xlsx speichern.'
        ) from None
    header = next((line for line in content.splitlines() if line.strip()), '')
    delimiter = ';' if header.count(';') > header.count(',') else ','
    reader = csv.reader(io.StringIO(content, newline=''), delimiter=delimiter)
    try:
        return _table(reader)
    except csv.Error:
        raise ImportFileError(f'Die CSV-Datei ist fehlerhaft (Zeile {reader.line_num}).') from None


def _read_xlsx(data):
    """The first worksheet; date cells arrive as dates, formulas as their last computed value."""
    import openpyxl  # here, not at the top: only the import needs it, and it is slow to load

    buffer = io.BytesIO(data)
    if not zipfile.is_zipfile(buffer):
        raise ImportFileError(NOT_XLSX)
    try:
        with zipfile.ZipFile(buffer) as archive:
            unpacked = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile:
        raise ImportFileError(NOT_XLSX) from None
    if unpacked > MAX_XLSX_UNPACKED:
        raise ImportFileError('Die Excel-Datei ist zu groß. Bitte nur die Mitgliederliste speichern.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')  # openpyxl warns about styles and extensions it skips
            workbook = openpyxl.load_workbook(buffer, read_only=True, data_only=True)
            try:
                sheet = workbook.worksheets[0]
                return _table(sheet.iter_rows(max_col=MAX_COLUMNS, values_only=True))
            finally:
                workbook.close()
    except ImportFileError:
        raise
    except Exception:  # openpyxl raises many kinds of errors for damaged files
        raise ImportFileError(NOT_XLSX) from None


def _read(filename, data):
    name = (filename or '').lower()
    if name.endswith('.csv'):
        return _read_csv(data)
    if name.endswith('.xlsx'):
        return _read_xlsx(data)
    raise ImportFileError('Bitte eine CSV-Datei oder eine Excel-Datei (.xlsx) hochladen.')


def map_header(cells):
    """{column index: field} of the recognised columns, and the names of the ignored ones."""
    columns, unknown, seen = {}, [], {}
    for index, cell in enumerate(cells):
        name = text(cell)
        if not name:
            continue
        name_key = '_'.join(name.casefold().split())
        target = HEADER_ALIASES.get(name_key)
        if target is None:
            unknown.append(name)
            continue
        if target in seen:
            raise ImportFileError(f'Die Spalte „{COLUMNS[target]}“ kommt doppelt vor ({seen[target]}, {name}).')
        seen[target] = name
        columns[index] = target
    if 'first_name' not in seen:
        raise ImportFileError(
            'Die Spalte „Vorname“ fehlt. Die erste Zeile muss die Spaltennamen enthalten (siehe Vorlage).'
        )
    return columns, unknown


def read_upload(filename, data):
    """Read and check an uploaded member list. Returns a Preview; raises ImportFileError."""
    table = _read(filename, data)
    if not table:
        raise ImportFileError('Die Datei ist leer.')
    (_, header), records = table[0], table[1:]
    columns, unknown = map_header(header)
    entries = [
        (line, {name: cells[index] for index, name in columns.items() if index < len(cells)}) for line, cells in records
    ]
    rows = validate(entries)
    if not rows:
        raise ImportFileError('Die Datei enthält nur die Kopfzeile, keine Mitglieder.')
    return Preview(rows, unknown)


# --- Checking the rows -----------------------------------------------------------


def parse_date(value):
    """A date cell, or text as dd.mm.yyyy or yyyy-mm-dd; None if it is no valid date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    value = text(value)
    match = _GERMAN_DATE.match(value)
    if match:
        day, month, year = (int(part) for part in match.groups())
    else:
        match = _ISO_DATE.match(value)
        if not match:
            return None
        year, month, day = (int(part) for part in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _rank_lookup():
    """Ranks in use by key, label and abbreviation, compared case-insensitively."""
    lookup = {}
    for rank in org.ranks():
        for name in (rank.key, rank.label, rank.abbreviation):
            lookup.setdefault(name.casefold(), rank)
    return lookup


def _check_row(row, raw_date, ranks, today):
    values = row.values
    if not values['first_name']:
        row.errors.append('Vorname fehlt.')
    for name, limit in MAX_LENGTH.items():
        if len(values[name]) > limit:
            row.errors.append(f'{COLUMNS[name]} ist zu lang (höchstens {limit} Zeichen).')
    if values['email']:
        try:
            validate_email(values['email'], check_deliverability=False)
        except EmailNotValidError:
            row.errors.append(EMAIL_INVALID)
        else:
            values['email'] = normalize_email(values['email'])
    if values['phone'] and not _PHONE.match(values['phone']):
        row.errors.append(f'Telefon: {services.PHONE_HINT}')
    if values['rank']:
        row.rank = ranks.get(values['rank'].casefold())
        if row.rank is None:
            known = ', '.join(sorted({rank.label for rank in ranks.values()}))
            row.errors.append(f'Unbekannter Rang „{values["rank"]}“ (bekannt: {known}).')
    if values['joined_on']:
        row.joined_on = parse_date(raw_date)
        if row.joined_on is None:
            row.errors.append(f'Eintritt „{values["joined_on"]}“ ist kein Datum (TT.MM.JJJJ oder JJJJ-MM-TT).')
        elif row.joined_on > today:
            row.errors.append('Eintritt liegt in der Zukunft.')


def _lines(rows):
    return ', '.join(str(row.line) for row in rows)


def _check_emails(rows):
    """An email may appear once in the file and must not belong to a member or login yet."""
    by_email = {}
    for row in rows:
        if row.values['email'] and EMAIL_INVALID not in row.errors:
            by_email.setdefault(row.values['email'], []).append(row)
    taken = set()
    if by_email:
        emails = list(by_email)
        taken.update(db.session.scalars(select(Member.email).where(Member.email.in_(emails))))
        taken.update(db.session.scalars(select(User.login_email).where(User.login_email.in_(emails))))
    for email, same in by_email.items():
        if len(same) > 1:
            for row in same:
                row.errors.append(f'E-Mail-Adresse steht mehrfach in der Datei (Zeilen {_lines(same)}).')
        if email in taken:
            for row in same:
                row.errors.append(EMAIL_TAKEN)


def _warn_same_names(rows):
    """Names may repeat (two people can share one), but re-importing a list should stand out."""

    def name_key(first, last):
        return services.fold(first), services.fold(last)

    existing = {
        name_key(first, last) for first, last in db.session.execute(select(Member.first_name, Member.last_name))
    }
    by_name = {}
    for row in rows:
        if row.values['first_name']:
            by_name.setdefault(name_key(row.values['first_name'], row.values['last_name']), []).append(row)
    for key, same in by_name.items():
        for row in same:
            if key in existing:
                row.warnings.append('Ein Mitglied mit diesem Namen gibt es schon.')
            if len(same) > 1:
                row.warnings.append(f'Name steht mehrfach in der Datei (Zeilen {_lines(same)}).')


def validate(entries):
    """Check every entry, given as (row number, {field: cell value}). Returns the ImportRows.

    Rows whose recognised cells are all empty are left out.
    """
    ranks = _rank_lookup()
    today = today_local()
    rows = []
    for line, cells in entries:
        values = {name: text(cells.get(name)) for name in COLUMNS}
        if not any(values.values()):
            continue
        row = ImportRow(line, values)
        _check_row(row, cells.get('joined_on'), ranks, today)
        rows.append(row)
    _check_emails(rows)
    _warn_same_names(rows)
    return rows


# --- The signed preview ----------------------------------------------------------


def _serializer():
    return URLSafeTimedSerializer(current_app.config['SECRET_KEY'], salt=SIGNATURE_SALT)


def sign(rows, *, user, filename=''):
    """The valid rows as a signed string for the preview form, bound to the importing account."""
    payload = {'by': user.id, 'file': (filename or '')[:100], 'rows': [row.payload() for row in rows if row.is_valid]}
    return _serializer().dumps(payload)


def load(token, *, user):
    """The rows of a signed preview, checked again. Returns (file name, ImportRows).

    Raises PreviewInvalid for a forged, damaged or expired preview, or one made by another account.
    """
    try:
        payload = _serializer().loads(token or '', max_age=SIGNATURE_MAX_AGE)
    except SignatureExpired:
        raise PreviewInvalid(PREVIEW_EXPIRED) from None
    except BadData:
        raise PreviewInvalid(PREVIEW_INVALID) from None
    if not isinstance(payload, dict) or payload.get('by') != user.id or not isinstance(payload.get('rows'), list):
        raise PreviewInvalid(PREVIEW_INVALID)
    entries = [(row['line'], {name: row.get(name) for name in COLUMNS}) for row in payload['rows']]
    return payload.get('file') or '', validate(entries)


# --- Importing -------------------------------------------------------------------


def import_members(rows, *, imported_by, permissions=frozenset(), send_invites=False, filename=''):
    """Create a member without login for every row, with rank and optional email invitation.

    All rows must be valid (checked just before, in the same request). The rank starts on the
    entry date, or today if the row has none. With send_invites, members with an email get an
    invitation by email to that address (the mails are queued as jobs); `permissions` are the
    importer's, checked by services.invite(). Commits nothing: the caller commits once, or
    rolls back everything on MemberImportError.
    """
    if not rows or not all(row.is_valid for row in rows):
        raise ValueError('import_members needs valid rows only')
    created, ranked, invited = [], 0, 0
    try:
        for row in rows:
            values = row.values
            member = services.new_member(
                values['first_name'],
                values['last_name'],
                actor=imported_by,
                source='import',
                email=values['email'] or None,
                biername=values['biername'] or None,
                phone=values['phone'] or None,
                joined_on=row.joined_on,
            )
            created.append(member)
            if row.rank is not None:
                org.change_rank(member, row.rank, row.joined_on or today_local(), changed_by=imported_by)
                ranked += 1
            if send_invites and member.email:
                services.invite(
                    member, InvitationChannel.EMAIL, invited_by=imported_by, permissions=permissions, email=member.email
                )
                invited += 1
    except IntegrityError:  # another request took one of the addresses meanwhile (unique index)
        raise MemberImportError(
            f'Zeile {row.line}: {EMAIL_TAKEN} Es wurde nichts importiert; bitte erneut hochladen.'
        ) from None
    except (org.OrgError, services.MemberError) as error:
        raise MemberImportError(f'Zeile {row.line}: {error} Es wurde nichts importiert.') from None
    audit.record(
        'members.import',
        entity_type='member',
        actor=imported_by,
        diff={
            'file': filename or None,
            'created': len(created),
            'ranked': ranked,
            'invited': invited,
            'member_ids': [member.id for member in created],
        },
    )
    return ImportResult(created, ranked, invited)


def template_csv():
    """The CSV template: German column names and one example row, the way Excel opens it
    (UTF-8 with BOM, semicolons)."""
    rank = org.rank_by_key('fux') or next(iter(org.ranks()), None)
    example = {
        'first_name': 'Max',
        'last_name': 'Mustermann',
        'biername': 'Bierfass',
        'email': 'max.mustermann@example.org',
        'phone': '0170 1234567',
        'rank': rank.label if rank else '',
        'joined_on': today_local().strftime('%d.%m.%Y'),
    }
    out = io.StringIO()
    writer = csv.writer(out, delimiter=';', lineterminator='\r\n')
    writer.writerow(COLUMNS.values())
    writer.writerow(example[name] for name in COLUMNS)
    return '﻿' + out.getvalue()
