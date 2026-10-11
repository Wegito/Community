# Decisions log

Deviations from [`ROADMAP.md`](ROADMAP.md) and other decisions worth remembering. Newest first.

## 2026-10-11 — M1

14. **M1-6: a job's `dedupe_key` stands for work that happens once.** `enqueue()` returns the existing job whatever its status, also when it is done or failed, so a reminder that was sent is never sent again. A failed job runs again only through `flask jobs retry` or the retry button on `/admin/jobs`, which also resets its attempts. Concurrent duplicates are stopped by the unique constraint (the second transaction fails); there is no savepoint retry, because SAVEPOINT handling in pysqlite is unreliable.
15. **M1-6: `attempts` counts runs started, not only failures.** The claim increments it. A tick that dies mid-job leaves the job `running`; the next tick releases it after its 5-minute lock as a failed attempt. Jobs therefore run at least once, and a crash loop still ends in `failed`.
16. **M1-6: mails are rendered when queued.** Each `templates/mail/<name>.txt` extends `mail/base.txt` (greeting, signature, footer with `CLUB_NAME`) and defines `subject` and `body` blocks. The subject is the `subject` block rendered alone; the body is the whole template. The job only sends text, so it needs no request. Links use `BASE_URL` and fall back to the request's host; without either (CLI), `notify()` sends the mail without links.
17. **M1-6: `/admin/jobs` never shows payloads,** only a short summary per job kind (for mails: recipient and subject). Mail bodies will contain password-reset and invitation links, and the technical admin must not be able to take over other accounts such as the Kassenwart's (roadmap §6.3, rule 6).
18. **M1-6: `office_assigned` and `rank_changed` are sent from the org routes, not the services.** Bootstrap through `flask org set-rank` and the test factories stays silent and needs no request or `BASE_URL`.
19. **M1-6: notification pages are part of core** (`community/core/platform/routes.py`, blueprint `notifications`), because every module sends notifications. The settings page is `/settings/notifications` until the profile shell (M2-1) has a settings area.
20. **M1-6: what is audited.** Retrying a job and changing notification settings are audited (only the changed entries). Running jobs and reading notifications are not: the `job` table and `read_at` already record them.
21. **M1-6: smaller additions and gaps.** `flask mail test <address>` sends one mail right away to check the `MAIL_*` settings. The console backend logs at WARNING level, so mails that were not sent show up in production logs too. Quiet hours (§7.4) are not built yet.

## 2026-10-11 — M0 implementation (Wegito/Community#1)

1. **One PR for M0, one commit per task.** The roadmap asks for one PR per task. M0 was delivered in the PR that added the roadmap. Each commit starts with its task ID, so the history still maps to the plan.
2. **M0-13 (package layout) ran before M0-5 … M0-12.** The roadmap puts it last. Running it first meant each fix was written once, in its final place. The M0-4 fix was still made on the old layout first, so that commit shows the original bug clearly.
3. **Dependencies were upgraded, not only completed (M0-3).** pip-audit reported known vulnerabilities in the old pins (Jinja2 3.1.4, Werkzeug 3.0.x). SQLAlchemy stays on the 2.0 line because Flask-SQLAlchemy 3.1 compatibility with 2.1 was not checked.
4. **Chores (M0-10): the assignee toggles "done" instead of editing.** The roadmap says "only the assignee or an admin edits". Admins edit everything; the assignee gets an "Erledigt / Wieder öffnen" button. This stops assignees from reassigning or renaming their own chores before real permissions arrive in M1-3 and M2-3.
5. **`X-Forwarded-Host` is never trusted (M0-5).** `TRUSTED_PROXY_HOPS` applies to `X-Forwarded-For` and `X-Forwarded-Proto` only; Nginx passes the host in the `Host` header. Trusting a client-controlled host header would let attackers poison links in future emails (password reset, invites).
6. **The unused htmx script was removed (M0-12).** M1-11 brings it back as a local, pinned file.
7. **A form macro (`templates/macros/forms.html`) was added in M0-9.** The roadmap plans macros in M1-11, but the new 10-character password rule needs visible error messages now.
8. **Bookings are serialised with a no-op `UPDATE` on the resource row (M0-11)** before the overlap check. On SQLite this takes the write lock; on Postgres it locks the row. `tests/test_bookings.py` proves the lock is held during the check.

## 2026-10-11 — Pre-push review of M0

An independent review (four reviewers, each finding checked by a second agent trying to refute it) confirmed 14 distinct issues; all are fixed in the commits marked "fix" in Wegito/Community#1. The ones that change design:

9. **SQLite migrations run with foreign keys off** (`migrations/env.py`), followed by `PRAGMA foreign_key_check`. Batch table rebuilds cannot drop a referenced table while foreign keys are enforced.
10. **Login attempts are counted before the password check, in one atomic UPDATE** (`User.reserve_login_attempt`). The attempt that reaches the limit sets the lock and is still checked. A locked account does the same hashing work as a wrong password.
11. **`.env` is loaded by the entry points, not by `community/config.py`.** `wsgi.py` loads it; the `flask` command loads it by itself. Tests no longer depend on a developer's `.env`.
12. **Test-client requests run in their own app context** (`tests/conftest.py`), so login state comes from the session cookie and the user loader, as in production.
13. **`parse_cents` rejects more than two decimal places** instead of rounding, because `1.234` is ambiguous between German and English notation.
