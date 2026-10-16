"""Sending email.

Requests never talk to the mail server: queue_mail() renders the mail and enqueues a
'mail.send' job, and `flask jobs tick` sends it (roadmap §4.3, D16). send_mail() delivers
through the backend chosen by MAIL_BACKEND:
  console  log the mail instead of sending it (development; production without MAIL_SERVER)
  memory   append it to app.extensions['mail_outbox'] (tests)
  smtp     send it through MAIL_SERVER
"""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from urllib.parse import urlsplit

import click
from flask import current_app, has_request_context, request, url_for
from flask.cli import with_appcontext

from community.core.club import club_name

from .jobs import enqueue, job_handler

BACKENDS = ('console', 'memory', 'smtp')
SMTP_TIMEOUT_SECONDS = 30


def mail_backend(config):
    """The backend to use; an empty MAIL_BACKEND means smtp when MAIL_SERVER is set, else console."""
    return config.get('MAIL_BACKEND') or ('smtp' if config.get('MAIL_SERVER') else 'console')


def _single_line(value):
    """Header values must not contain line breaks (header injection)."""
    return ' '.join(str(value).split())


def build_message(to, subject, body):
    if '\n' in to or '\r' in to or '@' not in parseaddr(to)[1]:
        raise ValueError(f'Invalid recipient {to!r}')
    sender = current_app.config['MAIL_FROM']
    message = EmailMessage()
    message['From'] = sender
    message['To'] = to
    message['Subject'] = _single_line(subject)
    message['Date'] = formatdate(localtime=True)
    message['Message-ID'] = make_msgid(domain=parseaddr(sender)[1].rpartition('@')[2] or None)
    message.set_content(body)
    return message


def _send_console(message):
    current_app.logger.warning(
        'MAIL_BACKEND=console, mail not sent:\nTo: %s\nSubject: %s\n\n%s',
        message['To'],
        message['Subject'],
        message.get_content(),
    )


def _send_memory(message):
    current_app.extensions.setdefault('mail_outbox', []).append(
        {
            'from': message['From'],
            'to': message['To'],
            'subject': message['Subject'],
            'body': message.get_content(),
        }
    )


def _send_smtp(message):
    config = current_app.config
    context = ssl.create_default_context()
    if config['MAIL_USE_SSL']:
        connection = smtplib.SMTP_SSL(
            config['MAIL_SERVER'], config['MAIL_PORT'], timeout=SMTP_TIMEOUT_SECONDS, context=context
        )
    else:
        connection = smtplib.SMTP(config['MAIL_SERVER'], config['MAIL_PORT'], timeout=SMTP_TIMEOUT_SECONDS)
    with connection as smtp:
        if config['MAIL_USE_TLS'] and not config['MAIL_USE_SSL']:
            smtp.starttls(context=context)
        if config['MAIL_USERNAME']:
            smtp.login(config['MAIL_USERNAME'], config['MAIL_PASSWORD'])
        smtp.send_message(message)


_SENDERS = {'console': _send_console, 'memory': _send_memory, 'smtp': _send_smtp}


def send_mail(to, subject, body):
    """Send one plain-text mail now. Use queue_mail() in requests; this is for jobs and the CLI."""
    message = build_message(to, subject, body)
    _SENDERS[mail_backend(current_app.config)](message)


# The body may hold an invitation, reset or confirmation link: the job keeps only `to` and `subject`.
@job_handler('mail.send', summary=lambda payload: f'{payload["to"]}: {payload["subject"]}', secret=('body',))
def _send_job(to, subject, body):
    send_mail(to, subject, body)


def render_mail(template, **context):
    """Render templates/mail/<template>.txt. Returns (subject, body).

    The template extends mail/base.txt (greeting and footer) and defines the blocks
    `subject` and `body`. The subject is the `subject` block alone; the body is the whole
    rendered template. Mails are plain text, so nothing is HTML-escaped.
    """
    tpl = current_app.jinja_env.get_template(f'mail/{template}.txt')
    if 'subject' not in tpl.blocks:
        raise ValueError(f'mail/{template}.txt has no subject block')
    variables = {'club_name': club_name(), **context}
    subject = _single_line(''.join(tpl.blocks['subject'](tpl.new_context(variables))))
    body = tpl.render(variables).strip() + '\n'
    return subject, body


def queue_mail(to, template, *, dedupe_key=None, **context):
    """Render a mail now and enqueue it for sending (commits nothing). Returns the job.

    With a dedupe_key that was used before, no second mail is queued (see jobs.enqueue).
    """
    subject, body = render_mail(template, **context)
    return enqueue('mail.send', {'to': to, 'subject': subject, 'body': body}, dedupe_key=dedupe_key)


def absolute_url(endpoint, **values):
    """Full URL for links in mails. Uses BASE_URL when set (works without a request, e.g. in
    jobs); otherwise the current request's host, which needs a request context."""
    base = current_app.config.get('BASE_URL')
    if not base:
        return url_for(endpoint, _external=True, **values)
    parts = urlsplit(base)
    adapter = current_app.url_map.bind(parts.netloc, script_name=parts.path or '/', url_scheme=parts.scheme or 'https')
    return adapter.build(endpoint, values, force_external=True)


def absolute_link(path):
    """Full URL for an app path like "/org/offices/senior", or None if no base is known."""
    if not path:
        return None
    base = current_app.config.get('BASE_URL') or (request.url_root.rstrip('/') if has_request_context() else '')
    return f'{base}{path}' if base else None


@click.group('mail')
def mail_cli():
    """Email settings check."""


@mail_cli.command('test')
@click.argument('to')
@with_appcontext
def send_test(to):
    """Send a test mail right away (not through the job queue) to check the MAIL_* settings."""
    club = club_name()
    backend = mail_backend(current_app.config)
    try:
        send_mail(to, f'Testmail von {club}', f'Diese Testmail zeigt, dass der Mailversand von {club} funktioniert.\n')
    except (OSError, smtplib.SMTPException, ValueError) as error:
        raise click.ClickException(f'Sending failed ({backend}): {error}') from None
    click.echo(f'Sent to {to} via {backend}.')
