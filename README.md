# Community

Internal web app for the club: announcements, Putzplan (chores) and room/equipment bookings today; member profiles, Ämter, Kasse/Saldo, laundry via QR, drinks and the semester calendar are planned.

- **Plan:** [`docs/ROADMAP.md`](docs/ROADMAP.md) (milestones, task IDs, open questions)
- **Decisions and deviations from the plan:** [`docs/decisions.md`](docs/decisions.md)

## ⚠️ Do this first on the server (roadmap task M0-1)

The repository is public, and an old `.env` with `SECRET_KEY`, `INVITE_CODE` and `ADMIN_EMAIL` is still readable in the git history (commit `b2850ff`). Deleting the file does not remove it from the history. Before deploying this version:

1. **Find out what is running.** On the server: `cd ~/community && git log -1 --oneline`, and check whether gunicorn runs (`ps aux | grep gunicorn` or `systemctl status <service>`).
2. **Check the accounts** (if a database exists):
   `sqlite3 instance/community.db "SELECT id, email, role, created_at FROM user;"`
   Delete unknown accounts. Every `admin` must be someone you know.
3. **Get the server's `.env` out of git's way.** The old version tracked `.env`. This version removes it from git, so a plain `git pull` would either refuse to run (if you edited `.env`) or delete it. Move it aside, pull, and put it back:
   ```bash
   cd ~/community
   mv .env ~/community.env.keep
   git pull
   mv ~/community.env.keep .env && chmod 600 .env
   ```
   From then on `.env` is ignored by git. Do **not** use `git stash` or `git checkout .env`: both lose your edits.
4. **Set new secrets** in the server's `.env` (compare with `.env.example`):
   - `SECRET_KEY`: new random value, at least 32 characters:
     `python -c "import secrets; print(secrets.token_hex(32))"`
   - `INVITE_CODE`: a new code, or leave it empty to close registration.
   - Remove `ADMIN_EMAIL` and any `cd …` / `cat > .env << 'ENV'` lines.
   Rotating the key logs everyone out once. That is intended.
   The new version refuses to start in production with a `SECRET_KEY` shorter than 32 characters.
5. **GitHub settings** (repository → Settings):
   - *Code security*: enable **Secret scanning** and **Push protection**.
   - *Branches*: protect `main` and require the CI checks to pass.
6. Optional: purge the old `.env` from the history. This needs a force-push, and existing clones keep their copy, so rotating the secrets (step 4) is what actually protects you.

## Local development

Needs Python 3.12 or newer.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                 # then set APP_ENV=development in .env (the flask command reads it)
flask db upgrade                     # creates instance/community.db
flask create-admin                   # asks for email, name, password
flask run --debug
```

Open http://127.0.0.1:5000 and log in with the admin account.

### Checks (the same ones CI runs)

```bash
ruff check . && ruff format --check .
python -m pytest --cov
flask db check                       # models and migrations agree
```

## Configuration

All settings come from environment variables or `.env` (see [`.env.example`](.env.example)).

| Variable | Meaning |
|---|---|
| `APP_ENV` | `production` (default), `development` or `testing` |
| `SECRET_KEY` | Required in production, at least 32 random characters. The app refuses to start otherwise. |
| `INVITE_CODE` | Shared registration code. Empty = registration closed. Replaced by personal invites in M1-7. |
| `CLUB_NAME` | Name shown in the UI |
| `BASE_URL` | Public URL, e.g. `https://community.example.org`. Used for links in emails; set it in production. |
| `SQLALCHEMY_DATABASE_URI` | Default `sqlite:///community.db` (in `instance/`) |
| `TRUSTED_PROXY_HOPS` | `1` behind one Nginx, `0` without a proxy (default) |
| `SESSION_COOKIE_SECURE` | Empty = default: `true` in production, `false` in development. Set `false` only for production without HTTPS. |
| `MAX_FAILED_LOGINS`, `LOGIN_LOCKOUT_MINUTES` | Login lock after failed attempts (default 5 attempts, 15 minutes) |
| `MAIL_BACKEND` | `smtp`, `console` (only logs the mail) or `memory` (tests). Empty = `smtp` if `MAIL_SERVER` is set, else `console`. Development defaults to `console`. |
| `MAIL_SERVER`, `MAIL_PORT` | SMTP server and port (default 587, or 465 with `MAIL_USE_SSL`) |
| `MAIL_USE_TLS` | STARTTLS, usually port 587 (default `true` unless `MAIL_USE_SSL`) |
| `MAIL_USE_SSL` | Implicit TLS, usually port 465 (default `false`). Not together with `MAIL_USE_TLS`. |
| `MAIL_USERNAME`, `MAIL_PASSWORD` | SMTP login; empty username = no login |
| `MAIL_FROM` | Sender, default `Community <noreply@localhost>` |
| `JOB_MAX_ATTEMPTS` | Tries per background job before it is marked failed (default 5; waits 2, 4, 8, 16 minutes in between) |

## Background jobs and email

Emails (and later reminders and Google sync) are never sent inside a web request. The app
writes them to the `job` table, and `flask jobs tick` sends them. Run it **every minute**:

```bash
# crontab -e (as the user that runs the app)
* * * * * cd ~/community && .venv/bin/flask jobs tick >> ~/community-jobs.log 2>&1
```

or with a systemd timer (`community-jobs.service` with `Type=oneshot`,
`WorkingDirectory=` the app folder and `ExecStart=<venv>/bin/flask jobs tick`, plus a
`community-jobs.timer` with `OnCalendar=minutely`). Overlapping runs are safe: each job is
claimed atomically. Failed jobs are retried with growing pauses and then marked failed.

```bash
flask jobs tick                      # run due jobs now
flask jobs list --status failed      # what went wrong (also on /admin/jobs for admins)
flask jobs retry 42                  # queue a failed job again
flask mail test you@example.org      # send a test mail right away to check MAIL_*
```

Without `MAIL_SERVER` the app only logs mails (`MAIL_BACKEND=console`), so invitations and
password resets do not arrive. Use the club domain's mail host or a transactional provider
(roadmap §7.4); Gmail needs an app password and has a daily limit.

## Deploying or updating on the server

```bash
cd ~/community
git pull                             # first time: move .env aside first (see "Do this first", step 3)
source .venv/bin/activate
pip install -r requirements.txt
flask db upgrade                     # always before restarting
# first time only: flask create-admin
gunicorn --workers 2 --bind 127.0.0.1:8000 wsgi:app
# first time only: add the cron job or timer for `flask jobs tick` (see "Background jobs and email")
```

Notes:
- The entry point is now `wsgi:app` (the old `app.py` is gone). Update the gunicorn command or systemd unit.
- Behind Nginx set `TRUSTED_PROXY_HOPS=1`, and make Nginx pass `Host`, `X-Forwarded-For` and `X-Forwarded-Proto`.
- Without HTTPS, login only works with `SESSION_COOKIE_SECURE=false`. Use HTTPS for anything real (roadmap M8-1).
- The database tables were never created by the old code. If you created them by hand, back up the file, compare it with `migrations/versions/0001_baseline.py`, then run `flask db stamp 0001` before `flask db upgrade`.
- A full deploy kit (systemd units, Nginx example, backups) is roadmap task M8-2.

## Project layout

```
community/
├── __init__.py        create_app() and the MODULES list
├── config.py          settings per environment
├── extensions.py      db, migrate, login_manager, csrf
├── core/              shared code; never imports from modules/
│   ├── identity/      Member and User models, login loader, create-admin command
│   ├── authz/         permissions: registry, roles, @require_perm, can()
│   ├── org/           semesters, ranks, Ämter, groups
│   ├── platform/      audit log, background jobs, mail, notifications (bell)
│   ├── db.py time.py money.py security.py errors.py
├── modules/           one blueprint per feature: models, forms, routes, templates
│   ├── auth/ members/ announcements/ tasks/ bookings/ admin/
├── templates/         layout.html, macros/, errors/, mail/ (plain-text email templates)
migrations/            Alembic migrations (flask db …)
tests/                 pytest
```

New features are added as modules; the recipe is in the roadmap, §4.4.
