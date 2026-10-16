import logging
import smtplib

import pytest

from community.config import TestingConfig
from community.core.platform import mail
from community.core.platform.jobs import Job, JobStatus, run_due_jobs
from community.core.platform.mail import absolute_url, mail_backend, queue_mail, render_mail, send_mail
from community.extensions import db


def outbox(app):
    return app.extensions.setdefault('mail_outbox', [])


class FakeSMTP:
    """Records what the smtp backend does instead of talking to a server."""

    instances = []
    fail_with = None

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls = []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.calls.append('quit')
        return False

    def starttls(self, context=None):
        self.calls.append('starttls')

    def login(self, user, password):
        self.calls.append(('login', user, password))

    def send_message(self, message):
        if FakeSMTP.fail_with:
            raise FakeSMTP.fail_with
        self.calls.append(('send', message))


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def fake_smtp(app, monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.fail_with = None
    monkeypatch.setattr(smtplib, 'SMTP', FakeSMTP)
    monkeypatch.setattr(smtplib, 'SMTP_SSL', FakeSMTPSSL)
    app.config.update(
        MAIL_BACKEND='smtp',
        MAIL_SERVER='mail.example.org',
        MAIL_PORT=587,
        MAIL_USE_TLS=True,
        MAIL_USE_SSL=False,
        MAIL_USERNAME='club',
        MAIL_PASSWORD='app password',
    )
    return FakeSMTP


def test_backend_choice():
    assert mail_backend({'MAIL_BACKEND': 'memory', 'MAIL_SERVER': 'x'}) == 'memory'
    assert mail_backend({'MAIL_BACKEND': '', 'MAIL_SERVER': 'mail.example.org'}) == 'smtp'
    assert mail_backend({'MAIL_BACKEND': '', 'MAIL_SERVER': ''}) == 'console'
    assert TestingConfig.MAIL_BACKEND == 'memory'


@pytest.mark.parametrize(
    ('settings', 'message'),
    [
        ({'MAIL_BACKEND': 'pigeon'}, 'MAIL_BACKEND must be'),
        ({'MAIL_BACKEND': 'smtp', 'MAIL_SERVER': ''}, 'needs MAIL_SERVER'),
        ({'MAIL_USE_SSL': True, 'MAIL_USE_TLS': True}, 'not both'),
        ({'JOB_MAX_ATTEMPTS': 0}, 'JOB_MAX_ATTEMPTS'),
    ],
)
def test_config_rejects_bad_mail_settings(settings, message):
    config = type('Config', (TestingConfig,), settings)
    with pytest.raises(RuntimeError, match=message):
        config.validate()


def test_memory_backend(app):
    send_mail('max@example.org', 'Hallo', 'Text\n')
    assert outbox(app) == [
        {'from': 'Community <noreply@localhost>', 'to': 'max@example.org', 'subject': 'Hallo', 'body': 'Text\n'}
    ]


def test_console_backend_logs_instead_of_sending(app, caplog):
    app.config['MAIL_BACKEND'] = 'console'
    with caplog.at_level(logging.WARNING):
        send_mail('max@example.org', 'Betreff', 'Inhalt der Mail')
    assert 'max@example.org' in caplog.text
    assert 'Betreff' in caplog.text and 'Inhalt der Mail' in caplog.text
    assert outbox(app) == []


def test_smtp_backend_uses_starttls_and_login(app, fake_smtp):
    send_mail('max@example.org', 'Hallo', 'Text')
    (smtp,) = fake_smtp.instances
    assert type(smtp) is FakeSMTP
    assert (smtp.host, smtp.port, smtp.timeout) == ('mail.example.org', 587, mail.SMTP_TIMEOUT_SECONDS)
    assert smtp.calls[0] == 'starttls'
    assert smtp.calls[1] == ('login', 'club', 'app password')
    message = smtp.calls[2][1]
    assert (message['To'], message['From'], message['Subject']) == (
        'max@example.org',
        'Community <noreply@localhost>',
        'Hallo',
    )
    assert message['Message-ID'] and message['Date']
    assert smtp.calls[-1] == 'quit'


def test_smtp_backend_with_ssl_and_without_login(app, fake_smtp):
    app.config.update(MAIL_USE_SSL=True, MAIL_USE_TLS=False, MAIL_PORT=465, MAIL_USERNAME='')
    send_mail('max@example.org', 'Hallo', 'Text')
    (smtp,) = fake_smtp.instances
    assert type(smtp) is FakeSMTPSSL
    assert smtp.context is not None
    assert [call if isinstance(call, str) else call[0] for call in smtp.calls] == ['send', 'quit']


def test_a_sent_mail_keeps_only_recipient_and_subject(app):
    """Mail bodies hold invitation, reset and confirmation links; the job forgets them once sent (finding 9)."""
    job = queue_mail('max@example.org', 'notification', name='Max', title='Neue Aufgabe')
    db.session.commit()
    assert 'Hallo Max,' in job.payload['body']
    assert run_due_jobs()['done'] == 1
    assert 'Hallo Max,' in outbox(app)[0]['body']
    job = db.session.get(Job, job.id)
    assert job.payload == {'to': 'max@example.org', 'subject': 'Neue Aufgabe'}
    assert job.summary == 'max@example.org: Neue Aufgabe'


def test_a_failed_mail_keeps_its_body_for_a_retry(app, fake_smtp):
    app.config['JOB_MAX_ATTEMPTS'] = 1
    fake_smtp.fail_with = smtplib.SMTPServerDisconnected('gone')
    job = queue_mail('max@example.org', 'notification', name='Max', title='Hallo')
    db.session.commit()
    assert run_due_jobs()['failed'] == 1
    db.session.refresh(job)
    assert 'Hallo Max,' in job.payload['body']


def test_smtp_errors_make_the_job_retry(app, fake_smtp):
    fake_smtp.fail_with = smtplib.SMTPServerDisconnected('gone')
    job = queue_mail('max@example.org', 'notification', name='Max', title='Hallo')
    db.session.commit()
    assert run_due_jobs()['retried'] == 1
    db.session.refresh(job)
    assert job.status == JobStatus.PENDING
    assert 'SMTPServerDisconnected' in job.last_error


def test_headers_cannot_be_injected(app):
    with pytest.raises(ValueError, match='recipient'):
        send_mail('max@example.org\nBcc: evil@example.org', 'Hallo', 'x')
    with pytest.raises(ValueError, match='recipient'):
        send_mail('not an address', 'Hallo', 'x')
    send_mail('max@example.org', 'Zeile eins\nBcc: evil@example.org', 'x')
    assert outbox(app)[0]['subject'] == 'Zeile eins Bcc: evil@example.org'


def test_render_mail_uses_the_layout(app):
    app.config['CLUB_NAME'] = 'VDSt Test'
    subject, body = render_mail('notification', name='Max', title='Du bist ab 01.10.2026 Bursch.', link_url=None)
    assert subject == 'Du bist ab 01.10.2026 Bursch.'
    assert body.startswith('Hallo Max,\n\nDu bist ab 01.10.2026 Bursch.\n\nViele Grüße\nVDSt Test\n')
    assert 'Mehr dazu' not in body
    # Plain text: names with special characters are not HTML-escaped.
    _, body = render_mail('notification', name='Jörg & Co', title='<Test>', link_url='https://x.example/a')
    assert 'Hallo Jörg & Co,' in body and '<Test>' in body
    assert 'Mehr dazu: https://x.example/a' in body


def test_render_mail_needs_a_subject_block(app):
    with pytest.raises(ValueError, match='subject'):
        render_mail('base')


def test_queue_mail_renders_now_and_sends_on_tick(app):
    job = queue_mail('max@example.org', 'notification', name='Max', title='Neue Aufgabe')
    db.session.commit()
    assert job.kind == 'mail.send'
    assert job.payload['subject'] == 'Neue Aufgabe'
    assert outbox(app) == []  # nothing is sent inside the request
    assert run_due_jobs()['done'] == 1
    (sent,) = outbox(app)
    assert sent['to'] == 'max@example.org'
    assert sent['subject'] == 'Neue Aufgabe'
    assert 'Hallo Max,' in sent['body']
    assert db.session.get(Job, job.id).status == JobStatus.DONE


def test_absolute_url(app):
    with app.test_request_context('/'):
        assert absolute_url('notifications.index') == 'http://localhost/notifications'
        # BASE_URL wins over the request's host (which a client could set).
        app.config['BASE_URL'] = 'https://club.example/app'
        assert absolute_url('org.office', key='senior') == 'https://club.example/app/org/offices/senior'
    # Without a request (jobs, CLI) only BASE_URL works.
    assert absolute_url('notifications.index') == 'https://club.example/app/notifications'
    app.config['BASE_URL'] = ''
    with pytest.raises(RuntimeError):
        absolute_url('notifications.index')


def test_mail_test_command(app):
    app.config['CLUB_NAME'] = 'Community'
    result = app.test_cli_runner().invoke(mail.mail_cli, ['test', 'max@example.org'])
    assert result.exit_code == 0, result.output
    assert 'via memory' in result.output
    assert outbox(app)[0]['subject'] == 'Testmail von Community'
    result = app.test_cli_runner().invoke(mail.mail_cli, ['test', 'kaputt'])
    assert result.exit_code != 0
