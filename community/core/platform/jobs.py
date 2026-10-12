"""Background jobs: one `job` table, run by `flask jobs tick` from cron or a systemd timer (D16).

enqueue() adds a job to the current session, so it is committed (or rolled back) together
with the change that caused it. Each tick claims due jobs one by one with an atomic UPDATE,
so overlapping ticks never run the same job twice, and runs each handler in its own
transaction. Failed jobs are retried with exponential backoff (2, 4, 8, ... minutes).
Delivery is at least once: a tick killed in the middle of a job runs it again later.
Daily housekeeping (e.g. the audit log's IP retention) is queued by the tick itself, once per day.
"""

import json
from dataclasses import dataclass
from datetime import timedelta

import click
from flask import current_app
from flask.cli import with_appcontext
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from community.core.db import UTCDateTime
from community.core.platform import audit
from community.core.time import today_local, utcnow
from community.extensions import db

# A claimed job counts as abandoned (tick killed) when it is still running after this.
LOCK_DURATION = timedelta(minutes=5)
MAX_ERROR_LENGTH = 2000


class JobStatus:
    PENDING = 'pending'
    RUNNING = 'running'
    DONE = 'done'
    FAILED = 'failed'
    ALL = (PENDING, RUNNING, DONE, FAILED)
    LABELS = {PENDING: 'wartet', RUNNING: 'läuft', DONE: 'erledigt', FAILED: 'fehlgeschlagen'}


class JobError(Exception):
    """A rule was broken; the message is shown to the user (German)."""


class Job(db.Model):
    __table_args__ = (db.Index('ix_job_status_run_at', 'status', 'run_at'),)

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(80), nullable=False)
    payload_json = db.Column(db.Text, nullable=False, default='{}')
    run_at = db.Column(UTCDateTime, nullable=False, default=utcnow)
    status = db.Column(db.String(10), nullable=False, default=JobStatus.PENDING)
    attempts = db.Column(db.Integer, nullable=False, default=0)  # runs started, including the current one
    last_error = db.Column(db.Text)
    locked_until = db.Column(UTCDateTime)
    dedupe_key = db.Column(db.String(200), unique=True)  # e.g. "event_reminder:<event>:<member>:<time>"
    created_at = db.Column(UTCDateTime, nullable=False, default=utcnow)
    finished_at = db.Column(UTCDateTime)

    @property
    def payload(self):
        return json.loads(self.payload_json or '{}')

    @property
    def summary(self):
        """Short description for the admin page; never the full payload (mails may contain reset links)."""
        handler = HANDLERS.get(self.kind)
        if handler is None or handler.summary is None:
            return ''
        try:
            return handler.summary(self.payload)
        except Exception:  # a broken payload must not break the admin page
            return ''

    def __repr__(self):
        return f'<Job {self.id} {self.kind} {self.status}>'


@dataclass(frozen=True)
class Handler:
    kind: str
    fn: object
    summary: object = None


HANDLERS = {}


def job_handler(kind, *, summary=None):
    """Register the function that runs jobs of `kind`. It is called with the payload as keyword
    arguments and must not commit: the tick commits its changes together with the "done" mark,
    or rolls them back when it raises. `summary(payload)` optionally returns a short text for
    the admin page."""

    def decorator(fn):
        existing = HANDLERS.get(kind)
        if existing is not None and existing.fn is not fn:
            raise ValueError(f'Job kind {kind!r} has two handlers')
        HANDLERS[kind] = Handler(kind, fn, summary)
        return fn

    return decorator


def enqueue(kind, payload=None, *, run_at=None, dedupe_key=None):
    """Add a job to the session (commits nothing) and return it.

    With a dedupe_key that is already in use, nothing is added and the existing job is
    returned, whatever its status: a key stands for work that happens once (a reminder
    that was sent is not sent again). The unique constraint backs this up; a concurrent
    duplicate makes the second transaction fail instead of running the work twice.
    """
    if dedupe_key is not None:
        existing = db.session.scalar(select(Job).where(Job.dedupe_key == dedupe_key))
        if existing is not None:
            return existing
    job = Job(
        kind=kind,
        payload_json=json.dumps(payload or {}, ensure_ascii=False, sort_keys=True),
        run_at=run_at or utcnow(),
        status=JobStatus.PENDING,
        attempts=0,
        dedupe_key=dedupe_key,
    )
    db.session.add(job)
    return job


# Kinds that `flask jobs tick` queues once per day (housekeeping), see daily_job().
DAILY_KINDS = []


def daily_job(kind, *, summary=None):
    """Register a job handler (like job_handler) that every tick queues once per day."""

    def decorator(fn):
        job_handler(kind, summary=summary)(fn)
        if kind not in DAILY_KINDS:
            DAILY_KINDS.append(kind)
        return fn

    return decorator


def queue_daily_jobs(day=None):
    """Queue each daily job for `day` (default today, Europe/Berlin) unless that is done already. Commits.

    The dedupe_key "<kind>:<date>" makes it once per day, however often the tick runs.
    """
    day = day or today_local()
    for kind in DAILY_KINDS:
        enqueue(kind, dedupe_key=f'{kind}:{day.isoformat()}')
    try:
        db.session.commit()
    except IntegrityError:  # an overlapping tick queued them at the same moment
        db.session.rollback()


@daily_job('audit.purge_ips')
def _purge_audit_ips():
    """Retention (M1-2): IP addresses older than AUDIT_IP_RETENTION_DAYS leave the audit log."""
    audit.purge_old_ips()


def _due_job_ids(now, limit):
    return db.session.scalars(
        select(Job.id)
        .where(
            Job.status == JobStatus.PENDING,
            Job.run_at <= now,
            or_(Job.locked_until.is_(None), Job.locked_until < now),
        )
        .order_by(Job.run_at, Job.id)
        .limit(limit)
    ).all()


def claim(job_id):
    """Mark a pending job as running. False if another tick got it first. Commits."""
    now = utcnow()
    result = db.session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.status == JobStatus.PENDING,
            or_(Job.locked_until.is_(None), Job.locked_until < now),
        )
        .values(status=JobStatus.RUNNING, locked_until=now + LOCK_DURATION, attempts=Job.attempts + 1)
        .execution_options(synchronize_session=False)
    )
    db.session.commit()
    return result.rowcount == 1


def _max_attempts():
    return current_app.config['JOB_MAX_ATTEMPTS']


def _record_failure(job, error, now):
    """Schedule the next try with backoff, or give up after JOB_MAX_ATTEMPTS. Returns the outcome."""
    job.last_error = error[:MAX_ERROR_LENGTH]
    job.locked_until = None
    if job.attempts >= _max_attempts():
        job.status = JobStatus.FAILED
        job.finished_at = now
        return 'failed'
    job.status = JobStatus.PENDING
    job.run_at = now + timedelta(minutes=2**job.attempts)
    return 'retried'


def release_abandoned():
    """Jobs still running after their lock expired belong to a tick that died: retry or fail them."""
    now = utcnow()
    jobs = db.session.scalars(
        select(Job).where(Job.status == JobStatus.RUNNING, Job.locked_until < now).order_by(Job.id)
    ).all()
    for job in jobs:
        _record_failure(
            job, 'Abgebrochen: der Lauf wurde nicht beendet (Prozess beendet oder Zeitüberschreitung).', now
        )
    db.session.commit()
    return len(jobs)


def _run_claimed(job_id):
    job = db.session.get(Job, job_id)
    handler = HANDLERS.get(job.kind)
    if handler is None:
        job.status = JobStatus.FAILED
        job.last_error = f'Unbekannte Job-Art {job.kind!r}'
        job.locked_until = None
        job.finished_at = utcnow()
        db.session.commit()
        return 'failed'
    try:
        handler.fn(**job.payload)
        job.status = JobStatus.DONE
        job.locked_until = None
        job.finished_at = utcnow()
        # The handler's own changes and the "done" mark are committed together.
        db.session.commit()
        return 'done'
    except Exception as error:  # every failure is recorded on the job
        db.session.rollback()
        current_app.logger.exception('Job %s (%s) failed', job_id, handler.kind)
        job = db.session.get(Job, job_id)
        outcome = _record_failure(job, f'{type(error).__name__}: {error}', utcnow())
        db.session.commit()
        return outcome


def run_due_jobs(limit=50):
    """Run up to `limit` due jobs. Returns counts: done, retried, failed, skipped (taken by another tick)."""
    counts = {'done': 0, 'retried': 0, 'failed': 0, 'skipped': 0}
    ids = _due_job_ids(utcnow(), limit)
    db.session.commit()  # end the read transaction; each job gets its own
    for job_id in ids:
        if not claim(job_id):
            counts['skipped'] += 1
            continue
        counts[_run_claimed(job_id)] += 1
    return counts


def retry_job(job, *, actor=None):
    """Put a failed job back in the queue with a fresh set of attempts. Commits nothing."""
    if job.status != JobStatus.FAILED:
        raise JobError('Nur fehlgeschlagene Jobs können wiederholt werden.')
    job.status = JobStatus.PENDING
    job.attempts = 0
    job.run_at = utcnow()
    job.locked_until = None
    job.finished_at = None
    audit.record('jobs.retry', job, actor=actor, diff={'kind': job.kind})
    return job


def recent_jobs(status=None, limit=100):
    query = select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(limit)
    if status:
        query = query.where(Job.status == status)
    return db.session.scalars(query).all()


@click.group('jobs')
def jobs_cli():
    """Background jobs (mail, reminders). Run `flask jobs tick` every minute."""


@jobs_cli.command('tick')
@click.option('--limit', default=50, show_default=True, help='Maximum number of jobs to run')
@with_appcontext
def tick(limit):
    """Run due jobs (from cron or a systemd timer, every minute); once a day also the housekeeping."""
    queue_daily_jobs()
    released = release_abandoned()
    counts = run_due_jobs(limit)
    click.echo(
        f'Jobs: {counts["done"]} done, {counts["retried"]} to retry, {counts["failed"]} failed, '
        f'{counts["skipped"]} skipped, {released} abandoned released.'
    )


@jobs_cli.command('list')
@click.option('--status', type=click.Choice(JobStatus.ALL))
@click.option('--limit', default=50, show_default=True)
@with_appcontext
def list_jobs(status, limit):
    """Show the newest jobs."""
    from community.core.time import format_datetime

    for job in recent_jobs(status, limit):
        error = (job.last_error or '').splitlines()[0][:100] if job.last_error else ''
        click.echo(
            f'{job.id:>6}  {job.status:<8} {job.kind:<20} attempts={job.attempts} '
            f'run_at={format_datetime(job.run_at)}  {job.summary}  {error}'.rstrip()
        )


@jobs_cli.command('retry')
@click.argument('job_id', type=int)
@with_appcontext
def retry(job_id):
    """Queue a failed job again."""
    job = db.session.get(Job, job_id)
    if job is None:
        raise click.ClickException(f'No job {job_id}')
    try:
        retry_job(job)
    except JobError as error:
        raise click.ClickException(str(error)) from None
    db.session.commit()
    click.echo(f'Job {job_id} queued again.')
