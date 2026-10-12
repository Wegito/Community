from datetime import timedelta

import pytest
from sqlalchemy import func, select

from community.core.identity.models import Member
from community.core.platform import jobs
from community.core.platform.audit import AuditLog
from community.core.platform.jobs import (
    Job,
    JobStatus,
    claim,
    enqueue,
    job_handler,
    jobs_cli,
    release_abandoned,
    retry_job,
    run_due_jobs,
)
from community.core.time import utcnow
from community.extensions import db

from .factories import make_admin, make_user

CALLS = []


@job_handler('test.ok')
def _ok(value=None):
    CALLS.append(value)


@job_handler('test.fail')
def _fail(**_):
    raise RuntimeError('kaputt')


@job_handler('test.writes_then_fails')
def _writes_then_fails():
    db.session.add(Member(first_name='Ghost', last_name='Writer'))
    db.session.flush()
    raise RuntimeError('after writing')


@pytest.fixture(autouse=True)
def _reset_calls():
    CALLS.clear()


def job_count():
    return db.session.scalar(select(func.count()).select_from(Job))


def test_enqueue_adds_without_committing(app):
    job = enqueue('test.ok', {'value': 1})
    assert job.status == JobStatus.PENDING
    db.session.rollback()
    assert job_count() == 0


def test_enqueue_rejects_payloads_that_are_not_json(app):
    with pytest.raises(TypeError):
        enqueue('test.ok', {'value': object()})


def test_dedupe_key_returns_the_existing_job(app):
    first = enqueue('test.ok', {'value': 1}, dedupe_key='reminder:1')
    # Same transaction, not flushed yet: still found.
    assert enqueue('test.ok', {'value': 2}, dedupe_key='reminder:1') is first
    db.session.commit()
    assert enqueue('test.ok', {'value': 3}, dedupe_key='reminder:1').id == first.id
    # Work that is done is not repeated either.
    run_due_jobs()
    again = enqueue('test.ok', {'value': 4}, dedupe_key='reminder:1')
    db.session.commit()
    assert again.id == first.id and again.status == JobStatus.DONE
    assert job_count() == 1
    assert CALLS == [1]
    # Without a key every call is a new job.
    enqueue('test.ok')
    enqueue('test.ok')
    db.session.commit()
    assert job_count() == 3


def test_tick_runs_due_jobs(app):
    job = enqueue('test.ok', {'value': 'a'})
    later = enqueue('test.ok', {'value': 'later'}, run_at=utcnow() + timedelta(hours=1))
    db.session.commit()
    assert run_due_jobs() == {'done': 1, 'retried': 0, 'failed': 0, 'skipped': 0}
    assert CALLS == ['a']
    db.session.refresh(job)
    assert (job.status, job.attempts, job.locked_until) == (JobStatus.DONE, 1, None)
    assert job.finished_at is not None
    assert db.session.get(Job, later.id).status == JobStatus.PENDING
    # Nothing left to do.
    assert run_due_jobs()['done'] == 0


def test_failing_job_is_retried_with_backoff(app):
    job = enqueue('test.fail')
    db.session.commit()
    before = utcnow()
    assert run_due_jobs()['retried'] == 1
    after = utcnow()
    db.session.refresh(job)
    assert (job.status, job.attempts, job.locked_until) == (JobStatus.PENDING, 1, None)
    assert job.last_error == 'RuntimeError: kaputt'
    assert before + timedelta(minutes=2) <= job.run_at <= after + timedelta(minutes=2)
    # Not due yet.
    assert run_due_jobs()['retried'] == 0

    job.run_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    before = utcnow()
    run_due_jobs()
    db.session.refresh(job)
    assert job.attempts == 2
    assert job.run_at >= before + timedelta(minutes=4)


def test_job_fails_for_good_after_max_attempts(app):
    app.config['JOB_MAX_ATTEMPTS'] = 2
    job = enqueue('test.fail')
    db.session.commit()
    run_due_jobs()
    job.run_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert run_due_jobs() == {'done': 0, 'retried': 0, 'failed': 1, 'skipped': 0}
    db.session.refresh(job)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 2)
    assert job.finished_at is not None
    job.run_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert run_due_jobs()['failed'] == 0  # failed jobs are not picked up again


def test_unknown_kind_fails_without_retry(app):
    job = enqueue('test.no_such_handler')
    db.session.commit()
    assert run_due_jobs()['failed'] == 1
    db.session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert 'test.no_such_handler' in job.last_error


def test_failed_handler_changes_are_rolled_back(app):
    enqueue('test.writes_then_fails')
    db.session.commit()
    run_due_jobs()
    assert db.session.scalar(select(Member).where(Member.first_name == 'Ghost')) is None


def test_claim_is_atomic(app):
    job = enqueue('test.ok')
    db.session.commit()
    assert claim(job.id) is True
    assert claim(job.id) is False
    db.session.refresh(job)
    assert job.status == JobStatus.RUNNING
    assert job.locked_until > utcnow()


def test_overlapping_ticks_run_a_job_once(app, monkeypatch):
    """A second tick that listed the job before the first one claimed it must skip it."""
    enqueue('test.ok', {'value': 'once'})
    db.session.commit()
    seen_by_second_tick = jobs._due_job_ids(utcnow(), 50)
    assert run_due_jobs()['done'] == 1
    monkeypatch.setattr(jobs, '_due_job_ids', lambda now, limit: seen_by_second_tick)
    assert run_due_jobs() == {'done': 0, 'retried': 0, 'failed': 0, 'skipped': 1}
    assert CALLS == ['once']


def test_abandoned_running_job_is_released(app):
    job = enqueue('test.ok', {'value': 'again'})
    db.session.commit()
    claim(job.id)  # a tick claimed it and then died
    assert release_abandoned() == 0  # the lock is still valid
    job.locked_until = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert release_abandoned() == 1
    db.session.refresh(job)
    assert (job.status, job.attempts) == (JobStatus.PENDING, 1)
    assert 'Abgebrochen' in job.last_error
    job.run_at = utcnow() - timedelta(seconds=1)
    db.session.commit()
    assert run_due_jobs()['done'] == 1
    assert CALLS == ['again']


def test_retry_job_only_for_failed_jobs(app):
    job = enqueue('test.fail')
    db.session.commit()
    with pytest.raises(jobs.JobError):
        retry_job(job)
    job.status, job.attempts = JobStatus.FAILED, 5
    db.session.commit()
    retry_job(job)
    db.session.commit()
    assert (job.status, job.attempts, job.finished_at) == (JobStatus.PENDING, 0, None)
    assert db.session.scalar(select(AuditLog).where(AuditLog.action == 'jobs.retry')).entity_id == str(job.id)


def test_cli_tick_list_and_retry(app):
    runner = app.test_cli_runner()
    ok = enqueue('test.ok', {'value': 'cli'})
    bad = enqueue('test.no_such_handler')
    db.session.commit()

    result = runner.invoke(jobs_cli, ['tick'])
    assert result.exit_code == 0, result.output
    # test.ok and the daily audit.purge_ips are done.
    assert '2 done' in result.output and '1 failed' in result.output

    result = runner.invoke(jobs_cli, ['list', '--status', 'failed'])
    assert 'test.no_such_handler' in result.output
    assert 'test.ok' not in result.output
    assert 'test.ok' in runner.invoke(jobs_cli, ['list']).output

    result = runner.invoke(jobs_cli, ['retry', str(ok.id)])
    assert result.exit_code != 0
    assert 'fehlgeschlagene' in result.output
    assert runner.invoke(jobs_cli, ['retry', '999']).exit_code != 0
    result = runner.invoke(jobs_cli, ['retry', str(bad.id)])
    assert result.exit_code == 0, result.output
    assert db.session.get(Job, bad.id).status == JobStatus.PENDING


def test_admin_jobs_page_lists_jobs_and_retries(client, login):
    admin = make_admin()
    job = enqueue('mail.send', {'to': 'x@example.org', 'subject': 'Passwort', 'body': 'Link: GEHEIM-TOKEN'})
    job.status, job.attempts, job.last_error = JobStatus.FAILED, 5, 'SMTPAuthenticationError: denied'
    db.session.commit()
    login(admin)
    page = client.get('/admin/jobs').get_data(as_text=True)
    assert 'x@example.org: Passwort' in page
    assert 'SMTPAuthenticationError' in page
    assert 'Wiederholen' in page
    # Mail bodies can contain password reset links: never shown.
    assert 'GEHEIM-TOKEN' not in page
    assert 'x@example.org' not in client.get('/admin/jobs?status=done').get_data(as_text=True)

    response = client.post(f'/admin/jobs/{job.id}/retry')
    assert response.status_code == 302
    db.session.refresh(job)
    assert job.status == JobStatus.PENDING
    # A second retry is refused with a message, not an error page.
    response = client.post(f'/admin/jobs/{job.id}/retry', follow_redirects=True)
    assert 'Nur fehlgeschlagene' in response.get_data(as_text=True)


def test_jobs_page_needs_system_admin(client, login):
    job = enqueue('test.fail')
    job.status = JobStatus.FAILED
    db.session.commit()
    user = make_user(perms=['audit.view'])
    login(user)
    assert client.get('/admin/jobs').status_code == 403
    assert client.post(f'/admin/jobs/{job.id}/retry').status_code == 403
    assert db.session.get(Job, job.id).status == JobStatus.FAILED
    assert client.post(f'/admin/jobs/{job.id + 1}/retry').status_code == 403
