"""M1-9: bulk member import from CSV or Excel (roadmap §3.3 "Bulk")."""

import io
import re
import zipfile
from datetime import date, datetime, timedelta

import openpyxl
import pytest
from itsdangerous import TimestampSigner
from sqlalchemy import func, select

from community.core.identity.models import Invitation, Member
from community.core.org.services import current_rank_entry
from community.core.platform.audit import AuditLog
from community.core.platform.jobs import Job
from community.core.time import today_local
from community.extensions import db
from community.modules.members import importer

from .factories import give_permissions, make_admin, make_member, make_officer, make_user

HEADER = ['Vorname', 'Nachname', 'Biername', 'E-Mail', 'Telefon', 'Rang', 'Eintritt']
PAYLOAD_RE = re.compile(r'name="payload"[^>]*value="([^"]*)"')


def csv_bytes(rows, sep=';', bom=False):
    content = ''.join(sep.join(cells) + '\r\n' for cells in rows)
    return (b'\xef\xbb\xbf' if bom else b'') + content.encode('utf-8')


def xlsx_bytes(rows):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def preview_of(rows, sep=';', bom=False):
    return importer.read_upload('liste.csv', csv_bytes(rows, sep=sep, bom=bom))


def errors(preview):
    return {row.line: row.errors for row in preview.rows}


def members_named(first_name):
    return db.session.scalars(select(Member).where(Member.first_name == first_name)).all()


def member_count():
    return db.session.scalar(select(func.count(Member.id)))


def audit_entries(action):
    return db.session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)).all()


GOOD_ROWS = [
    HEADER,
    ['Anna', 'Alpha', 'Ameise', 'Anna@Example.org', '0170 1234567', 'Fux', '01.10.2024'],
    ['Bernd', 'Beta', '', '', '', 'alter_herr', '1988-04-15'],
    ['Carla', '', '', 'carla@example.org', '', '', ''],
]


# --- Reading CSV and Excel files -------------------------------------------------


@pytest.mark.parametrize(('sep', 'bom'), [(';', False), (',', False), (';', True), (',', True)])
def test_csv_separators_and_bom(app, sep, bom):
    preview = preview_of(GOOD_ROWS, sep=sep, bom=bom)
    assert [row.line for row in preview.rows] == [2, 3, 4]
    assert all(row.is_valid for row in preview.rows), errors(preview)
    anna, bernd, carla = preview.rows
    assert anna.values == {
        'first_name': 'Anna',
        'last_name': 'Alpha',
        'biername': 'Ameise',
        'email': 'anna@example.org',
        'phone': '0170 1234567',
        'rank': 'Fux',
        'joined_on': '01.10.2024',
    }
    assert anna.rank.key == 'fux' and anna.joined_on == date(2024, 10, 1)
    assert bernd.rank.key == 'alter_herr' and bernd.joined_on == date(1988, 4, 15)
    assert carla.rank is None and carla.joined_on is None
    assert preview.unknown_columns == []


def test_csv_quoted_fields_umlauts_and_empty_rows(app):
    content = (
        '﻿Vorname,Nachname,E-Mail\r\n'
        '"Jörg","Müller, jun.",joerg@example.org\r\n'
        '\r\n'
        ',,\r\n'
        '  Özlem  ,"Şahin ""Ötzi""",\r\n'
    ).encode()
    preview = importer.read_upload('liste.CSV', content)
    assert [(row.line, row.values['first_name'], row.values['last_name']) for row in preview.rows] == [
        (2, 'Jörg', 'Müller, jun.'),
        (5, 'Özlem', 'Şahin "Ötzi"'),
    ]


def test_header_aliases_are_case_insensitive_and_unknown_columns_are_listed(app):
    rows = [
        [' first_name ', 'LAST_NAME', 'biername', 'Email', 'phone', 'Rank', 'Joined_On', 'Bemerkung', ''],
        ['Anna', 'Alpha', 'Ameise', 'anna@example.org', '0170', 'B', '2020-01-02', 'egal', 'x'],
    ]
    preview = preview_of(rows)
    (row,) = preview.rows
    assert row.is_valid, row.errors
    assert row.values['last_name'] == 'Alpha' and row.values['email'] == 'anna@example.org'
    assert row.rank.key == 'bursch' and row.joined_on == date(2020, 1, 2)
    assert preview.unknown_columns == ['Bemerkung']


def test_german_header_variants(app):
    preview = preview_of([['vorname', 'E-MAIL', 'Telefon', 'RANG', 'eintritt'], ['Anna', '', '', 'spefux', '']])
    assert preview.rows[0].rank.key == 'spefux'


def test_only_first_name_column_is_required(app):
    preview = preview_of([['Vorname'], ['Anna'], ['Bernd']])
    assert [row.values['first_name'] for row in preview.rows] == ['Anna', 'Bernd']
    assert all(row.is_valid for row in preview.rows)


def test_xlsx_with_date_cells_numbers_and_aliases(app):
    rows = [
        ['first_name', 'Nachname', 'email', 'Telefon', 'Rang', 'Eintritt', 'Notiz'],
        ['Anna', 'Alpha', 'anna@example.org', 491701234567, 'Fux', date(2024, 10, 1), 'x'],
        [None, None, None, None, None, None, None],
        ['Bernd', 'Beta', None, None, 'AH', datetime(1988, 4, 15, 0, 0), None],
        ['Carla', None, None, None, None, '15.03.2025', None],
    ]
    preview = importer.read_upload('Mitglieder.XLSX', xlsx_bytes(rows))
    assert [row.line for row in preview.rows] == [2, 4, 5]
    assert all(row.is_valid for row in preview.rows), errors(preview)
    anna, bernd, carla = preview.rows
    assert anna.values['phone'] == '491701234567'
    assert anna.joined_on == date(2024, 10, 1) and anna.values['joined_on'] == '01.10.2024'
    assert bernd.rank.key == 'alter_herr' and bernd.joined_on == date(1988, 4, 15)
    assert carla.joined_on == date(2025, 3, 15)
    assert preview.unknown_columns == ['Notiz']


def test_xlsx_number_in_date_column_is_no_date(app):
    preview = importer.read_upload('l.xlsx', xlsx_bytes([['Vorname', 'Eintritt'], ['Anna', 45000]]))
    assert 'kein Datum' in preview.rows[0].errors[0]


@pytest.mark.parametrize(
    ('filename', 'data', 'message'),
    [
        ('liste.txt', b'Vorname\r\nAnna\r\n', 'CSV-Datei oder eine Excel-Datei'),
        ('liste.xls', b'\xd0\xcf\x11\xe0', 'CSV-Datei oder eine Excel-Datei'),
        ('liste.csv', b'', 'leer'),
        ('liste.csv', b'\r\n;;\r\n', 'leer'),
        ('liste.csv', b'Vorname;Nachname\r\n', 'nur die Kopfzeile'),
        ('liste.csv', b'Vorname;Nachname\r\n;\r\n', 'nur die Kopfzeile'),
        ('liste.csv', 'Vorname;Nachname\r\nJörg;Müller\r\n'.encode('cp1252'), 'UTF-8'),
        ('liste.csv', b'Vorname\r\n' + b'x' * 200_000 + b'\r\n', 'fehlerhaft'),
        ('liste.csv', b'Name;E-Mail\r\nAnna;a@example.org\r\n', 'Spalte „Vorname“ fehlt'),
        ('liste.csv', b'Vorname;first_name\r\nAnna;Anna\r\n', 'kommt doppelt vor'),
        ('liste.xlsx', b'not a zip file', 'Excel-Datei konnte nicht gelesen werden'),
        ('liste.xlsx', b'PK\x03\x04broken', 'Excel-Datei konnte nicht gelesen werden'),
    ],
)
def test_unreadable_files(app, filename, data, message):
    with pytest.raises(importer.ImportFileError, match=message):
        importer.read_upload(filename, data)


def test_zip_that_is_no_workbook(app):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('hello.txt', 'hi')
    with pytest.raises(importer.ImportFileError, match='Excel-Datei konnte nicht gelesen werden'):
        importer.read_upload('liste.xlsx', buffer.getvalue())


def test_xlsx_that_unpacks_too_large_is_refused(app, monkeypatch):
    monkeypatch.setattr(importer, 'MAX_XLSX_UNPACKED', 1000)
    with pytest.raises(importer.ImportFileError, match='zu groß'):
        importer.read_upload('liste.xlsx', xlsx_bytes([['Vorname'], ['Anna']]))


def test_row_limit(app, monkeypatch):
    monkeypatch.setattr(importer, 'MAX_ROWS', 3)
    assert len(preview_of([['Vorname'], ['A'], ['B'], ['C']]).rows) == 3
    with pytest.raises(importer.ImportFileError, match='zu viele Zeilen'):
        preview_of([['Vorname'], ['A'], ['B'], ['C'], ['D']])


def test_line_limit_counts_empty_rows_too(app, monkeypatch):
    monkeypatch.setattr(importer, 'MAX_LINES', 5)
    assert len(preview_of([['Vorname'], ['A'], [''], [''], ['B']]).rows) == 2
    with pytest.raises(importer.ImportFileError, match='zu viele Zeilen'):
        preview_of([['Vorname'], ['A'], [''], [''], [''], ['B']])


# --- Checking rows ---------------------------------------------------------------


def test_row_errors(app):
    future = (today_local() + timedelta(days=1)).strftime('%d.%m.%Y')
    rows = [
        HEADER,
        ['', 'Ohne Vorname', '', '', '', '', ''],
        ['Anna', '', '', 'keine-adresse', '', '', ''],
        ['Bernd', '', '', '', '', 'Senior', ''],
        ['Carla', '', '', '', '', '', '2024-13-01'],
        ['Dora', '', '', '', '', '', '31.02.2024'],
        ['Emil', '', '', '', '', '', 'gestern'],
        ['Fritz', '', '', '', '', '', future],
        ['Gina', '', '', '', '0170 abc', '', ''],
        ['H' * 81, '', '', '', '', '', ''],
        ['Ida', '', '', '', '', 'fux', '01.10.2020'],
    ]
    found = errors(preview_of(rows))
    assert found[2] == ['Vorname fehlt.']
    assert found[3] == [importer.EMAIL_INVALID]
    assert found[4] == ['Unbekannter Rang „Senior“ (bekannt: Alter Herr, Bursch, Fux, Spefux).']
    assert found[5] == ['Eintritt „2024-13-01“ ist kein Datum (TT.MM.JJJJ oder JJJJ-MM-TT).']
    assert found[6] == ['Eintritt „31.02.2024“ ist kein Datum (TT.MM.JJJJ oder JJJJ-MM-TT).']
    assert 'ist kein Datum' in found[7][0]
    assert found[8] == ['Eintritt liegt in der Zukunft.']
    assert found[9] == ['Telefon: Nur Ziffern, Leerzeichen und + ( ) / . -']
    assert found[10] == ['Vorname ist zu lang (höchstens 80 Zeichen).']
    assert found[11] == []


@pytest.mark.parametrize('value', ['fux', 'FUX', 'Fux', ' f ', 'F'])
def test_rank_by_key_label_or_abbreviation(app, value):
    (row,) = preview_of([['Vorname', 'Rang'], ['Anna', value]]).rows
    assert row.rank is not None and row.rank.key == 'fux'


def test_archived_rank_is_unknown(app):
    from community.core.org.services import rank_by_key
    from community.core.time import utcnow

    rank_by_key('spefux').archived_at = utcnow()
    db.session.commit()
    (row,) = preview_of([['Vorname', 'Rang'], ['Anna', 'Spefux']]).rows
    assert 'Unbekannter Rang' in row.errors[0]


@pytest.mark.parametrize(
    ('value', 'expected'),
    [('1.2.2020', date(2020, 2, 1)), ('01.02.2020', date(2020, 2, 1)), ('2020-2-1', date(2020, 2, 1))],
)
def test_date_formats(app, value, expected):
    (row,) = preview_of([['Vorname', 'Eintritt'], ['Anna', value]]).rows
    assert row.joined_on == expected


def test_duplicate_email_in_file_marks_every_row(app):
    rows = [
        ['Vorname', 'E-Mail'],
        ['Anna', 'same@example.org'],
        ['Bernd', 'other@example.org'],
        ['Carla', ' SAME@example.org '],
        ['Dora', 'kaputt@'],
        ['Emil', 'kaputt@'],
    ]
    found = errors(preview_of(rows))
    message = 'E-Mail-Adresse steht mehrfach in der Datei (Zeilen 2, 4).'
    assert found[2] == [message]
    assert found[4] == [message]
    assert found[3] == []
    # Invalid addresses are reported as invalid only.
    assert found[5] == [importer.EMAIL_INVALID]
    assert found[6] == [importer.EMAIL_INVALID]


def test_email_of_existing_member_or_login_is_refused(app):
    make_member('Kon Takt', email='contact@example.org')
    make_user(email='login@example.org', name='Log In')
    rows = [['Vorname', 'E-Mail'], ['Anna', 'Contact@example.org'], ['Bernd', 'login@example.org'], ['C', 'c@x.org']]
    found = errors(preview_of(rows))
    assert found[2] == [importer.EMAIL_TAKEN]
    assert found[3] == [importer.EMAIL_TAKEN]
    assert found[4] == []


def test_same_names_only_warn(app):
    make_member('Anna Alpha')
    rows = [['Vorname', 'Nachname'], ['anna', 'ALPHA'], ['Bernd', 'Beta'], ['Bernd', 'Beta'], ['Carla', '']]
    preview = preview_of(rows)
    assert all(row.is_valid for row in preview.rows)
    warnings = {row.line: row.warnings for row in preview.rows}
    assert warnings[2] == ['Ein Mitglied mit diesem Namen gibt es schon.']
    assert warnings[3] == warnings[4] == ['Name steht mehrfach in der Datei (Zeilen 3, 4).']
    assert warnings[5] == []


# --- Signed preview --------------------------------------------------------------


def test_sign_and_load_round_trip(app):
    user = make_admin()
    preview = preview_of(GOOD_ROWS + [['', 'kaputt', '', '', '', '', '']])
    token = importer.sign(preview.rows, user=user, filename='liste.csv')
    filename, rows = importer.load(token, user=user)
    assert filename == 'liste.csv'
    # Only the valid rows travel; they come back checked again.
    assert [row.line for row in rows] == [2, 3, 4]
    assert [row.payload() for row in rows] == [row.payload() for row in preview.valid_rows]
    assert all(row.is_valid for row in rows)
    assert rows[0].rank.key == 'fux' and rows[0].joined_on == date(2024, 10, 1)


def test_tampered_preview_is_rejected(app):
    user = make_admin()
    token = importer.sign(preview_of(GOOD_ROWS).rows, user=user)
    head, _, signature = token.rpartition('.')
    forged = head + '.' + ('A' if signature[0] != 'A' else 'B') + signature[1:]
    for bad in (forged, token[:-3], 'garbage', ''):
        with pytest.raises(importer.PreviewInvalid, match='ungültig'):
            importer.load(bad, user=user)


def test_preview_of_another_purpose_or_account_is_rejected(app):
    from itsdangerous import URLSafeTimedSerializer

    user = make_admin()
    other = make_user(email='other@example.org', name='Other Importer')
    token = importer.sign(preview_of(GOOD_ROWS).rows, user=user)
    with pytest.raises(importer.PreviewInvalid, match='ungültig'):
        importer.load(token, user=other)
    # Same secret, other salt (e.g. a password reset token) is not a preview.
    foreign = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='password-reset').dumps({'by': user.id})
    with pytest.raises(importer.PreviewInvalid, match='ungültig'):
        importer.load(foreign, user=user)


def expired_token(monkeypatch, rows, user):
    real = TimestampSigner.get_timestamp
    monkeypatch.setattr(TimestampSigner, 'get_timestamp', lambda self: real(self) - importer.SIGNATURE_MAX_AGE - 5)
    token = importer.sign(rows, user=user)
    monkeypatch.setattr(TimestampSigner, 'get_timestamp', real)
    return token


def test_expired_preview_is_rejected(app, monkeypatch):
    user = make_admin()
    token = expired_token(monkeypatch, preview_of(GOOD_ROWS).rows, user)
    with pytest.raises(importer.PreviewInvalid, match='älter als eine Stunde'):
        importer.load(token, user=user)


# --- Importing -------------------------------------------------------------------


def test_import_creates_members_with_ranks(app):
    importer_user = make_admin()
    rows = preview_of(GOOD_ROWS).rows
    before = member_count()
    result = importer.import_members(rows, imported_by=importer_user, filename='liste.csv')
    db.session.commit()

    assert member_count() == before + 3
    assert (len(result.members), result.ranked, result.invited) == (3, 2, 0)
    (anna,) = members_named('Anna')
    assert (anna.last_name, anna.biername, anna.email, anna.phone) == (
        'Alpha',
        'Ameise',
        'anna@example.org',
        '0170 1234567',
    )
    assert anna.joined_on == date(2024, 10, 1)
    assert anna.user is None and anna.payment_ref == f'M-{anna.id:04d}'
    entry = current_rank_entry(anna)
    assert entry.rank.key == 'fux' and entry.valid_from == date(2024, 10, 1)
    assert entry.changed_by_user_id == importer_user.id
    (bernd,) = members_named('Bernd')
    assert current_rank_entry(bernd).valid_from == date(1988, 4, 15)
    (carla,) = members_named('Carla')
    assert carla.joined_on is None and carla.last_name == '' and carla.biername is None and carla.phone is None
    assert current_rank_entry(carla) is None

    created = audit_entries('members.create')[-3:]
    assert [entry.diff['source'] for entry in created] == ['import'] * 3
    assert all(entry.actor_user_id == importer_user.id for entry in created)
    assert len(audit_entries('org.rank.change')) >= 2
    (summary,) = audit_entries('members.import')
    assert summary.actor_user_id == importer_user.id
    assert summary.diff == {
        'file': 'liste.csv',
        'created': 3,
        'ranked': 2,
        'invited': 0,
        'member_ids': [anna.id, bernd.id, carla.id],
    }


def test_rank_without_entry_date_starts_today(app):
    rows = preview_of([['Vorname', 'Rang'], ['Anna', 'Bursch']]).rows
    importer.import_members(rows, imported_by=make_admin())
    db.session.commit()
    (anna,) = members_named('Anna')
    assert anna.joined_on is None
    assert current_rank_entry(anna).valid_from == today_local()


def test_import_with_email_invitations(app):
    inviter = make_admin()
    rows = preview_of(GOOD_ROWS).rows
    with app.test_request_context():
        result = importer.import_members(rows, imported_by=inviter, send_invites=True)
        db.session.commit()
    assert result.invited == 2
    invitations = db.session.scalars(select(Invitation).order_by(Invitation.id)).all()
    assert [(i.member.first_name, i.channel, i.email) for i in invitations] == [
        ('Anna', 'email', 'anna@example.org'),
        ('Carla', 'email', 'carla@example.org'),
    ]
    assert all(i.created_by_user_id == inviter.id for i in invitations)
    jobs = db.session.scalars(select(Job).where(Job.kind == 'mail.send').order_by(Job.id)).all()
    assert [job.payload['to'] for job in jobs] == ['anna@example.org', 'carla@example.org']
    assert audit_entries('members.import')[-1].diff['invited'] == 2


def test_import_is_all_or_nothing_on_a_database_conflict(app):
    rows = preview_of(GOOD_ROWS).rows
    # Another request takes Carla's address after the rows were checked.
    admin = make_admin()
    make_member('Schnell Er', email='carla@example.org')
    before = member_count()
    with pytest.raises(importer.MemberImportError, match='Zeile 4: E-Mail-Adresse gehört schon'):
        importer.import_members(rows, imported_by=admin)
    db.session.rollback()
    assert member_count() == before
    assert members_named('Anna') == [] and members_named('Bernd') == []
    assert audit_entries('members.import') == []


def test_import_refuses_invalid_rows(app):
    rows = preview_of([['Vorname', 'Rang'], ['Anna', 'unbekannt']]).rows
    with pytest.raises(ValueError, match='valid rows'):
        importer.import_members(rows, imported_by=None)


def test_template_csv_imports_cleanly(app):
    content = importer.template_csv()
    assert content.startswith('﻿Vorname;Nachname;Biername;E-Mail;Telefon;Rang;Eintritt\r\n')
    (row,) = importer.read_upload('vorlage.csv', content.encode()).rows
    assert row.is_valid and row.rank.key == 'fux' and row.joined_on == today_local()


# --- Routes ----------------------------------------------------------------------


def upload(client, data, filename='liste.csv'):
    return client.post(
        '/members/import', data={'file': (io.BytesIO(data), filename)}, content_type='multipart/form-data'
    )


def payload_of(response):
    return PAYLOAD_RE.search(response.get_data(as_text=True)).group(1)


def test_import_routes_need_login_and_permission(client, login):
    urls = [
        ('get', '/members/import'),
        ('post', '/members/import'),
        ('post', '/members/import/confirm'),
        ('get', '/members/import/vorlage.csv'),
    ]
    for method, url in urls:
        response = getattr(client, method)(url)
        assert response.status_code == 302 and '/login' in response.location, url
    make_user(email='bursch@example.org')  # a plain Bursch may not import
    login('bursch@example.org')
    for method, url in urls:
        assert getattr(client, method)(url).status_code == 403, url


def test_directory_links_to_import_for_importers_only(client, login):
    make_user(email='bursch@example.org')
    login('bursch@example.org')
    assert '/members/import' not in client.get('/members').get_data(as_text=True)
    client.post('/logout')
    login(make_officer('senior'))
    assert '/members/import' in client.get('/members').get_data(as_text=True)


def test_upload_page_and_template_download(client, login):
    login(make_admin())
    page = client.get('/members/import').get_data(as_text=True)
    assert 'enctype="multipart/form-data"' in page
    assert '/members/import/vorlage.csv' in page
    response = client.get('/members/import/vorlage.csv')
    assert response.status_code == 200
    assert response.headers['Content-Type'] == 'text/csv; charset=utf-8'
    assert 'attachment' in response.headers['Content-Disposition']
    assert response.data.startswith(b'\xef\xbb\xbfVorname;Nachname;')


def test_upload_shows_preview_with_errors(client, login):
    login(make_admin())
    rows = GOOD_ROWS + [['', 'Ohne', '', '', '', '', ''], ['Dora', '', '', 'anna@example.org', '', '', '']]
    response = upload(client, csv_bytes(rows + [['Emil', '', '', '', '', 'Papst', '', 'x']]))
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    page = response.get_data(as_text=True)
    assert 'Vorname fehlt.' in page
    assert 'E-Mail-Adresse steht mehrfach in der Datei (Zeilen 2, 6).' in page
    assert 'Unbekannter Rang „Papst“' in page
    assert '2 Mitglieder importieren' in page  # Bernd and Carla; Anna's address is repeated
    assert ' disabled>' not in page
    # Admins also hold members.invite: the email option is offered.
    assert 'name="send_invites"' in page
    assert member_count() == 1  # only the admin; the preview creates nothing


def test_preview_without_valid_rows_disables_the_button(client, login):
    login(make_admin())
    page = upload(client, csv_bytes([['Vorname', 'Rang'], ['Anna', 'Papst']])).get_data(as_text=True)
    assert '0 Mitglieder importieren' in page
    assert 'disabled>0 Mitglieder importieren' in page


def test_upload_errors_are_shown_on_the_form(client, login):
    login(make_admin())
    response = upload(client, b'Name\r\nAnna\r\n')
    assert response.status_code == 200
    assert 'Die Spalte „Vorname“ fehlt' in response.get_data(as_text=True)
    response = upload(client, b'Vorname\r\nAnna\r\n', filename='liste.pdf')
    assert 'Nur CSV- oder Excel-Dateien' in response.get_data(as_text=True)
    response = client.post('/members/import', data={}, content_type='multipart/form-data')
    assert 'Bitte eine Datei auswählen.' in response.get_data(as_text=True)


def test_upload_too_large_redirects_with_message(app, client, login):
    login(make_admin())
    data = b'Vorname\r\n' + b'x' * app.config['MAX_CONTENT_LENGTH']
    response = upload(client, data)
    assert response.status_code == 302
    assert response.location.endswith('/members/import')
    assert 'Die Datei ist zu groß (höchstens 2 MB).' in client.get(response.location).get_data(as_text=True)


def test_upload_too_large_with_csrf_protection_on(app, client, login):
    # In production the CSRF check reads the form first, so the 413 comes from there.
    login(make_admin())
    app.config['WTF_CSRF_ENABLED'] = True
    response = upload(client, b'x' * (app.config['MAX_CONTENT_LENGTH'] + 1))
    assert response.status_code == 302 and response.location.endswith('/members/import')


def test_too_large_post_to_a_post_only_url_goes_home(app, client, login):
    login(make_admin())
    response = client.post('/members/import/confirm', data={'payload': 'x' * app.config['MAX_CONTENT_LENGTH']})
    assert response.status_code == 302 and response.location.endswith('/')


def test_confirm_creates_members_and_redirects(client, login):
    admin = make_admin()
    login(admin)
    preview = upload(client, csv_bytes(GOOD_ROWS, sep=',', bom=True))
    response = client.post('/members/import/confirm', data={'payload': payload_of(preview)})
    assert response.status_code == 302 and response.location.endswith('/members')
    page = client.get(response.location).get_data(as_text=True)
    assert '3 Mitglieder importiert, 2 mit Rang.' in page
    assert 'Anna Alpha' in page
    assert current_rank_entry(members_named('Anna')[0]).rank.key == 'fux'
    assert db.session.scalar(select(func.count(Invitation.id))) == 0
    assert audit_entries('members.import')[-1].actor_user_id == admin.id


def test_confirm_xlsx_upload(client, login):
    login(make_admin())
    data = xlsx_bytes([['Vorname', 'Nachname', 'Eintritt', 'Rang'], ['Xaver', 'Excel', date(2023, 4, 1), 'Bursch']])
    preview = upload(client, data, filename='liste.xlsx')
    assert '1 Mitglied importieren' in preview.get_data(as_text=True)
    client.post('/members/import/confirm', data={'payload': payload_of(preview)})
    (xaver,) = members_named('Xaver')
    assert xaver.joined_on == date(2023, 4, 1)
    assert current_rank_entry(xaver).rank.key == 'bursch'


def test_confirm_with_invitations(app, client, login):
    login(make_officer('senior'))  # holds members.import and members.invite
    preview = upload(client, csv_bytes(GOOD_ROWS))
    response = client.post('/members/import/confirm', data={'payload': payload_of(preview), 'send_invites': 'y'})
    assert '2 Einladungen per E-Mail werden verschickt.' in client.get(response.location).get_data(as_text=True)
    invitations = db.session.scalars(select(Invitation)).all()
    assert sorted(i.email for i in invitations) == ['anna@example.org', 'carla@example.org']
    assert {i.channel for i in invitations} == {'email'}
    jobs = db.session.scalars(select(Job).where(Job.kind == 'mail.send')).all()
    assert sorted(job.payload['to'] for job in jobs) == ['anna@example.org', 'carla@example.org']
    assert all('http://localhost/join/' in job.payload['body'] for job in jobs)


def test_invitations_need_members_invite(client, login):
    user = make_user(email='importer@example.org', name='Nur Import', perms=[])
    give_permissions(user.member, 'members.import', 'members.directory.view')
    login(user)
    preview = upload(client, csv_bytes(GOOD_ROWS))
    assert 'name="send_invites"' not in preview.get_data(as_text=True)
    client.post('/members/import/confirm', data={'payload': payload_of(preview), 'send_invites': 'y'})
    assert len(members_named('Anna')) == 1
    assert db.session.scalar(select(func.count(Invitation.id))) == 0
    assert db.session.scalar(select(func.count(Job.id)).where(Job.kind == 'mail.send')) == 0


def test_confirm_rejects_tampered_payload(client, login):
    login(make_admin())
    token = payload_of(upload(client, csv_bytes(GOOD_ROWS)))
    before = member_count()
    for bad in (token[:-2] + ('xy' if token[-2:] != 'xy' else 'zz'), 'garbage', ''):
        response = client.post('/members/import/confirm', data={'payload': bad})
        assert response.status_code == 302 and response.location.endswith('/members/import')
        assert importer.PREVIEW_INVALID in client.get(response.location).get_data(as_text=True)
    assert member_count() == before


def test_confirm_rejects_expired_payload(client, login, monkeypatch):
    admin = make_admin()
    login(admin)
    token = expired_token(monkeypatch, preview_of(GOOD_ROWS).rows, admin)
    before = member_count()
    response = client.post('/members/import/confirm', data={'payload': token})
    assert response.status_code == 302
    assert importer.PREVIEW_EXPIRED in client.get(response.location).get_data(as_text=True)
    assert member_count() == before


def test_confirm_rejects_another_importers_payload(client, login):
    senior = make_officer('senior')
    login(senior)
    token = payload_of(upload(client, csv_bytes(GOOD_ROWS)))
    client.post('/logout')
    login(make_admin())
    before = member_count()
    response = client.post('/members/import/confirm', data={'payload': token})
    assert importer.PREVIEW_INVALID in client.get(response.location).get_data(as_text=True)
    assert member_count() == before


def test_confirm_checks_again_and_imports_nothing_after_a_change(client, login):
    login(make_admin())
    token = payload_of(upload(client, csv_bytes(GOOD_ROWS)))
    make_member('Schnell Er', email='carla@example.org')  # meanwhile, someone else took Carla's address
    before = member_count()
    response = client.post('/members/import/confirm', data={'payload': token})
    assert response.status_code == 409
    page = response.get_data(as_text=True)
    assert 'Es wurde nichts importiert' in page
    assert importer.EMAIL_TAKEN in page
    assert member_count() == before
    # The new preview offers the rows that are still valid, signed anew.
    assert '2 Mitglieder importieren' in page
    assert payload_of(response) != token
    client.post('/members/import/confirm', data={'payload': payload_of(response)})
    assert member_count() == before + 2
    assert len(members_named('Carla')) == 0


def test_confirm_rolls_back_everything_on_a_database_conflict(client, login, monkeypatch):
    login(make_admin())
    token = payload_of(upload(client, csv_bytes(GOOD_ROWS)))
    make_member('Schnell Er', email='carla@example.org')
    # Simulate the race the second check cannot see: the conflict appears only on insert.
    monkeypatch.setattr(importer, '_check_emails', lambda rows: None)
    before = member_count()
    response = client.post('/members/import/confirm', data={'payload': token})
    assert response.status_code == 302 and response.location.endswith('/members/import')
    assert 'Zeile 4: E-Mail-Adresse gehört schon zu einem Mitglied.' in client.get(response.location).get_data(
        as_text=True
    )
    assert member_count() == before
    assert members_named('Anna') == [] and audit_entries('members.create') == []


def test_confirm_without_rows(client, login):
    admin = make_admin()
    login(admin)
    with client.application.test_request_context():
        token = importer.sign([], user=admin)
    response = client.post('/members/import/confirm', data={'payload': token})
    assert response.status_code == 302
    assert 'Keine gültigen Zeilen' in client.get(response.location).get_data(as_text=True)


def test_seed_grants_import_to_senior_and_sysadmin_only(app):
    from community.core.authz.service import permissions_for

    holders = {
        'senior': make_officer('senior').member,
        'sysadmin': make_admin().member,
        'consenior': make_officer('consenior').member,
        'schriftwart': make_officer('schriftwart').member,
        'fuxmajor': make_officer('fuxmajor').member,
        'bursch': make_user(email='bursch@example.org').member,
    }
    allowed = {name for name, member in holders.items() if 'members.import' in permissions_for(member)}
    assert allowed == {'senior', 'sysadmin'}
