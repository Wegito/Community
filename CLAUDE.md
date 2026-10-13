# Community: rules for everyone who changes this code (people and agents)

The club web app of a Studentenverbindung: members, ranks, Ämter, permissions, laundry by QR code,
the Kasse (double-entry ledger with a Saldo per member). Flask 3, SQLAlchemy 2, Alembic, SQLite,
Tailwind (built), htmx. UI text is **German**; code, comments and docs are **English**.

Plan and decisions: `docs/ROADMAP.md` (milestones), `docs/TASKS.md` (task cards),
`docs/decisions.md` (why things are as they are), `docs/AGENTS.md` (how agents work on this repo).

## Commands

```bash
tools/check.sh          # every CI gate: lint, format, CSS, migrations, tests. Must pass before a commit.
tools/check.sh quick    # lint, format, tests (CHECK_TESTS=tests/test_x.py to run one file)
ruff format . && ruff check --fix .
tools/build-css.sh      # after adding Tailwind classes to a template; commit community/static/app.css
flask --app community db migrate -m "what changed" --rev-id 00NN   # then edit the file by hand
```

## Layout

- `community/core/`: identity (members, logins, invitations), org (ranks, Ämter, groups, semesters),
  authz (permissions), platform (audit, jobs, mail, notifications, QR), club settings, widgets.
- `community/modules/<name>/`: one blueprint per module: `__init__.py`, `permissions.py`, `models.py`,
  `services.py` (all logic), `forms.py`, `routes.py` (thin), `templates/<name>/`.
- `migrations/versions/00NN_*.py`: numbered by hand; seed data inserted with `sa.table()` in the migration.
- `tests/`: pytest; `tests/factories.py` makes members, users and officers.

## Rules that are checked in review

1. **Every route declares access**: `@public`, `@require_login` or `@require_perm('code')`.
   New permission codes go in the module's `permissions.py`, in `community/seed/authz.yaml`
   (who gets them) and in the matrix in `docs/ROADMAP.md` §6.5 (a test fails otherwise).
2. **Code checks permission codes, never rank or Amt names.** Club-specific names are data (D27).
3. **Money is integer cents.** Parse with `core.money.parse_cents`, show with the `|cents` filter. Never floats.
4. **Bookings go through `finance.services.post_journal()`** and are never edited or deleted; mistakes are reversed.
5. **Services commit nothing**; routes call `db.session.commit()`. Services raise a module error with a
   German message; routes `flash()` it after `db.session.rollback()`.
6. **Changes that matter are audited**: `audit.record('module.thing.action', obj, actor=..., diff=...)`.
7. **Times**: store UTC (`core.time.utcnow`), show Berlin time (`|dt`, `|d`); "today" is `today_local()`.
8. **Templates** use the macros in `templates/macros/ui.html` and `forms.html`; new Tailwind classes need
   `tools/build-css.sh`. Pages must work at phone width.
9. **Tests come with every change**: for a bug, a test that fails before the fix; for a feature, tests of the
   service and of the page (status codes, permissions, German messages). New routes with URL parameters
   need an entry in the `data` fixture of `tests/test_smoke.py`.
10. **No real personal data** in the repository: no names, emails or amounts from the club's files.
    Demo data is made up.
11. Keep changes inside the task's scope. Write down non-obvious choices in `docs/decisions.md`.

## Commit messages

First line: `<task id>: <what changed>` (e.g. `M3-5: Excel export of the Kassenbuch`), then a short
body that says why. No model names in commits or code.
