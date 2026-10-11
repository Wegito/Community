# Decisions log

Deviations from [`ROADMAP.md`](ROADMAP.md) and other decisions worth remembering. Newest first.

## 2026-10-11 — M0 implementation (Wegito/Community#1)

1. **One PR for M0, one commit per task.** The roadmap asks for one PR per task. M0 was delivered in the PR that added the roadmap. Each commit starts with its task ID, so the history still maps to the plan.
2. **M0-13 (package layout) ran before M0-5 … M0-12.** The roadmap puts it last. Running it first meant each fix was written once, in its final place. The M0-4 fix was still made on the old layout first, so that commit shows the original bug clearly.
3. **Dependencies were upgraded, not only completed (M0-3).** pip-audit reported known vulnerabilities in the old pins (Jinja2 3.1.4, Werkzeug 3.0.x). SQLAlchemy stays on the 2.0 line because Flask-SQLAlchemy 3.1 compatibility with 2.1 was not checked.
4. **Chores (M0-10): the assignee toggles "done" instead of editing.** The roadmap says "only the assignee or an admin edits". Admins edit everything; the assignee gets an "Erledigt / Wieder öffnen" button. This stops assignees from reassigning or renaming their own chores before real permissions arrive in M1-3 and M2-3.
5. **`X-Forwarded-Host` is never trusted (M0-5).** `TRUSTED_PROXY_HOPS` applies to `X-Forwarded-For` and `X-Forwarded-Proto` only; Nginx passes the host in the `Host` header. Trusting a client-controlled host header would let attackers poison links in future emails (password reset, invites).
6. **The unused htmx script was removed (M0-12).** M1-11 brings it back as a local, pinned file.
7. **A form macro (`templates/macros/forms.html`) was added in M0-9.** The roadmap plans macros in M1-11, but the new 10-character password rule needs visible error messages now.
8. **Bookings are serialised with a no-op `UPDATE` on the resource row (M0-11)** before the overlap check. On SQLite this takes the write lock; on Postgres it locks the row. `tests/test_bookings.py` proves the lock is held during the check.
