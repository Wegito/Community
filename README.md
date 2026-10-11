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
   - Remove `INVITE_CODE`: the shared registration code is gone. New members get personal
     invitation links (Mitglieder → Neues Mitglied).
   - Remove `ADMIN_EMAIL` and any `cd …` / `cat > .env << 'ENV'` lines.
   - `BASE_URL`: the public address, e.g. `https://community.example.org`. Required in
     production: links in emails (password reset, invitations) are built from it.
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

There is no self-registration. To add someone, open *Mitglieder → Neues Mitglied* (permission
`members.invite`): the member is created without login, and they get a personal, single-use
link (copy it, show it as a QR code, or let the app email it) at `/join/<token>`, where they
choose their login email and password.

Many members at once (go-live): *Mitglieder → Importieren* (permission `members.import`) reads a
CSV or Excel list (a template is on the page), shows a preview with every error and then creates
all valid rows as members without login, with their rank and, optionally, email invitations.

Forgotten passwords are reset by the members themselves (*Passwort vergessen?* on the login
page; the link is mailed). In development the mail only appears in the log of `flask jobs tick`.
Password and login email are changed on *Mein Profil*.

### Checks (the same ones CI runs)

```bash
ruff check . && ruff format --check .
python -m pytest --cov
flask db check                       # models and migrations agree
tools/build-css.sh && git diff --exit-code -- community/static/app.css   # committed CSS is up to date
```

### CSS and htmx (frontend)

The pages use [Tailwind CSS](https://tailwindcss.com) classes in the templates and
[htmx](https://htmx.org) for partial page updates. Both are served from `community/static/`;
nothing is loaded from a CDN, and the server needs no Node or build step.

- **After changing classes in a template, rebuild the CSS and commit it with the change:**
  ```bash
  tools/build-css.sh            # writes community/static/app.css (minified)
  tools/build-css.sh --watch    # rebuilds on every template change while you work
  ```
  The script downloads the Tailwind standalone CLI (pinned version, SHA-256 checked; Linux,
  macOS or Windows with Git Bash) once into `tools/bin/`, which git ignores. It builds
  `community/static/src/app.css` into `community/static/app.css`. CI rebuilds it and fails if the
  committed file differs. Tailwind only sees class names written out in templates (`*.html`), not
  ones built in Python.
- Shared look: `templates/layout.html` (menus), `templates/macros/ui.html` (page headers, buttons,
  badges, notices, cards, lists, tables) and `templates/macros/forms.html` (form fields).
- **htmx 1.9.12** is vendored as `community/static/vendor/htmx.min.js`
  (SHA-256 `449317ade7881e949510db614991e195c3a099c4c791c24dacec55f9f4a2a452`, also in
  `static/vendor/SHA256SUMS`, which CI checks). htmx requests send the CSRF token in the
  `X-CSRFToken` header automatically (`hx-headers` on `<body>`). To update it, download the new
  `htmx.min.js` from the htmx GitHub release, update `SHA256SUMS` and the hash in
  `tests/test_frontend.py`. htmx 2.x is a separate change (roadmap D25).
- Updating Tailwind: change `TAILWIND_VERSION` and the hashes in `tools/build-css.sh` (from the
  release's `sha256sums.txt`), rebuild and commit `app.css`.

## Configuration

All settings come from environment variables or `.env` (see [`.env.example`](.env.example)).

| Variable | Meaning |
|---|---|
| `APP_ENV` | `production` (default), `development` or `testing` |
| `SECRET_KEY` | Required in production, at least 32 random characters. The app refuses to start otherwise. |
| `CLUB_NAME` | Name shown in the UI |
| `BASE_URL` | Public URL, e.g. `https://community.example.org`. Used for links in emails (password reset, invitations). **Required in production**: the app refuses to start without it, so links are never built from a forged `Host` header. |
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
| `INVITE_LINK_DAYS` | Days an invitation link (copied or sent by email) stays valid (default 14) |
| `INVITE_QR_HOURS` | Hours an invitation shown as a QR code stays valid (default 24) |
| `PASSWORD_RESET_MAX_AGE_MINUTES` | Minutes a password reset link stays valid (default 60); it also stops working once used |
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
- The app sets `X-Content-Type-Options`, `Referrer-Policy` and `X-Frame-Options` itself. Do not add them in
  Nginx as well, or browsers get them twice.
- `community/static/app.css` is built and committed; deploying needs no CSS build.
- Uploads (member import) may be up to 2 MB. Set `client_max_body_size 3m;` in Nginx: its default of 1 MB would
  refuse larger lists with a bare error page before the app can show its own message.
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
│   ├── identity/      Member, User and Invitation models, login loader, create-admin command
│   ├── authz/         permissions: registry, roles, @require_perm, can()
│   ├── org/           semesters, ranks, Ämter, groups
│   ├── platform/      audit log, background jobs, mail, notifications (bell), QR codes
│   ├── nav.py         the main menu and the "Verwaltung" group (links follow the routes' permissions)
│   ├── db.py time.py money.py security.py errors.py assets.py
├── modules/           one blueprint per feature: models, forms, routes, templates
│   ├── auth/ members/ announcements/ tasks/ bookings/ admin/
├── templates/         layout.html, macros/ (ui.html, forms.html), errors/, mail/ (plain-text email templates)
├── static/            app.css (built, committed), src/app.css (Tailwind source), vendor/htmx.min.js
migrations/            Alembic migrations (flask db …)
tests/                 pytest
tools/                 build-css.sh (Tailwind standalone CLI; downloads into tools/bin/)
```

New features are added as modules; the recipe is in the roadmap, §4.4.
