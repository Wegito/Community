# Community: Master Roadmap

> Status: plan v1 (2026-10-11). **M0 is implemented** (M0-2 … M0-13, see §8.3); M0-1 is server work for the owner (see README). This file is the single source of truth for future coding sessions. Every task has an ID, for example `M0-9`. Reference that ID in branch names and commits.
> Points marked **[verify]** were not confirmed and must be checked before implementation. Points marked **[Q n]** depend on an open question in §10.

---

## 1. Vision & guiding principles

**Vision.** Every member opens one page, **Mein Bereich** (`/me`), and sees three things: what they have to do, what they owe (Saldo), and what is coming up. Each office holder (Amt) gets tools that still work after the semester handover. The Excel sheets, the laundry list and the drinks tally move into one system, and that system can grow module by module.

**Principles**
1. **Data over code.** Ranks, Ämter, groups, prices, permission grants and event types are database rows. Adding a new Amt or rank is an admin action, not a code change.
2. **Code checks permission codes, never rank or office names.** It uses `can('finance.journal.post')` and never `role == 'admin'`.
3. **Money is integer cents.** It lives in an immutable double-entry journal. Corrections are made only by reversal. The Saldo is always computed, never stored.
4. **Capture first, bill later.** Drinks, laundry and event attendance are recorded as usage. A settlement then turns usage into journal entries.
5. **GET never writes.** QR links, link previews and prefetching must not be able to create records.
6. **Boring tech, small modules.** Flask, Jinja, htmx and SQLite, organised as blueprints with one-way dependencies. Upgradability comes from conventions (consistent module shape, migrations, tests), not from a plugin framework.
7. **Privacy and audit by default.** Members never see each other's Saldo. Every rank, office, permission and finance change is written to an append-only audit log.
8. **The club owns accounts and keys, not individual people.** This covers the Google account, the Cloud project, the domain, the mail sender and the server. At least two people must have access to each.
9. **Ship thin releases.** A deferred feature is better than a half-built one. Every PR includes tests, plus a migration when models change.

---

## 2. Domain glossary

### 2.1 Ranks (Status)
Ranks are stored as data. Labels are editable, and extra ranks can be added after [Q6].

| Rank | Meaning | Typical rights (proposal, see §6) |
|---|---|---|
| **Spefux** (user wrote "Speefux"; the spelling is a label setting) | Prospective member before formal reception (*Rezeption*) | Own profile, events, tasks, own Saldo. No member directory [Q6]. |
| **Fux** (pl. Füchse) | Received new member, in training under the Fuxmajor | Plus member directory and bookings. Fux curriculum. |
| **Bursch** (pl. Burschen) | Full member after the *Burschung* (Burschifizierung). The only rank eligible for Ämter. | Plus contact data and Fux progress. Can hold an Amt. |
| **Alter Herr** (AH, Philister) | Alumnus after *Philistrierung*, usually also a member of the Altherrenverband (AHV) | Profile, events, own Saldo. Module access to be decided [Q11]. |
| *(optional)* inaktiver Bursch, Ehrenmitglied, Konkneipant | Only if the club uses them [Q6] | — |

### 2.2 Ämter (offices, held by Burschen, change every semester)
| Amt | Job in the club | What the app gives them |
|---|---|---|
| **Senior** | Writes the whole semester program: events, what to do and when. Leads the club. | Program editor (draft and publish), rank changes, office assignment, co-signing finance entries |
| **Consenior** | "Boss around." Checks that everything works and that needed supplies are bought. Divides members into groups for tasks. Often the Senior's deputy [Q9]. | Task and group workspace, Putzplan rotation, shopping list, laundry machines, co-signing finance entries |
| **Schriftwart** | **Not described by the user** [Q10]. Assumed: convent minutes (Protokolle), member records, correspondence. | Member data editing and documents. Nothing is built for this Amt until Q10 is answered. |
| **Kassenwart** | Finance: pays bills, tells members what they owe, records event costs | Ledger, Saldenliste grid, bulk charges and splits, payments, xlsx export, payment-request emails, posting settlements |
| **Fuxmajor** | Turns Füchse into Burschen. Teaches traditions, rules and more. | Invites, Fux curriculum and sign-off, Fux progress, attendance |
| **Bierwart** | Checks drinks stock, buys when needed, records who drank what, and sends the finished list to the Kassenwart | Catalog and prices, stock, Strichliste, settlement ("An Kassenwart senden"), API tokens for the counter app |
| *(optional, data only)* Kassenprüfer, Hauswart, … | Only if the club has them [Q10] | Read-only finance, or laundry and house duties |

### 2.3 Other terms
| Term | Meaning in this app |
|---|---|
| **Aktivitas** | Active members (Füchse and Burschen) |
| **AHV (Altherrenverband)** | Alumni association, often a separate legal entity with its own money. Never mixed into the Aktivitas books without confirmation [Q21]. |
| **Convent** | Members' assembly that decides elections, receptions and Burschungen. The app records the decision (`convent_ref`). It does not encode the club's Comment (code of customs). |
| **Rezeption / Burschung / Philistrierung / Austritt** | Rank transitions: Spefux→Fux, Fux→Bursch, Bursch→AH, and leaving the club |
| **kommissarisch** | Acting office holder (`is_acting`) |
| **Amtsübergabe** | Handover between office holders at semester change |
| **Semesterprogramm** | The Senior's list of events for a semester |
| **Pflichtveranstaltung** | Mandatory event |
| **Hausbewohner** | People living in the club house. A group, not a rank. Laundry rights come from this group. |
| **Putzplan** | Cleaning rota, generated as rotating group tasks |
| **Einkaufsliste** | Shopping list (tasks of category "Einkauf") |
| **Kasse / Buch** | A separate cash book (e.g. Aktivenkasse, Getränkekasse) [Q21] |
| **Saldo** | A member's balance per Kasse. Negative means the member owes the club ("Du schuldest 12,50 €"); positive means credit ("Guthaben"). |
| **Kontoauszug / Saldenliste / Kassenbuch** | Per-member statement / all members' balances / chronological journal |
| **Übertrag** | Opening balance imported from the old Excel sheet |
| **Auslage** | Money a member spent for the club. It is credited to that member's Saldo. |
| **Zahlungsaufforderung** | Payment-request email with statement, IBAN and Verwendungszweck (`payment_ref`, e.g. `M-0042`) |
| **GiroCode** | EPC QR code for bank transfers (optional) **[verify spec]** |
| **Strichliste** | Drinks tally list, one sheet per evening |
| **Abrechnung (settlement)** | Turns unsettled usage (drinks, laundry) into journal entries. States: DRAFT → SUBMITTED → POSTED. |
| **Schwund** | Shrinkage: stock counted minus stock expected |
| **Kassenprüfung** | Annual audit of the books (xlsx export) |

### 2.4 Technical terms
- **member**: the person, who may have no login (e.g. an imported Alter Herr). **user**: the login account, linked 1:1 to a member.
- **role**: anything that grants rights (a rank, an Amt, a group, a custom grant, a system role). **permission code**: `module.resource.action`.
- **journal / entry**: a ledger posting and its debit/credit pairs. **job**: a background task run by `flask jobs tick`.

---

## 3. System map

### 3.1 Whole-system tree
```
Community
├── core                                  (never imports a module)
│   ├── app & config ── factory create_app(config) · Dev/Test/Prod · fail-fast secrets · BASE_URL · proxy hops
│   ├── db ── naming convention · UTCDateTime · timestamps · Flask-Migrate · SQLite WAL
│   ├── identity
│   │   ├── member ── personal data · payment_ref · status ACTIVE/ENDED · pseudonymisation
│   │   ├── user ── password · remember-me · DB login throttling · session_version · reset
│   │   ├── invitation ── link / email / QR · single use · bound to a pre-created member
│   │   └── external_identity ── Google OIDC link (optional)
│   ├── org
│   │   ├── semester ── WS/SS dates · current_semester()
│   │   ├── rank + rank_history ── "Rang ändern" form (v1) · workflow (M7-2)
│   │   ├── office + office_assignment ── date ranges · acting · max holders · incompatibilities
│   │   └── group + membership ── task groups · Hausbewohner (residence) · committees
│   ├── authz ── permission registry · roles · grants · @require_perm · can() · policies.py
│   └── platform
│       ├── audit_log (append-only)
│       ├── jobs ── job table · `flask jobs tick` (systemd timer) · retry · dedupe
│       ├── notify ── in-app bell · email (SMTP adapter) · preferences
│       └── qr ── qr_token · /q/<token> router · printable sheets · rotation
├── modules
│   ├── auth ── login · logout (POST) · /join/<token> · password reset · (Google sign-in)
│   ├── profile "Mein Bereich" (/me)
│   │   ├── header ── name · Biername · rank · Ämter · groups
│   │   ├── widgets ── Meine Aufgaben · Mein Saldo · Nächste Veranstaltungen · Meine Wäsche
│   │   │              · Meine Getränke · Fux-Fortschritt · office tiles (KW, BW, Sen, Con, FM)
│   │   └── settings ── password · notifications · Google opt-in · log out everywhere · data export
│   ├── members ── directory (/m/<public_id>, login required) · admin · bulk import · Austritt
│   ├── tasks (Consenior) ── task → member | group | Amt · statuses · rotation · templates · Einkaufsliste
│   ├── announcements ── audience by role · pinned
│   ├── bookings ── admin-managed resources · end > start · cancel
│   ├── finance (Kassenwart)
│   │   ├── ledger ── books · accounts · journal · entries · reversal · Saldo
│   │   ├── workspace ── Saldenliste grid · payments · charges · bulk/split with preview
│   │   ├── club payments ── supplier invoices · Auslagen
│   │   ├── outbound ── Zahlungsaufforderung emails · reminders · (GiroCode)
│   │   └── excel ── opening-balance import (dry-run, reconciliation) · xlsx export
│   ├── laundry ── machines · QR form · lists · monthly billing (optional)
│   ├── events (Senior) ── program editor · audience · RSVP · attendance · costs → ledger
│   │   └── calendar ── ICS feeds · club Google Calendar push · ACL sharing · email reminders
│   ├── drinks (Bierwart) ── catalog/prices · Strichliste · stock · settlement → Kassenwart
│   ├── api /v1 ── device & personal tokens · products · members · consumptions · settlements
│   ├── fux (Fuxmajor) ── curriculum · progress · sign-off            (M7)
│   └── documents (Schriftwart) ── Satzung · Comment · Protokolle     (M7, scope [Q10])
└── ops ── deploy kit · backups + restore drill · domain/HTTPS · monitoring · GDPR pack · staging
```

### 3.2 QR laundry flow
```
[Sticker: "Waschmaschine 1" + printed domain + QR → https://<domain>/q/<token>]
        │ scan with phone camera
        ▼
GET /q/<token> ── unknown / rotated token ──► 404 page "Maschine manuell wählen"
        │ valid (target = laundry machine)
        ▼
logged in? ── no ──► /login?next=/q/<token> ──► password (or Google, M5-11)
        │                     └─ next accepted only if relative ("/…", not "//" or "/\", no scheme/host)
        │ yes (remember-me cookie, ~90 days)  ◄────────────────────────────────┘
        ▼
has laundry.use.create_own?  (comes from the Hausbewohner group) ── no ──► 403 "Nur für Hausbewohner"
        │ yes
        ▼
machine status DEFECT? ── yes ──► status page + [Defekt melden] → task for Consenior/Hauswart
        │ no
        ▼
Pre-filled form (rendered by GET, which writes nothing):
  machine · "Angemeldet als Max — nicht du? Abmelden" · now (Europe/Berlin)
  program (last used or default) · cycles = 1 · price shown · hidden submission_id (UUID)
        │ one tap [Speichern]
        ▼
POST + CSRF ─┬─ submission_id already stored ──► show the existing entry (double-tap safe)
             ├─ same member + machine within N min ──► "Wirklich nochmal?" confirm
             └─ daily cap reached ──► refuse with a message
        ▼
laundry_use saved (unit_price_cents snapshot, source = QR) + audit
        ▼
Success page: "Eingetragen – fertig ca. 21:40"   [Rückgängig] (5 minutes, sets voided_at)
        ▼
(only if billing is enabled [Q26]) monthly laundry settlement → journal → member Saldo
```

### 3.3 Onboarding flow
```
Inviter (members.invite) ──► "Neues Mitglied": name, email (optional), starting rank (only allowed ranks)
   ──► member row (no login) + rank_history row + invitation (token stored as hash, channel, expiry)
   ──► delivery:  email link (14 days) │ copy link (14 days) │ QR on the inviter's phone (24 hours)

Invitee opens /join/<token>
   ├─ invalid / expired / revoked ──► "Neuen Link anfordern" (notifies the inviter)
   ├─ email already has an account ──► offer login
   └─ page shows club and inviter ──► set password │ "Mit Google fortfahren" (M5-11)
        ──► user created · invitation consumed · audit · (optional policy: pending approval [Q7])
        ──► jobs: welcome mail · notify Fuxmajor · calendar share (only if opted in)

First login ──► /me wizard: phone, Biername, notification preferences, calendar (ICS link / Google opt-in / skip)

Bulk (go-live):  CSV/XLSX ──► preview + validation ──► members without login ──► optional invite batch (jobs)
Bootstrap:       `flask create-admin` ──► custom.sysadmin ──► import members ──► assign first Ämter ──► hand over
Leaving:         Austritt ──► status ENDED ──► role system.former_member (own profile + own Saldo only)
                 ──► office/group assignments closed ──► calendar unshared ──► Kassenwart notified
                 ──► ledger kept; personal data pseudonymised after the retention period
```

### 3.4 Money flow (Bierwart → Kassenwart → member Saldo)
```
CAPTURE (usage, no money yet)        SETTLE / CHARGE                          JOURNAL (debit / credit, cents)
──────────────────────────────       ───────────────────────────────────     ──────────────────────────────────
Bierwart Strichliste ─┐
Counter app (API v1) ─┼─► drink_consumption ─► Bierwart "Abrechnung bis <Datum>"
Fridge QR (optional) ─┘   (priced by server)   DRAFT ─► "An Kassenwart senden" ─► SUBMITTED
                                               Kassenwart reviews ─► POSTED ─────► per member: D MEMBER / C INCOME:Getränke
                                               (rejected ─► back to DRAFT)
QR laundry ─► laundry_use ─► monthly settlement (same states) ─────────────────────► D MEMBER / C INCOME:Wäsche
Event ─► attendance + cost items ─► Kassenwart "abrechnen" (preview) ──────────────► D MEMBER / C INCOME:Veranstaltung
                                    payer's receipt ───────────────────────────────► D EXPENSE / C MEMBER (Auslage)
Kassenwart bulk charge (Semesterbeitrag), preview ─────────────────────────────────► D MEMBER / C INCOME:Beiträge
Member bank transfer "M-0042" ─► Kassenwart "Zahlung erfassen" ────────────────────► D BANK / C MEMBER
Supplier invoice paid ─► Kassenwart ───────────────────────────────────────────────► D EXPENSE / C BANK
Mistake ─► reversal journal (swaps debit and credit; the original is never edited)

Saldo(member, book) = Σ credits − Σ debits on the member's account   (< 0 = owes the club)
   └─► /me "Mein Saldo" · Kontoauszug · Saldenliste (Kassenwart) · Zahlungsaufforderung · xlsx export
```

### 3.5 Google Calendar flow
```
Senior edits event (DRAFT) ──► publish ──┬──► ICS feeds (club + personal token) are always current;
                                         │    Google refreshes subscribed URLs on its own schedule [verify]
                                         └──► job calendar.push(event) ──► Calendar API, service account,
                                                club-owned calendar: insert / patch / delete,
                                                stored google_event_id (+ id chosen by the app for idempotent retries [verify])
                                                failure ──► retry with backoff; sync status shown to the Senior
Cancel / delete ──► job ──► Google event cancelled or deleted

Member opt-in (settings: Google address) ──► job calendar.share ──► ACL "reader" on club calendar ──► acl_rule_id stored
   help page: "set default notifications for the club calendar once"  (the app cannot set them per user [verify])
Opt-out / Austritt ──► job calendar.unshare ──► ACL rule deleted

Reminders the app controls: email N hours before (per preferences); Pflicht events flagged
Events created directly in Google: ignored in v1 (the app is master; no two-way sync)
```

---

## 4. Target architecture & project layout

### 4.1 Binding decisions
These resolve the contradictions between the earlier design drafts. If you deviate from one, record why in `docs/decisions.md`.

| # | Topic | Decision | Rejected |
|---|---|---|---|
| D1 | Stack | Flask, Jinja and htmx as a modular monolith, plus a small JSON API | SPA, microservices |
| D2 | Module system | Blueprints with a consistent package shape and plain Python lists (`MODULES`, `WIDGETS`, `QR_HANDLERS`) | Plugin registry framework, `ENABLED_MODULES`, `_template` generator |
| D3 | Database | **SQLite** with WAL, `busy_timeout` and `foreign_keys=ON`, decided once in M0. Only portable SQL, so Postgres remains possible later. | Dual-DB CI, switching engines right before finance go-live |
| D4 | Migrations | Flask-Migrate. Naming convention set in the **first** migration. `render_as_batch=True`. Merged migrations are never edited. | `create_all` |
| D5 | Time | UTC stored (custom `UTCDateTime` type), Europe/Berlin displayed. Semesters and validity ranges use `DATE`; `valid_to` is inclusive. | Naive datetimes, `datetime.utcnow` |
| D6 | Money | Integer cents. Compact double-entry: `fin_entry(debit_account, credit_account, amount_cents > 0)` grouped by `fin_journal`. Immutable, reversal only, Saldo computed. | Floats; single-entry member-only ledger; `fin_period`/persisted draft batches in v1 |
| D7 | Authorization | One role layer: rank, Amt, group, custom and system all map to a role, and roles map to permission codes. Additive grants, no deny rules. `custom.sysadmin` has **no finance write rights**. | Separate rank/office/user permission tables, wildcards, an all-powerful superadmin flag |
| D8 | Office terms | Date ranges; a holder stays in office until the successor takes over (`valid_to` NULL). `semester_id` is informational only. | Rights expiring automatically at semester end |
| D9 | Laundry rights | Hausbewohner group [Q12] | By rank, or all members |
| D10 | QR | Generic `/q/<token>` and a shared `qr_token` table. Tokens identify the *target*, never the user. GET never writes. | `/w/<token>`, token stored on the machine row |
| D11 | Drink prices | The server prices each item on receipt, using the price valid at `occurred_at`, and snapshots it. The client never sends prices. | Pricing at settlement, client-sent prices |
| D12 | Idempotency | API: unique `(api_client_id, client_event_id)`. Forms: unique `submission_id`. Journals: unique `idempotency_key`. | Batch `Idempotency-Key` store |
| D13 | Settlements | One `settlement` table with a `module` column. States DRAFT → SUBMITTED → POSTED; REJECTED returns to DRAFT. Each module writes its own `settle()` service. | Abstract "billable source" framework |
| D14 | Settlement trigger | Only a named Bierwart, in the app or with a *personal* token. Device tokens only send consumptions. | Device token with settle scope |
| D15 | Login throttling | DB counters on `user` (`failed_logins`, `locked_until`); works across gunicorn workers | In-memory Flask-Limiter (each worker counts separately [verify]) |
| D16 | Background work | One `job` table run by `flask jobs tick` from a systemd timer or cron | APScheduler inside gunicorn, an app-wide event bus or outbox for everything |
| D17 | Rank changes (v1) | Direct "Rang ändern" form: permission, date, convent reference, audit. Request and approval workflow later (M7-2). | Transition-rule engine with prerequisite checkers in v1 |
| D18 | Event visibility | Audience by role (rank, Amt or group) | By rank only |
| D19 | Laundry billing | Monthly totals through a settlement, with a price snapshot on each use | One ledger charge per wash |
| D20 | Splits | Largest-remainder method with tie-break by member id: exact cents, deterministic | Leftover cents to a club account |
| D21 | Excel | The owner's Excel file stays untouched. Community gets its own Kasse with the same structure (journal with one column pair per Kasse, Kontokarten, cost centres, Bilanz/GuV, AH-Kontingent) and becomes the source of truth; the Excel is only read (opening balances, parallel run), never written; the export reproduces the familiar layout. Decided 2026-10-13 [Q19] | Excel or Google Sheet as master, live two-way sync |
| D22 | Google | The app is master and pushes one way. ICS first, then the club calendar with ACL sharing, plus email reminders. OIDC sign-in is optional. No per-user Google tokens until needed. | Two-way sync, per-user OAuth in v1 |
| D23 | Four-eyes rules | Compare **people**, not roles | Role-based checks |
| D24 | Language | German UI only, no Flask-Babel until English is needed [Q13]. Own filter formats `12,50 €`. | i18n from day one |
| D25 | Frontend | Tailwind standalone CLI build (no Node); local, pinned htmx 1.9.x; htmx 2.x is a separate later PR | Tailwind Play CDN, unpkg |
| D26 | Naming | `@require_perm`, `@public`; module names `auth, members, tasks, events, finance, drinks, laundry, fux, documents, api` | `require_permission`, `fuxen`, `program` |
| D27 | Other clubs | One install per club; club-specific names, ranks, Ämter, prices and accounts are data (settings or a preset), never code | Multi-tenant hosting of several clubs in one database |

### 4.2 Project layout
```
Community/
├── wsgi.py                         # app = create_app()
├── requirements.txt                # pinned runtime deps
├── requirements-dev.txt            # pytest, pytest-cov, ruff, pip-audit
├── .env.example  .gitignore
├── migrations/                     # Alembic, one linear history
├── community/
│   ├── __init__.py                 # create_app(config=None); MODULES list → register blueprints, CLI
│   ├── config.py                   # BaseConfig / Dev / Test / Prod (Prod refuses weak or missing secrets)
│   ├── extensions.py               # db, migrate, login_manager, csrf
│   ├── core/
│   │   ├── db.py                   # metadata naming convention, UTCDateTime, TimestampMixin, SQLite pragmas
│   │   ├── time.py  money.py       # utcnow(), Berlin display; cents parse/format
│   │   ├── security.py             # safe_next(), login throttling helpers
│   │   ├── identity/               # member, user, invitation, external_identity (models, services, routes)
│   │   ├── org/                    # semester, rank, office, group (models, services, admin routes)
│   │   ├── authz/                  # permission registry, roles, decorators, can(), policies.py, sync/seed CLI
│   │   └── platform/               # audit.py, jobs.py, notify.py, mail.py, qr.py, errors.py
│   ├── modules/
│   │   ├── auth/ profile/ members/ tasks/ announcements/ bookings/
│   │   ├── finance/ laundry/ events/ drinks/ api/ fux/ documents/
│   │   │   (each module) __init__.py  → blueprint
│   │   │                 routes.py    → thin: parse, check permission, call service, render
│   │   │                 services.py  → all business logic and transactions
│   │   │                 models.py  forms.py  permissions.py (codes + descriptions)
│   │   │                 widgets.py (optional /me partials)  templates/<module>/
│   ├── templates/                  # layout.html, macros/, errors/
│   ├── static/                     # built tailwind.css, htmx.min.js (pinned), favicon
│   └── seed/                       # authz.yaml (default grants), org.yaml (ranks, Ämter)
├── tests/
│   ├── conftest.py  factories.py
│   ├── unit/  integration/
│   ├── test_route_coverage.py      # every route has @require_perm or @public
│   └── test_permission_matrix.py   # seed grants × endpoints
├── deploy/                         # gunicorn.conf.py, community.service, community-jobs.{service,timer},
│                                   # nginx.conf.example, backup.sh, deploy.sh
├── docs/                           # ROADMAP.md, decisions.md, api/openapi.yaml, spikes/
└── .github/                        # workflows/ci.yml, dependabot.yml
```

### 4.3 Dependency rules (enforced by review and tests, no import-linter)
```
core  ◄── every module
tasks ◄── events (event tasks), drinks (stock alerts), laundry (defect reports)
finance ◄── drinks, laundry, events (costs)        finance never imports them
drinks ◄── api
profile knows widget endpoint *names* only (strings in WIDGETS); it imports no module
```
- Routes call services; services use models. Templates never query the database.
- A module never imports another module's models. It calls that module's `services` functions.
- Services raise domain exceptions; routes turn them into flash messages or 4xx responses. There is one transaction per service call.
- Slow or external work (email, Google) is always a job, never done inside a request.

### 4.4 Recipe: adding a module later
1. Create `community/modules/<name>/` with the standard files.
2. Declare permission codes in `permissions.py`, then run `flask authz sync`. Add default grants to `seed/authz.yaml` and a row to §6.
3. Add models and generate a migration. Review it by hand.
4. Append the blueprint to `MODULES`. Optionally add a `/me` widget to `WIDGETS` and a QR handler to `QR_HANDLERS`.
5. If it bills members, write a `settle()` service that creates a `settlement` and calls `finance.services.post_journal()`.
6. Write tests: services, routes, the permission matrix row and route coverage.

---

## 5. Data model

Conventions:
- Every table has `id`, plus `created_at` and `created_by` where useful.
- Money columns are `*_cents` (integer, EUR).
- Timestamps are UTC; validity ranges are `DATE`.
- Public URLs use `public_id` (UUID) or random tokens, never sequential ids.
- Members are never hard-deleted; they are pseudonymised. Finance foreign keys use `ON DELETE RESTRICT`.

### 5.1 Core: identity
| Table | Key columns | Notes |
|---|---|---|
| `member` | public_id UUID uq, first_name, last_name, biername?, email (lowercase) uq?, phone?, address?, status ACTIVE/ENDED, ended_on?, ended_reason (enum), payment_ref uq (`M-0042`), show_phone, show_address, joined_on, pseudonymized_at? | A member can exist without a login |
| `user` | member_id uq FK, login_email (lowercase) uq, password_hash? (NULL for Google-only), is_enabled, session_version, failed_logins, locked_until?, last_login_at | The existing `user` table becomes login-only |
| `invitation` | member_id FK, email?, token_hash uq, channel LINK/EMAIL/QR, expires_at, created_by, accepted_at?, revoked_at? | Link 14 days, QR 24 hours, single use |
| `external_identity` | user_id FK, provider, subject, email_at_link, linked_at; uq(provider, subject) | Google `sub`, never the email (M5-11) |

### 5.2 Core: org
| Table | Key columns | Notes |
|---|---|---|
| `semester` | code uq ("WS26/27"), kind WS/SS, starts_on, ends_on, status PLANNING/ACTIVE/CLOSED | CHECK starts_on < ends_on; no overlaps (service) |
| `rank` | key uq, label, abbreviation, sort_order, role_id uq, is_aktivitas, is_alumni, archived_at? | Seed: spefux, fux, bursch, alter_herr |
| `rank_history` | member_id, rank_id, valid_from, valid_to?, convent_ref?, note, changed_by | Partial unique index on (member_id) WHERE valid_to IS NULL guarantees exactly one current rank |
| `office` | key uq, label, abbreviation, role_id uq, max_holders (default 1), description_md, sort_order, archived_at? | description_md is the written job description kept for the handover |
| `office_eligible_rank` | office_id, rank_id (PK) | Default: bursch for all six Ämter [Q9] |
| `office_incompatibility` | office_a_id, office_b_id (PK) | Seed: Kassenwart ⊥ Bierwart (and ⊥ Kassenprüfer if that office exists) [Q9] |
| `office_assignment` | office_id, member_id, semester_id?, valid_from, valid_to?, is_acting, elected_on?, convent_ref?, assigned_by, end_reason? | Overlapping holders must not exceed max_holders (checked by the service) |
| `member_group` | name, kind TASK/RESIDENCE/COMMITTEE/CUSTOM, semester_id?, role_id?, archived_at? | Only groups that grant rights have a role (e.g. Hausbewohner) |
| `group_membership` | group_id, member_id, valid_from, valid_to?, is_lead | |

### 5.3 Core: authz & platform
| Table | Key columns | Notes |
|---|---|---|
| `permission` | code uq, module, description, is_sensitive, last_seen_at | Written by `flask authz sync` from code |
| `role` | key uq (`rank.bursch`, `office.kassenwart`, `group.hausbewohner`, `custom.sysadmin`, `system.former_member`), label, kind RANK/OFFICE/GROUP/CUSTOM/SYSTEM | |
| `role_permission` | role_id, permission_id (PK), granted_by, granted_at | Edited via seed YAML (v1) or matrix UI (M7-6) |
| `member_role` | member_id, role_id, valid_from, valid_to?, granted_by, reason | Custom and system roles only |
| `audit_log` | occurred_at, actor_user_id? / actor_api_client_id?, action, entity_type, entity_id, diff_json, ip?, request_id | Append-only; IP retention period per M8-7 |
| `job` | kind, payload_json, run_at, status, attempts, last_error, locked_until?, dedupe_key uq? | Used for email, Google sync, reminders, rotations |
| `notification` | member_id, kind, title, link, created_at, read_at? | In-app bell |
| `notification_pref` | member_id, kind, channel, enabled (PK member_id+kind+channel) | Some kinds are mandatory (password reset, payment request) |
| `qr_token` | token uq (≥128-bit), target_type, target_id, label, active, rotated_from_id? | Shared by laundry, fridge and event check-in |
| `api_client` | name, token_prefix, token_hash uq, scopes, owner_member_id? (NULL = device token), created_by, last_used_at, revoked_at? | Token shown once |
| `attachment` (M7-4) | storage_key, filename, mime, size, sha256, uploaded_by | Receipts, minutes, PDFs |

### 5.4 Tasks
| Table | Key columns | Notes |
|---|---|---|
| `task` (replaces `chore`) | title, description, category, semester_id?, event_id?, **assignee_member_id? / assignee_group_id? / assignee_office_id?** (CHECK exactly one), due_at?, status OPEN/DONE/VERIFIED/CANCELLED, source MANUAL/ROTATION/OFFICE_DUTY/STOCK_ALERT/DEFECT, template_id?, completed_by/at, verified_by/at | An office assignee resolves to the *current* holder, so open duties move with the Amt |
| `task_template` | title, description, category, recurrence, office_id?, event_type_id?, due_offset_days | Office duties and event preparation |
| `task_rotation` | template_id, semester_id, group_order_json, interval_days, next_generate_on | Putzplan |

### 5.5 Events & calendar
| Table | Key columns |
|---|---|
| `event_type` | key, label, default_mandatory, color |
| `event` | public_id, semester_id, event_type_id, title, description, starts_at, ends_at, location, status DRAFT/PUBLISHED/CANCELLED, is_mandatory, rsvp_deadline?, responsible_office_id?, responsible_member_id?, published_at, version, google_event_id?, google_etag?, google_sync_status, settled_journal_id? |
| `event_audience` | event_id, role_id (empty = all active members) |
| `event_participation` | PK(event_id, member_id), rsvp YES/NO/MAYBE, excuse?, excused_by?, attended?, guests |
| `event_cost_item` | event_id, description, amount_cents, paid_by_member_id? |
| `event_price` | event_id, rank_id? (NULL = default), amount_cents |
| `calendar_feed_token` | member_id, token_hash, created_at, revoked_at? |
| `calendar_share` | member_id, google_email, acl_rule_id?, status, last_error? |

### 5.6 Finance
| Table | Key columns | Notes |
|---|---|---|
| `fin_book` | key, label | At least the Aktivenkasse; more after [Q21] |
| `fin_account` | book_id, kind MEMBER/BANK/CASH/INCOME/EXPENSE/EQUITY/CLEARING, member_id?, name; uq(book_id, member_id) | Member accounts are created lazily |
| `fin_category` | key, label, active | Cost centres (Kostenstellen), editable per semester: the Excel has about 30 (Kneipen, Vorträge, Stiftungsfest, Party, Renovierung, …; see the M3-0 spike). Events from M5 become cost centres. Earmarked reserves (Spenden, Fuxenkasse, Waschmaschine, LAN) are accounts, not categories. |
| `fin_journal` | book_id, kind CHARGE/PAYMENT/SPLIT/SETTLEMENT/OPENING/REVERSAL/MANUAL, title, booked_on, semester_id?, source_type?, source_id?, idempotency_key uq?, reverses_journal_id uq?, posted_by, cosigned_by?, posted_at, note | Immutable after insert (service guard + tests) |
| `fin_entry` | journal_id, debit_account_id, credit_account_id, amount_cents CHECK > 0, category_id, memo, external_ref? | Balanced by construction |
| `settlement` | module DRINKS/LAUNDRY, book_id, period_to, status DRAFT/SUBMITTED/POSTED, submitted_by/at, posted_by/at, reject_note?, journal_id uq?, totals_json | Only one non-POSTED settlement per (module, book) |

- Saldo is computed with this formula: `SUM(amount WHERE credit = member_acct) − SUM(amount WHERE debit = member_acct)`.
- At club size, no cached balance is needed. If one is ever added, it must be rebuildable from the journal.

### 5.7 Drinks
| Table | Key columns |
|---|---|
| `drink_product` | key, name, unit_label, is_alcoholic, min_stock_units, is_active, sort_order, external_ref? |
| `drink_price` | product_id, price_cents, valid_from, set_by |
| `drink_sheet` | title, event_id?, opened_by, closed_at? |
| `drink_consumption` | member_id, product_id, quantity (≠ 0; negative = correction), unit_price_cents (server snapshot), occurred_at, source MANUAL/SELF/DEVICE/IMPORT, sheet_id?, recorded_by_member_id?, api_client_id?, client_event_id?, corrects_id?, settlement_id?, disputed_at?, held_for_review; uq(api_client_id, client_event_id) |
| `drink_stock_movement` | product_id, qty_delta, kind PURCHASE/COUNT_CORRECTION/BREAKAGE, unit_cost_cents?, occurred_at, recorded_by |

The schema stays provisional until M6-0 has examined the existing drinks database [Q30].

### 5.8 Laundry
| Table | Key columns |
|---|---|
| `laundry_machine` | name, kind WASHER/DRYER, location, status ACTIVE/DEFECT/RETIRED, default_program |
| `laundry_price` | machine_kind, price_cents, valid_from |
| `laundry_use` | member_id, machine_id, used_at, program, cycles, unit_price_cents, source QR/MANUAL/ADMIN, submission_id uq, recorded_by, settlement_id?, voided_at/by/reason? |

Form fields are final only after the current laundry list has been checked [Q26].

### 5.9 Later modules
- `curriculum_item`, `curriculum_progress` (Fux, M7-1)
- `rank_change_request` (M7-2)
- `document` (with audience roles and attachment; M7-4)
- `role_limit` (M7-7)

### 5.10 Relations (summary)
```
member 1─0..1 user 1─* external_identity           member 1─* invitation
member 1─* rank_history *─1 rank 1─1 role
member 1─* office_assignment *─1 office 1─1 role   office *─* rank (eligible)
member 1─* group_membership *─1 member_group *─0..1 role
member 1─* member_role *─1 role *─* permission
member 1─* fin_account (one per book) ◄─ debit/credit ─ fin_entry *─1 fin_journal *─1 fin_book
drink_consumption / laundry_use *─0..1 settlement 1─0..1 fin_journal
event *─1 semester; event 1─* participation, cost_item, price, task; event *─* role (audience)
task ─► exactly one of member | group | office
qr_token ─► (target_type, target_id) e.g. laundry_machine
```

### 5.11 Migrating the current tables
| Current | Becomes |
|---|---|
| `user` | Split into `member` + `user`. `role='admin'` becomes a `custom.sysadmin` member_role. `role='resident'` becomes Hausbewohner group membership [Q12]. **Rank cannot be derived**: an admin sets ranks in a post-migration step (bulk import or a form). |
| `chore` | `task`: renamed, `assigned_to_id` → `assignee_member_id`, `is_done` → status, plus the new columns |
| `announcement` | Adds audience roles, `author_member_id`, `pinned_until` |
| `resource` | Adds `is_active` and `kind`. Only `bookings.manage_any` holders create resources. |
| `booking` | Adds CHECK(end > start), status (ACTIVE/CANCELLED) and purpose |

---

## 6. Permissions model

### 6.1 Evaluation
Evaluation runs once per request and is cached in `flask.g`. Dates are evaluated in Europe/Berlin.
```
active_roles(m) = {current rank role}
                ∪ {office.role | office_assignment active today}
                ∪ {group.role  | group_membership active today, group has a role}
                ∪ {member_role active today}
   status ENDED          → {system.former_member} only
   user.is_enabled=false → no login at all
perms(m) = ⋃ role_permission over active_roles        (additive; no deny rules)
```

### 6.2 Enforcement
- `@require_perm('finance.journal.post')` on routes. `@public` marks deliberately open routes (login, `/join`, `/healthz`).
- `can('…')` in Jinja. Navigation and buttons show only what the user may do.
- **Ownership and scope** rules live in one place, `core/authz/policies.py`:
  - `can_edit_task(m, task)` = `tasks.manage`, OR the task is assigned to me, OR I lead the assigned group, OR I hold the assigned Amt.
  - `can_view_saldo(m, other)` = `m == other` OR `finance.ledger.view_all`.
  - `can_view_event(m, event)` = the audience is empty or intersects `active_roles(m)`, OR `events.edit`.
- **Safety nets**:
  - `test_route_coverage.py` fails when a route has neither decorator.
  - `test_permission_matrix.py` checks the seed grants against the endpoints.
  - `flask authz sync` warns about permission codes that are no longer in the code.
  - Seed YAML is applied only by `flask authz seed`, so it never overwrites edits made by admins.

### 6.3 Invariants (in code, all compare *people*)
1. Nobody changes their own rank or creates their own office assignment.
2. A journal that **credits the poster's own member account** needs a cosign from a *different* person who holds `finance.journal.cosign` (Senior or Consenior). This covers the Kassenwart reimbursing their own Auslage.
3. A settlement's poster must be a different person from its submitter, otherwise a cosign is required. This covers one person being both Bierwart and Kassenwart.
4. M7-2 workflow: approver ≠ requester ≠ subject member. At least **two** Ämter hold the approve right, so a request from the Senior, or the Senior's own Philistrierung, can still be decided.
5. Granting or revoking a permission marked `is_sensitive` writes an audit entry and notifies the Senior and the Kassenwart.
6. `custom.sysadmin` (technical admin) never holds finance write permissions. If the admin is also the Kassenwart, they get finance rights from the Amt, and the overlap is shown in the audit view.

### 6.4 Rank and office edge cases
- **Holder changes rank mid-term** (e.g. Bursch → Alter Herr). The "Rang ändern" form lists assignments that become ineligible. The user must choose either *close on date* or *keep as acting* (an audited exception).
- **Holder leaves the club (Austritt).** All open office and group assignments close automatically, so `max_holders` does not block the successor.
- **Vacant Amt.** The Senior (or the Consenior if the Senior's office is vacant) creates an acting assignment. Holders of `offices.assign` can always do this; sysadmin has it for bootstrap.
- **Two Ämter for one person.** Allowed unless the pair is listed in `office_incompatibility` [Q9].
- **Deputies.** Modelled as acting assignments with a date range.
- **Bootstrap.** Sysadmin imports members with their initial ranks (`members.import`) and assigns the first Ämter. After that, ranks change only through holders of `members.rank.change`.

### 6.5 Default permission matrix
This is the proposed seed, editable later.

Legend:
- Columns: SF = Spefux, F = Fux, B = Bursch, AH = Alter Herr │ Sen, Con, Sch = Schriftwart, KW = Kassenwart, FM = Fuxmajor, BW = Bierwart │ Bew = Hausbewohner group, Adm = `custom.sysadmin`.
- Office holders are Burschen, so they also get the B column; office columns show only the *extra* grants.
- `Y` = granted, `-` = not granted, `.` = no extra grant, `?` = club decision needed (until answered, treat as **not granted**).

```
permission                           SF F  B  AH │ Sen Con Sch KW  FM  BW │ Bew Adm
──────────────────────────────────────────────────┼─────────────────────────┼────────
profile.view_own, profile.edit_own   Y  Y  Y  Y  │  .   .   .   .   .   . │  .   .
members.directory.view               ?  Y  Y  Y  │  .   .   .   .   .   . │  .   Y
members.contact.view                 -  -  Y  Y  │  .   .   .   .   Y   . │  .   Y
members.invite                       -  -  -  -  │  Y   Y   ?   .   Y   . │  .   Y
members.import                       -  -  -  -  │  Y   .   ?   .   .   . │  .   Y
members.edit_any                     -  -  -  -  │  Y   .   Y   .   .   . │  .   Y
members.rank.change   (v1 form)      -  -  -  -  │  Y   Y   .   .   ?   . │  .   -
members.exit.record                  -  -  -  -  │  Y   .   Y   .   .   . │  .   -
offices.assign                       -  -  -  -  │  Y   ?   Y   .   .   . │  .   Y
groups.manage                        -  -  -  -  │  Y   Y   .   .   .   . │  .   .
org.semesters.manage                 -  -  -  -  │  Y   .   .   .   .   . │  .   Y
authz.matrix.edit                    -  -  -  -  │  .   .   .   .   .   . │  .   Y
audit.view                           -  -  -  -  │  Y   .   .   ?   .   . │  .   Y
announcements.view                   Y  Y  Y  Y  │  .   .   .   .   .   . │  .   .
announcements.create                 -  -  -  -  │  Y   Y   ?   ?   ?   ? │  .   .
announcements.manage_any             -  -  -  -  │  Y   .   Y   .   .   . │  .   Y
events.view, events.rsvp_own         Y  Y  Y  Y  │  .   .   .   .   .   . │  .   .   (audience-filtered)
events.edit (drafts)                 -  -  -  -  │  Y   Y   Y   .   .   . │  .   .
events.publish                       -  -  -  -  │  Y   .   .   .   .   . │  .   .
events.attendance.record             -  -  -  -  │  Y   Y   .   .   Y   . │  .   .
tasks.view_own, tasks.complete_own   Y  Y  Y  Y  │  .   .   .   .   .   . │  .   .
tasks.view_all                       -  Y  Y  -  │  .   .   .   .   .   . │  .   .
tasks.manage                         -  -  -  -  │  Y   Y   .   .   ?   ? │  .   .
bookings.create_own                  -  Y  Y  Y  │  .   .   .   .   .   . │  .   .
bookings.manage_any (+ resources)    -  -  -  -  │  .   Y   .   .   .   . │  .   Y
finance.saldo.view_own               Y  Y  Y  Y  │  .   .   .   .   .   . │  .   .
finance.ledger.view_all              -  -  -  -  │  .   .   .   Y   .   . │  .   -   (+ Kassenprüfer)
finance.journal.post                 -  -  -  -  │  .   .   .   Y   .   . │  .   -
finance.journal.reverse              -  -  -  -  │  .   .   .   Y   .   . │  .   -
finance.journal.cosign               -  -  -  -  │  Y   Y   .   .   .   . │  .   -
finance.reports.view                 -  -  -  -  │  .   .   .   Y   .   . │  .   -   (+ Kassenprüfer)
finance.accounts.manage              -  -  -  -  │  .   .   .   Y   .   . │  .   -
finance.payment_request.send         -  -  -  -  │  .   .   .   Y   .   . │  .   -
drinks.view_own                      Y  Y  Y  Y  │  .   .   .   .   .   . │  .   .
drinks.tally.record                  -  -  -  -  │  .   ?   .   .   .   Y │  .   .
drinks.catalog.manage, drinks.stock  -  -  -  -  │  .   .   .   .   .   Y │  .   .
drinks.view_all                      -  -  -  -  │  .   .   .   Y   .   Y │  .   .
drinks.settlement.submit             -  -  -  -  │  .   .   .   .   .   Y │  .   .
laundry.use.create_own               -  -  -  -  │  .   .   .   .   .   . │  Y   .
laundry.view_own                     -  -  -  -  │  .   .   .   .   .   . │  Y   .
laundry.view_all                     -  -  -  -  │  .   Y   .   Y   .   . │  .   .
laundry.machines.manage              -  -  -  -  │  .   Y   .   .   .   . │  .   Y
laundry.settlement.submit            -  -  -  -  │  .   Y   .   .   .   . │  .   .
fux.curriculum.view_own              Y  Y  -  -  │  .   .   .   .   .   . │  .   .
fux.progress.view_all                -  -  Y  -  │  Y   .   .   .   Y   . │  .   .
fux.progress.signoff                 -  -  -  -  │  .   .   .   .   Y   . │  .   .
documents.view                       Y  Y  Y  Y  │  .   .   .   .   .   . │  .   .   (audience-filtered)
documents.manage                     -  -  -  -  │  Y   .   Y   .   .   . │  .   .
system.admin                         -  -  -  -  │  .   .   .   .   .   . │  .   Y
drinks.consumption.ingest            API device tokens only, never a human role
```
- A Saldo is **never** visible to other members. Only the member and holders of `finance.ledger.view_all` see it.
- The Kassenprüfer office (added in M3-1, up to two holders, Bursch or Alter Herr, incompatible with Kassenwart) gets `finance.ledger.view_all`, `finance.reports.view` and `finance.journal.cosign`, read-only.

### 6.6 Limits per rank (placeholders only, built in M7-7 after [Q35])
v1 has **no** per-rank limits, only global config constants (e.g. `LAUNDRY_MAX_USES_PER_DAY`). The values below are illustrative and **not agreed**:

| Limit key (placeholder) | SF | F | B | AH |
|---|---|---|---|---|
| booking.max_hours | 0 | 4 | 24 | 24 |
| event.max_guests | 0 | 1 | 2 | 3 |
| saldo.warning_below_cents | — | −5000 | −10000 | −10000 |
| tasks.exempt | — | — | — | yes |

Whether a negative Saldo should only warn or should block something is a club policy decision [Q23]. Recommendation: warn only.

---

## 7. Integrations

### 7.1 Google

**User goal:** members "see our Google Calendar and get notifications on events".

**Recommended, staged approach**
1. **G1 – ICS feeds (M5-6).** A club feed plus a personal secret-token feed. No Google dependency; works with Google, Apple and Outlook.
2. **G2 – Club calendar push (M5-8).** A club-owned Google Calendar. The app writes events one way through the Calendar API using a service account the calendar is shared with. Events are tagged with a private extended property, and `google_event_id` and the etag are stored.
3. **G3 – Sharing automation (M5-9).** Members opt in with their Google address. The app adds a `reader` ACL rule and removes it on opt-out or Austritt.
4. **G4 – App email reminders (M5-10).** These cover the notification gap. A help page explains how to turn on default notifications for the club calendar once.
5. **G5 – Sign in with Google (M5-11, optional).** OIDC with `openid email profile` only. Linking is explicit, `sub` is the key, and accounts are never auto-created.
6. **Per-user OAuth** (writing into each member's own calendar, with app-controlled reminders) only if the user decides notifications must be fully automatic [Q28]. It requires Google app verification.

**Alternatives compared**
| Option | Members get | Notifications | Verification | Workspace needed | Effort |
|---|---|---|---|---|---|
| A. ICS feed | Read-only calendar | Unreliable; Google probably ignores alarms in URL calendars **[verify]** → app emails | none | no | low |
| B. Club calendar + service account + ACL sharing (**recommended with A**) | Native calendar with near-instant updates | Each member sets defaults once; the app cannot set reminders for others **[verify]** | none for the service account | no **[verify]** | medium |
| B′. As B, but members invited as attendees | Invitations in their own calendar | Their own defaults | none | **yes** (reportedly service accounts cannot invite without domain-wide delegation **[verify]**) | medium |
| C. Per-user OAuth | Events in their own calendar | App-controlled | Calendar scopes probably "sensitive": verification, possible user cap and 7-day refresh tokens in Testing mode **[verify all]** | no | high |
| D. Embedded iframe | View inside the app only | none | none | no | trivial; needs a public calendar, which leaks the program |

**Fallback if B fails on a consumer Gmail account:** the club account grants the app one offline OAuth token for the club calendar only. This may run into the same Testing-mode and verification limits as C **[verify]**. If the club has Workspace, B′ becomes possible [Q27].

**Points to verify in spike M5-7, before any Google code:**
- Which ACL role the service account needs to manage sharing (probably "Make changes and manage sharing" = `owner`).
- Whether a calendar shared with a consumer Gmail address appears automatically or must be added by the recipient.
- Attendee insert without Workspace (expected to fail).
- App-chosen event ids (base32hex) for idempotent retries.
- How quickly Google refreshes subscribed ICS URLs.
- Calendar API quotas at this volume.
- Consent-screen and brand-verification rules for OIDC.
- Whether a Google organisation policy blocks service-account key creation.

**Prerequisites:**
- Final domain with HTTPS (M8-1).
- Club-owned Google account, Cloud project and key, kept outside the repo with mode 0600, held by at least two people and rotated at handover (M8-6).
- Privacy notice that mentions the transfer to Google (M8-7).
- All Google features are opt-in and hidden when `GOOGLE_*` config is missing.

### 7.2 Excel / Kasse
- **Decision (D21, accepted 2026-10-13 [Q19]).** The owner does not want the Excel file changed and asked for "a smart new system with a structure similar to the Excel". So **Community is the source of truth** with the Excel's structure (see `docs/spikes/m3-0-kasse-excel.md`); the Excel file is only ever read:
  - A web Saldenliste grid with htmx inline actions.
  - A journal view laid out like the Excel: one row per posting, one Soll/Haben column pair per Kasse.
  - An **xlsx export** in the familiar layout: Journal, Kontokarten (one column per person, like the sheet "Alle"), cost centres ("Versch"), AH-Kontingent ("Konti"), Bilanz/GuV, Kassenzählung; formatted tables, frozen header, autofilter and € format (openpyxl).
  - Reason: the Kassenwart changes every semester, and guarded forms with an audit trail survive a handover better than a spreadsheet anyone can break.
  - A read-only Google Sheets mirror and an interim improved Excel template stay in the backlog (§11).
- **One-time import (M3-0 spike → M3-6):**
  1. The user provides an anonymised copy. The analysis documents sheets, columns and sign convention, and whether the laundry list is a sheet in the same file.
  2. The import command runs as a **dry run by default**.
  3. Amounts are converted with `Decimal(str(v)).quantize(Decimal("0.01"))` → cents, never `int(v*100)`, because `12.29*100` truncates to `1228`.
  4. `openpyxl` with `data_only=True` returns `None` for formulas unless Excel saved the file last. Merged cells and free-text names need handling.
  5. Names are matched to members through a mapping table. The reconciliation report compares each member's amount **and sign** against the sheet's own Saldo, plus the grand total.
  6. The Kassenwart signs off. The import creates one OPENING journal per book against EQUITY "Übertrag aus Excel per <Datum>", and the original file is archived.
  7. One month of parallel running, then the Excel file becomes read-only.
- **"Pays payments".** Supplier invoices (D EXPENSE / C BANK) and Auslagen (D EXPENSE / C MEMBER) are possible because the ledger is double-entry (M3-8). Bank CSV/CAMT import with an unmatched-payments queue is backlog **[verify bank formats]**.
- **"Writes people what they need to pay".** Zahlungsaufforderung emails (M3-7) depend on email sending (M1-6, M8-6).
- **Go-live gate:** nightly encrypted off-site backups **and one performed restore** (M8-3) before real money goes in (M3-9).

### 7.3 Bierwart API contract (v1)

The contract is published early as `docs/api/openapi.yaml` (M6-2) so the user can build the counter app in parallel. It is final only after M6-0 answers whether the counter app is separate or a Community module [Q31].

**Auth**
- Header `Authorization: Bearer cmt_<random>`. Only the SHA-256 hash and a display prefix are stored. The token is shown once and can be revoked individually.
- **Device tokens** (`owner_member_id` NULL, e.g. "Theke-Tablet") can have the scopes `products:read`, `members:read`, `drinks:write` and `stock:write`.
- **Personal tokens** (the owner is a member) can also have `drinks:settle`. That scope works only while the owner currently holds `drinks.settlement.submit`; this is checked on every call.
- The API is CSRF-exempt (no cookies), JSON only, and returns errors as `application/problem+json` (401, 403, 404, 422).

**Endpoints**
| Method & path | Scope | Purpose / response |
|---|---|---|
| `GET /api/v1/products` | products:read | Active products with current `price_cents`. Community owns prices. |
| `GET /api/v1/members?status=active` | members:read | `public_id` and display name only (data minimisation; no emails) |
| `POST /api/v1/drinks/consumptions` | drinks:write | Batch of ≤ 500 events. Per-item result: `accepted` / `duplicate` / `held` / `rejected` + reason |
| `POST /api/v1/drinks/stock-counts` | stock:write | Inventory count; creates a correction movement |
| `POST /api/v1/drinks/settlements` | drinks:settle (personal) | `{period_to, note}` → 201 DRAFT settlement with totals. Submitting to the Kassenwart happens in the app. |
| `GET /api/v1/drinks/settlements/{id}` | drinks:settle | Status DRAFT / SUBMITTED / POSTED |

**Consumption item** (illustrative):
```json
{"client_event_id":"6f1c…","member_id":"<public_id>","product_id":3,"quantity":2,
 "occurred_at":"2026-10-11T21:14:00+02:00","corrects_client_event_id":null}
```

**Rules**
- **Idempotency:** unique `(api_client_id, client_event_id)`. A retry returns `duplicate`. The same id with different fields returns `rejected: client_event_id_reused`. Offline tablets can retry safely.
- **Pricing:** the server applies the price valid at `occurred_at` and snapshots it (D11).
- **Time:** ISO-8601 with offset, stored as UTC. Events with `occurred_at` more than 5 minutes in the future are rejected. Events dated inside an already-POSTED settlement period are `held` for Bierwart review.
- **Rejection reasons:** `unknown_member`, `inactive_member`, `unknown_product`, `inactive_product`, `invalid_quantity`, `occurred_at_in_future`, `client_event_id_reused`.
- **Corrections:** a negative quantity referencing `corrects_client_event_id`. Posted data is never edited.
- **Abuse at the bar:** members see their own tallies and can mark them disputed before settlement; the Bierwart reviews.
- **Versioning:** only additive changes inside v1. Breaking changes go to `/api/v2` with `Deprecation`/`Sunset` headers on v1.
- **Rate limiting:** a simple per-token counter in the DB if needed. Flask-Limiter with in-memory storage counts per worker **[verify]**.
- **Later (backlog):** signed outbound webhooks (`product.updated`, `member.updated`) so the counter app does not have to poll.

### 7.4 Notifications & email
- **Channels:**
  - v1: in-app bell and email.
  - Calendar events: through G2/G3.
  - Later: Web Push/PWA (iOS only for home-screen PWAs, 16.4+ **[verify]**) and Telegram. WhatsApp Business is costly and complex.
- **Sending:**
  - SMTP adapter with a console backend in dev.
  - Preferably the club domain's mail host or an EU transactional provider with SPF/DKIM and a data-processing agreement (AVV).
  - Gmail consumer accounts have daily send limits (around 500/day **[verify]**) and need an app password.
  - Email is a hidden dependency of invites, password reset, payment requests and reminders, so set it up early (M1-6, M8-6).
- **Notification types:**
  - invite / welcome
  - password_reset (mandatory)
  - task_assigned / task_due / task_overdue
  - event_reminder
  - payment_request (mandatory) / saldo_below_threshold
  - drinks_settlement_submitted (to the Kassenwart) / posted (to members)
  - low_stock (to the Bierwart)
  - laundry_done (optional)
  - office_handover
  - sensitive_permission_changed (to the Senior and the Kassenwart)
  - weekly digest ("Wochenübersicht": tasks, events, Saldo)
- **Preferences:** per type and channel, with quiet hours. Mandatory types cannot be turned off.
- **Scheduling:**
  - A systemd timer runs `flask jobs tick` every 1–5 minutes. It enqueues due reminders and rotations, then runs pending jobs with retry and exponential backoff.
  - Duplicates are prevented by `job.dedupe_key`, e.g. `event_reminder:<event>:<member>:<time>`.
  - Never APScheduler inside gunicorn.
  - Upgrade path: RQ or Celery behind the same `jobs.enqueue()` call.

---

## 8. Delivery plan

### 8.1 Conventions (Definition of Done for every task)
- One concern per PR, roughly 400 changed lines or fewer excluding migrations. Branch and commit messages start with the task ID (`M0-9: safe next redirect`).
- Tests are required:
  - service unit tests;
  - route tests including anonymous and forbidden cases;
  - a permission-matrix row for each new permission;
  - a regression test for every bug fix.
- Models changed → hand-reviewed migration. **Only one migration-bearing PR is merged at a time.** On rebase, regenerate `down_revision`. CI fails if there are multiple Alembic heads.
- New routes carry `@require_perm` or `@public`. New permission codes go in `permissions.py`, `seed/authz.yaml` and §6.5.
- German UI strings; English code and comments. No secrets in the repo.
- Partially built features go behind a config flag. A deviation from §4.1 is recorded in `docs/decisions.md`.

**Size:**
- S: about one session, at most about 200 lines.
- M: a few sessions.
- L: one to two weeks at hobby pace.

**"Parallel-safe with"** means the tasks touch different modules and can be developed at the same time; the migration rule above still applies. `M2-*` means every task in M2.

### 8.2 Traceability: known defects → tasks
| Defect | Task |
|---|---|
| K1 Indentation: `create_app()` returns `True` (line 72), so the app never starts and `announcement_new` body is dead code | M0-4 |
| K2 `admin_required()` always returns False | M0-4 |
| K3 `.env` committed (in history since b2850ff; **repo is public**), heredoc lines in it, secrets must rotate | M0-1, M0-2 |
| K4 `chore_edit`: unknown id shows an empty form on GET and crashes on POST; anyone edits any chore | M0-10 |
| K5 No chore create route; `ChoreForm` lacks `assigned_to` | M0-10 |
| K6 Flask-SQLAlchemy missing from requirements | M0-3 |
| K7 Weak defaults `SECRET_KEY='dev-secret'`, `INVITE_CODE='invite'` | M0-5 |
| K8 Bookings lack end > start; `.idea/` committed; no migrations or `create_all`; no tests; shared invite code; admin via `ADMIN_EMAIL` | M0-11, M0-2, M0-6, M0-7, M0-5 → M1-7, M0-4 → M1-3 |
| N1 **Admin takeover:** leaked `ADMIN_EMAIL` plus invite code, unverified emails | M0-1, M0-4 |
| N2 No WSGI entry point; `register()` re-imports `Config` | M0-4 |
| N3 `/` shows announcements to anonymous visitors | M0-9 |
| N4–N7 `?next=` ignored; logout via GET; no throttling, 6-character passwords; email case not normalised | M0-9 |
| N8 Free-text resource creation; resource saved *before* the overlap check; overlap race; emails shown in the bookings list | M0-11 |
| N9 `ChoreForm.due_date` lacks `Optional()` (empty date fails validation); edit link shown to everyone; assignee never shown | M0-10 |
| N10 Hardcoded URLs and brand "WG-Haus" in templates | M0-12 |
| N11 `datetime.utcnow` deprecated (Python 3.12+), naive datetimes | M0-12, M0-13 |
| N12 `FLASK_ENV` ignored by Flask ≥ 2.3 **[verify]**; `PREFERRED_URL_SCHEME`/`TZ` never read; no `BASE_URL`; `ProxyFix` always on | M0-5 |
| N13 Tailwind Play CDN (not for production); htmx loaded from unpkg but unused | M1-11 |
| N14 Empty `templates/bookings/create.html` and `static/app.css`; unused imports; mixed `Model.query`/`select()` style | M0-12, M0-13 |
| N15 `nulls_last()` needs SQLite ≥ 3.30 on the server **[verify]** | M0-6 |
| N16 No password reset | M1-8 |
| N17 State of the server at `~/community` unknown | M0-1 |

### 8.3 M0 — Rescue & foundations → Release **R0 "Läuft wieder"**
**Status (2026-10-11):** M0-2 … M0-13 are implemented in Wegito/Community#1, one commit per task. Deviations are listed in `docs/decisions.md`. **M0-1 is still open**: it is server work for the owner, with a checklist in `README.md`.

**Ordering rule:** M0-1 (rotation and server audit) **must be finished before deploying anything that makes the app start** (M0-4 or later).

| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M0-1 | Secret rotation & server audit (ops, no PR) | Find what runs in `~/community`: commit, DB, users, who has access. Rotate `SECRET_KEY` and `INVITE_CODE` on every host. Check whether an account with the leaked `ADMIN_EMAIL` exists and who created it; remove unknown users or admins. Decide on purging history; it is optional because forks and clones keep copies, and rotation is what protects. | — | S | all of M0 |
| M0-2 | Repo hygiene | `.gitignore` (`.env`, `.idea/`, `*.db`, `instance/`, `.venv/`, `__pycache__/`); untrack `.env` and `.idea/`; `.env.example` with key names only (no heredoc lines); enable GitHub secret scanning and push protection | — | S | M0-1, M0-3, M0-4 |
| M0-3 | Dependencies & Python | Add Flask-SQLAlchemy (3.1.x, compatible with SQLAlchemy 2.0.x **[verify]**) and Flask-Migrate; add `requirements-dev.txt`; use the server's Python (3.12 or 3.13 [Q2]) and verify all pins install on it | — | S | M0-1, M0-2 |
| M0-4 | Make it start + close the admin hole | Fix indentation so `create_app()` returns the app; move `announcement_new` body back inside; turn `admin_required` into a working decorator; **remove `ADMIN_EMAIL` auto-promotion**; add `flask create-admin`; `create_app(config=None)`; read `current_app.config`; add `wsgi.py`. **Deploy gate: M0-1.** | M0-3 | S | M0-1, M0-2 |
| M0-5 | Config hardening | Dev/Test/Prod classes. Prod refuses a missing or default `SECRET_KEY`. No `INVITE_CODE` default (registration closed when unset). `BASE_URL`; `PREFERRED_URL_SCHEME` read. Session and remember cookie flags (Secure, HttpOnly, SameSite=Lax). `TRUSTED_PROXY_HOPS` controls ProxyFix (0 = off). Drop `FLASK_ENV`/`TZ` from the example. | M0-4 | S | M0-6 |
| M0-6 | Migrations baseline & DB settings | Flask-Migrate with the metadata naming convention, `render_as_batch`, a baseline of the 5 tables; SQLite pragmas WAL, `busy_timeout`, `foreign_keys`; document `flask db upgrade`; check the server's SQLite version; if a production DB exists, compare it then `flask db stamp` **[verify on server]** | M0-4 | S | M0-5 |
| M0-7 | Test harness | pytest, TestConfig, temporary DB built *through the migrations*, small factory helpers, login helper, smoke test per route (status codes, anonymous redirects) | M0-5, M0-6 | M | M0-2 |
| M0-8 | CI | GitHub Actions: ruff lint and format, pytest with coverage, upgrade from an empty DB, multiple-heads check, drift check (`flask db check` **[verify version]**), pip-audit; Dependabot; protect `main` | M0-7 | S | M0-9 … M0-12 |
| M0-9 | Auth fixes | Lowercase and trim emails (migration lowercases existing rows; fails loudly on duplicates); safe `next`; remember-me; logout via POST with CSRF (nav button); `login_required` on `/`; minimum password length 10; DB throttling (`failed_logins`, `locked_until`); generic failure message | M0-7 | M | M0-10, M0-11 |
| M0-10 | Chores fixes | `db.get_or_404` on GET and POST; only the assignee or an admin edits; `/chores/new`; `assigned_to` SelectField (`coerce=int`, blank allowed); `due_date` `Optional()`; show the assignee; edit link only when allowed | M0-7 | M | M0-9, M0-11, M0-12 |
| M0-11 | Bookings fixes | Form validator and DB CHECK `end > start`; resource chosen from an admin-managed select (no free-text creation, so no junk resources); names instead of emails; cancel own booking; overlap check and insert in one serialised write transaction **[verify pysqlite transaction behaviour]** | M0-7 | S | M0-9, M0-10, M0-12 |
| M0-12 | Template & time hygiene | `url_for` everywhere (prerequisite for blueprints); `CLUB_NAME` setting; 403/404/500 pages; timezone-aware `utcnow()` helper; Berlin display filter; delete empty files and unused imports | M0-7 | S | M0-10, M0-11 (merge after M0-9; both touch `base.html`) |
| M0-13 | Package layout + blueprints | Move into the `community/` package (§4.2) with `extensions.py`, `core/db.py` (naming convention, `UTCDateTime`, timestamp mixin), `core/time.py`, `core/money.py`. Blueprints `auth`, `announcements`, `tasks` (chores), `bookings`; endpoints like `auth.login`; standardise on `select()`. One PR; other PRs are frozen while it lands. | M0-8 … M0-12 | M | none (freeze) |

**Exit criteria for M0:**
- The app starts under gunicorn and every route is reachable.
- CI is green.
- No secrets are tracked in git, and every defect in §8.2 assigned to M0 has a regression test.
- Secrets have been rotated and the server audited.

### 8.4 M1 — Identity, org & permissions → part of **R1 "Mitglieder & Profil"**
| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M1-1 | Member/User split | `member` table; `user` becomes login-only; data migration creates one member per user; `public_id`, `payment_ref` | M0-13 | M | M1-2, M1-6, M1-11 |
| M1-2 | Audit log | `audit_log` and `audit.record()`; successful and failed logins, admin actions; viewer (`audit.view`); retention setting | M0-13 | S | M1-1, M1-6, M1-11 |
| M1-3 | Authz engine | Permission registry and `flask authz sync`; role, role_permission, member_role; `@require_perm`/`@public`; `can()`; `policies.py`; cache in `g`; `flask authz seed` from YAML; `custom.sysadmin` replaces `Role.ADMIN` (migration); navigation filtered by permission; route-coverage and matrix tests | M1-1, M1-2 | L | M1-6, M1-11 |
| M1-4 | Semester + ranks | Semester CRUD; rank table (labels are data) with roles; rank_history with partial unique index; "Rang ändern" form (date, convent_ref, note; never your own rank; audited; `members.rank.change`) | M1-3 | M | M1-6, M1-8, M1-11 |
| M1-5 | Ämter + groups | Office table with eligible ranks, incompatibilities, `max_holders`, `description_md`; date-range assignments (open-ended, acting); groups and memberships (Hausbewohner residence group with role; task groups); office and group roles added to the evaluation; rank-change and exit handling of open assignments (§6.4); assignment UI | M1-4 | M | M1-6, M1-7, M1-9, M1-10, M1-11 |
| M1-6 | Jobs, mail, notifications core | `job` table; `flask jobs tick` with retry, backoff and dedupe; SMTP and console mail adapter; German mail templates; `notification` with in-app bell; `notification_pref` | M0-13 | M | M1-1 … M1-5 |
| M1-7 | Personal invites | Invitation table (hashed token; link 14 days, QR 24 hours; single use; bound to a pre-created member); `/join/<token>`; inviter UI (`members.invite`, allowed ranks only); revoke and resend; delivery by link, QR or email (after M1-6); **remove shared `INVITE_CODE` registration** | M1-4 (M1-6 for email) | M | M1-8, M1-9, M1-10 |
| M1-8 | Password reset + email verification | Signed, time-limited tokens; bump `session_version` on reset; verify changed emails | M1-1, M1-6 | S | M1-4, M1-5, M1-9, M1-10 |
| M1-9 | Bulk member import | CSV/XLSX upload → preview with validation (duplicates, bad emails, unknown ranks) → commit; members without login; optional invite batch via jobs; initial ranks set here (bootstrap) | M1-4 | M | M1-7, M1-8, M1-10 |
| M1-10 | Directory + own profile | `/m/<public_id>` (login required); fields filtered by `members.directory.view`/`members.contact.view` and member flags; members edit their own data; no Saldo shown | M1-4 | M | M1-5 … M1-9 |
| M1-11 | Frontend pipeline | Tailwind standalone CLI build; local pinned htmx 1.9.x with CSRF header; mobile-first layout; macros for forms, tables and buttons | M0-13 | M | M1-1, M1-2, M1-6 |
| M1-12 | Member lifecycle admin | Deactivate and reactivate login; Austritt (ENDED → `system.former_member`, close assignments, notify the Kassenwart via job); "log out everywhere" via `session_version` in the remember token (Flask-Login alternative-token pattern **[verify]**) | M1-5, M1-6 | M | M1-7 … M1-10 |

### 8.5 M2 — Profile & tasks → **R1** (M2-1, M2-2, M2-6) and **R4 "Aufgaben & Putzplan"** (the rest)
| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M2-1 | Profile shell `/me` | Header (name, Biername, rank, Ämter with term, groups); `WIDGETS` list (endpoint, permission, order); each widget is its own htmx partial, so a failing widget cannot break the page; settings page; `/me` is the landing page after login | M1-5, M1-11 | M | M2-8, M3-1, M4-1, M4-2, M5-1 |
| M2-2 | Chore → Task | Migration to `task`; assignee exactly one of member, group or Amt; statuses; source; `can_edit_task`; "Meine Aufgaben" widget (mine, my groups, my Ämter, plus my Pflicht events once M5-3 exists) | M1-5, M2-1 | M | M3-*, M4-1 … M4-3 |
| M2-3 | Consenior workspace | Create and assign tasks; semester task groups; verify done; overdue and unassigned view; Consenior tile | M2-2 | M | M3-*, M4-* |
| M2-4 | Putzplan rotation + templates | `task_template`, `task_rotation`; generation via `jobs tick` | M2-3, M1-6 | M | M2-5, M2-6, M2-7 |
| M2-5 | Task notifications | Assigned, due-soon and overdue (email and in-app); optional Monday digest | M2-2, M1-6 | S | M2-4, M2-6, M2-7 |
| M2-6 | Amt pages + office tiles | One page per Amt (job description, current and previous holders); tiles on `/me` for holders | M2-1 | S | M2-3 … M2-5 |
| M2-7 | Einkaufsliste | Shopping list as task category "Einkauf" with quick-add; Consenior and Bierwart view | M2-3 | S | M2-4 … M2-6 |
| M2-8 | Announcements upgrade | Audience roles, author member, `announcements.create` per matrix [Q16], pinning, `/me` widget | M1-3 | S | M2-1 … M2-7 |

### 8.6 M3 — Finance (Kasse) → **R3 "Kasse & Saldo"**
| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M3-0 | Excel analysis (spike) ✅ | Done: `docs/spikes/m3-0-kasse-excel.md` (structure only; the file stays out of the repo) | user input | S | everything |
| M3-1 | Ledger core ✅ | Books, accounts, categories, journal, entries; `post_journal()`, `reverse()`, `saldo()`, `statement()`; idempotency keys; immutability guard; person-based cosign rule; audit; money parse and format; invariant tests (≥ 90 % coverage) | M1-2, M1-3, M1-5 | L | M2-*, M4-1 … M4-4, M5-1 … M5-4 |
| M3-2 | Saldo widget + Kontoauszug ✅ | Posted Saldo per book (no total across legally separate books), latest entries, payment details (IBAN, `payment_ref`); statement page | M3-1, M2-1 | S | M3-3 … M3-8 |
| M3-3 | Kassenwart workspace (first part ✅: Kasse overview with Kontokarten, Kassenbuch with filters, booking form with presets Zahlung/Belastung/Auslage, reversal, accounts and cost centres; still open: collective bank-statement payments) | Saldenliste grid (members × books, filter by rank and status); htmx inline actions: record payment, single charge, reversal with reason; member payments from one bank statement in one collective journal (`GS` Beleg); Beleg numbers per series (G, K, B, GS); statements; Kassenwart tile | M3-1 | L | M3-2, M3-5, M3-6, M3-7 |
| M3-4 | Bulk charges + splits ✅ (automatic monthly posting via `jobs tick` still open) | Bulk charge by rank or group (Semesterbeitrag); recurring monthly charges per group (Aktivenbeiträge, Internetbeiträge with price levels); equal split (D20), fixed price per rank or an amount per person (event shares); a Kasse can pay part of a share for a rank (the Fuxenkasse for Füxe); mandatory preview before posting | M3-3 | M | M3-5 … M3-8 |
| M3-5 | xlsx export ("better Excel") | Kassenbuch, Saldenliste, statements, semester report (openpyxl, formatted) | M3-1 | S | M3-2 … M3-4, M3-6 … M3-8 |
| M3-6 | Opening-balance import | Procedure in §7.2: dry run, Decimal conversion, mapping, sign check, reconciliation report, sign-off, OPENING journals | M3-0, M3-1, M1-9 | M | M3-2 … M3-5, M3-7 |
| M3-7 | Zahlungsaufforderung | Filter by Saldo below X → preview → personal emails via jobs (statement, IBAN, Verwendungszweck); reminders after N days (can be switched off); optional GiroCode **[verify EPC spec + library]** | M3-1, M1-6 | M | M3-4 … M3-6, M3-8 |
| M3-8 | Club payments & Auslagen | Manual journals: supplier invoices (EXPENSE/BANK), member Auslage credit and payout, cash book | M3-3 | S | M3-4 … M3-7 |
| M3-10 | Semesterabschluss | Bilanz and GuV from the journal; AH-Kontingent report (budget, spending by group, remainder); cash count by denomination that books the difference; carry balances into the next semester; all as sheets in the xlsx export | M3-1, M3-5 | M | M3-7, M3-8 |
| M3-9 | Finance go-live (ops) | **Gate: M8-3 restore drill passed.** Import, one month of parallel running, archive Excel read-only. | M3-3, M3-6, M8-3 | S | — |

### 8.7 M4 — Laundry via QR → **R2 "Wäsche per QR"** (next after M1)

**Status (2026-10-12):** M4-1 … M4-4 implemented (QR core, machines and prices, the form, lists, monthly statement with Excel export, import of the old list). M4-5 (posting to the Saldo) waits for M3; M4-6 (machine free / done reminder) is open. Details in `docs/decisions.md`.

**Facts from the current Google Form "Waschliste" (analysed 2026-10-12; the file holds personal data and stays out of the repo):**
- Questions: *Waschen* (required: "eine" or "beide" machines), *Trocknen* (yes), *Trocknen Modus* (8 dryer programs: Extra Trocken, Schranktrocken +, Bügeltrocken K, Sehr Trocken, Schranktrocken, Bügeltrocken P, Warm 20 min, Kalt 20 min), *Transport* (free text: which tub/box, e.g. "blaugraue Wanne"), plus email, a name ("Timer") and room. With a login, name, email and room are known and not asked again.
- Billing sheet: one unit costs 0,25 € for members (aktiv/inaktiv) and 1,50 € for "Hausbewohner" (residents who are not members). The owner confirmed that **washing and drying both cost money**: one wash per machine ("beide" = 2) and one per dryer run. The old monthly counts only matched the dryer runs.
- About 336 entries since January 2026; 84 have no email and cannot be assigned.
- Only the laundry form moves away from Google; Google stays planned for the calendar (M5).

| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M4-1 | QR core | `qr_token`; `/q/<token>` resolved through `QR_HANDLERS`; friendly 404 with manual choice; printable A4 sheet with label and printed domain (SVG; segno or qrcode **[verify]**); rotate button. **Print only after M8-1.** | M0-9, M1-3 | M | M2-*, M3-* |
| M4-2 | Laundry machines | Machines (incl. DEFECT status), price history, admin CRUD | M1-5 | S | M4-1, M2-*, M3-* |
| M4-3 | QR laundry form | Flow §3.2: pre-filled GET, POST+CSRF only, `submission_id`, "nicht du?", 5-minute undo, duplicate warning, daily cap (config), "Defekt melden" → task (once M2-2 exists) | M4-1, M4-2 | M | M2-*, M3-* |
| M4-4 | Laundry lists | Own-history widget; full list for `laundry.view_all`; export; import of the old laundry list if present in the M3-0 file | M4-3, M2-1 | S | M3-* |
| M4-5 | Laundry billing (flag) | Creates the `settlement` table if it does not exist yet; monthly DRAFT → SUBMITTED → POSTED; one entry per member from snapshotted prices; provisional amount on `/me`. Only if [Q26] says laundry is billed. | M4-3, M3-3 | M | M5-*, M6-1 … M6-4 |
| M4-6 | Machine status + done reminder (optional) | "Ist die Maschine frei?" page; reminder job | M4-3, M1-6 | S | M4-4, M4-5 |

### 8.8 M5 — Events, program & calendar → **R5 "Semesterprogramm & Kalender"**
| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M5-1 | Events core | Event types; events (DRAFT/PUBLISHED/CANCELLED, Pflicht flag, responsible Amt); audience by role; list and month view | M1-5 | M | M3-*, M4-* |
| M5-2 | Semesterprogramm editor | Senior drafts (visible to `events.edit`), publishes (`events.publish`), copies from last semester; print/PDF view via print CSS | M5-1 | M | M5-3, M5-6, M5-7 |
| M5-3 | RSVP + attendance | RSVP, excuses, attendance recording; "Nächste Veranstaltungen" widget with Pflicht marker | M5-1, M2-1 | M | M5-2, M5-6 |
| M5-4 | Event tasks | Consenior links tasks to events; task templates per event type | M5-1, M2-4 | S | M5-2, M5-3, M5-6 |
| M5-5 | Event cost settlement | Cost items (payer credited as Auslage); price by rank or equal split; preview → journal; re-settle = reverse the whole journal and settle again | M5-3, M3-4 | M | M5-6 … M5-10 |
| M5-6 | ICS feeds | Club feed and personal secret-token feed (audience-filtered, rotatable, stored hashed); "Kalender abonnieren" help. Library such as `icalendar` **[verify]**. | M5-1 | S | M5-2 … M5-5 |
| M5-7 | Google spike (S1 + S2) | Every point in the §7.1 verify list, using a throwaway calendar; results in `docs/spikes/` | M8-6 | S | everything |
| M5-8 | Club calendar push | One-way sync through jobs; stored ids and etags; idempotent retries; cancellations propagate; sync status shown to the Senior | M5-2, M5-7, M1-6 | L | M5-3 … M5-6, M5-10 |
| M5-9 | Calendar sharing automation | Opt-in Google address; ACL grant and revoke (opt-in, opt-out, Austritt); `acl_rule_id`; "how to enable notifications" help | M5-8, M1-12, M8-7 | M | M5-10, M5-11 |
| M5-10 | Event email reminders | Reminder N hours before (per preferences); digest integration | M5-1, M1-6 | S | M5-2 … M5-9 |
| M5-11 | Sign in with Google (optional) | OIDC per §7.1, e.g. Authlib **[verify current API]**; explicit linking; validated `next` kept across the redirect; `state` checked | M1-7, M8-1, M8-6, M8-7 | M | M5-1 … M5-10 |

### 8.9 M6 — Drinks (Bierwart) → **R6 "Getränke + API"**
| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M6-0 | Drinks discovery | Examine the existing drinks database "behind the scenes" [Q30]; decide import, integrate or replace; counter app separate or a module; devices; member identification [Q31] | user input | S | everything |
| M6-1 | Catalog + prices | Products and price history; Bierwart UI | M6-0, M1-5 | S | M4-*, M5-* |
| M6-2 | API contract | `docs/api/openapi.yaml` per §7.3, published early | M6-1 | S | M6-3, M6-4 |
| M6-3 | Consumption + Strichliste | Sheet per evening; grid entry by the Bierwart (or delegates [Q32]); server pricing; correction rows; members see their own tallies and can dispute them | M6-1 | M | M6-2, M6-4 |
| M6-4 | Stock | Purchases; inventory counts (Schwund visible); low stock → task and notification | M6-1, M2-2, M1-6 | M | M6-2, M6-3 |
| M6-5 | Settlement to the Kassenwart | "Abrechnung bis <Datum>" claims items with one `UPDATE … WHERE settlement_id IS NULL AND occurred_at <= :period_to` in one transaction; one open settlement per module and book; DRAFT → SUBMITTED → POSTED (rule §6.3-3); one entry per member; late items go to the next settlement; REJECTED returns to DRAFT | M6-3, M3-3 | M | M6-6, M6-7, M6-8 |
| M6-6 | API v1 | `api_client` (hashed tokens, scopes, revoke); token UI for the Bierwart and sysadmin; endpoints and rules per §7.3 | M6-2, M6-3 | M | M6-4, M6-5, M6-8 |
| M6-7 | Legacy drinks import | Depends on the outcome of M6-0 | M6-0, M6-3 | S–M | M6-4 … M6-6 |
| M6-8 | Drinks widget + reports | Own consumption this period with a cost estimate labelled *vorläufig*; reports on consumption, Schwund and stock value | M6-3, M6-4, M2-1 | S | M6-5 … M6-7 |
| M6-9 | Fridge QR self-service (optional) | A `/q/` handler for "+1 drink", only if the counter app will not cover it | M4-1, M6-3 | S | M6-4 … M6-8 |

### 8.10 M7 — Office workflows & documents → **R7 "Ämter-Workflows"**
| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M7-1 | Fux curriculum | Curriculum items, progress per Fux, Fuxmajor sign-off, Fux widget and Fuxmajor tile [Q34] | M1-5, M2-1 | M | M7-3 … M7-6 |
| M7-2 | Rank-change workflow | Request → approval (rule §6.3-4); prerequisites shown as information (curriculum complete, Saldo); convent ref; notifications | M1-4, M7-1 | M | M7-3 … M7-6 |
| M7-3 | Semester rollover + Amtsübergabe | Wizard: next semester, assign Ämter, generate office-duty tasks, handover checklist (open items, who holds which credentials), Kassenwart closing export | M1-5, M2-4, M3-5 | M | M7-1, M7-2, M7-4 |
| M7-4 | Documents | Attachment storage; library with audience (Satzung, Comment, Protokolle, program PDF) | M1-3 | S | M7-1 … M7-3 |
| M7-5 | Schriftwart | Scope after [Q10] | M7-4 | M | M7-1 … M7-3 |
| M7-6 | Permission matrix UI | Grid editor for role grants; sensitive changes audited and notified | M1-3, M1-6 | M | M7-1 … M7-5 |
| M7-7 | Rank limits | `role_limit` and a limit-key registry, only after [Q35] | M1-3 | M | M7-1 … M7-6 |

### 8.11 M8 — Operations (parallel track, start any time)
| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M8-1 | Domain, HTTPS, hosting | EU host, final domain, TLS. **Blocks QR printing, Google and email deliverability.** | — | S | all |
| M8-2 | Deploy kit | gunicorn config; systemd service and jobs timer; nginx with TLS and static files; proxy hops matched to `TRUSTED_PROXY_HOPS`; `deploy.sh` (backup → pull → install → `flask db upgrade` → restart → health check); `/healthz`; logs with request ids | M0-13, M8-1 | M | M1-*, M2-* |
| M8-3 | Backups + restore drill | Nightly SQLite online backup (`.backup` / `sqlite3.Connection.backup`), encrypted and off-site; retention 7 daily / 4 weekly / 12 monthly; backup before each migration; a restore performed and documented. **Hard gate for M3-9.** | M8-2 | M | all |
| M8-4 | Staging + demo seed | `flask seed demo`; staging instance | M8-2 | S | all |
| M8-5 | Monitoring | Uptime check, error emails, log rotation | M8-2 | S | all |
| M8-6 | Club-owned accounts | Club Google account, Cloud project, service-account key; shared password manager with at least 2 holders; mail sender (club domain or EU provider, SPF/DKIM) with AVV | M8-1 | S | all |
| M8-7 | GDPR pack | Datenschutzerklärung, Verarbeitungsverzeichnis, retention policy (audit IPs, drinks detail vs. bookkeeping retention), AVVs, own-data export (Art. 15/20). Get advice. **Gate before bulk onboarding (M1-9 invite batch) and before any Google feature.** | — | M | all |

### 8.12 M9 — Eigene Community: other clubs run their own copy → **R8 "Eigene Community"**
The owner wants to offer the app to other clubs (2026-10-12). One install per club (D27): each club has its own server, database and admins; nobody hosts several clubs in one database.

| ID | Title | Scope | Depends on | Size | Parallel-safe with |
|---|---|---|---|---|---|
| M9-1 | Club settings ✅ | Name, short name (menu bar) and the optional name of the Heimverein editable under Verwaltung → Verein (`system.admin`), audited; used in the menu bar, browser tab and every mail; `CLUB_NAME` in `.env` is only the fallback until saved. Later fields: logo, accent colour, contact and legal links, IBAN (M3-7) | M1-3 | S | all |
| M9-2 | Presets instead of seeds | The club's structure as one data file per kind of club (`community/presets/<key>.yaml`): ranks, Ämter (eligible ranks, descriptions, incompatibilities), groups, default grants, starting rank, the Amt told about new members, laundry and finance defaults (accounts, cost centres). Ship "Studentenverbindung" (today's seed, renamable ranks and Ämter, so other Verbindungen adapt it in the wizard). New installs get their data from the chosen preset, not from migrations; existing installs keep theirs. The last hard-coded keys (`STARTING_RANK`, `NEW_MEMBER_OFFICE`) become settings. | M9-1 | M | M2-*, M3-* |
| M9-3 | First-run wizard | `/einrichtung`, open only while no login exists and only with the one-time setup code that `flask setup` prints (otherwise anyone who finds a fresh server first becomes its admin). Steps: club name and, optionally, the Heimverein's name → first admin (name, email, password) → choose a preset → review ranks and Ämter (rename, remove, add) → modules on or off → done, with links to "Mitglieder importieren" and "Einladen". Each step is audited; the wizard locks itself when finished. | M9-2, M9-4 | M | M9-5 |
| M9-4 | Module switches | Per install: laundry, bookings, drinks, finance, calendar on or off. Menus, routes (404 when off), permissions and the wizard follow. | M9-1 | S | M9-2 |
| M9-5 | Branding | Logo upload (PNG, size-limited, re-encoded), accent colour from an accessible palette, favicon. | M9-1 | S | M9-3, M9-4 |
| M9-6 | Install kit | One documented way to install and update (container image or install script), an `.env` generator, versioned releases with a changelog, upgrade notes, a demo instance. | M8-2, M9-3 | M | — |
| M9-7 | Commercial licence and docs | The owner sells the app; each Verbindung hosts its own copy (Q38, Q40). Proprietary licence text and a licence agreement per Verbindung (legal advice needed); `THIRD_PARTY_NOTICES` for the bundled open-source libraries (all permissive: MIT, BSD, ISC, Apache, PSF, Unlicense; checked 2026-10-13); README for customers (install, first run, backups, updates); keep this club's notes (`docs/decisions.md`, spikes) as history only. Owner action: make the repository private. | — | S | all |
| M9-8 | Licence key (optional) | A signed licence file per Verbindung (club name, valid until, modules), checked offline with a public key; the app shows a notice when it is missing or expired. A self-hosted Python app can be patched, so the contract is the real protection; the key mainly makes the terms visible. | M9-3 | S | M9-6 |

### 8.13 Dependency tree
```
M0 ───────────────────────────────────────────────────────────────────────────────────────
  M0-1 rotate/audit ··········· (deploy gate) ···························┐
  M0-2 gitignore                                                          ▼
  M0-3 deps ─► M0-4 start+admin ─┬─► M0-5 config ─┐                 (deploy allowed)
                                 └─► M0-6 migr. ──┴─► M0-7 tests ─┬─► M0-8 CI
                                                                  └─► M0-9 · M0-10 · M0-11 · M0-12
                                                     (all of the above) ─► M0-13 package + blueprints
M1 ───────────────────────────────────────────────────────────────────────────────────────
  M0-13 ─┬─► M1-1 member/user ─┬─► M1-3 authz ─► M1-4 ranks ─┬─► M1-5 Ämter/groups ─► M1-12
         ├─► M1-2 audit ───────┘                             ├─► M1-7 invites · M1-9 import · M1-10 directory
         ├─► M1-6 jobs/mail ─► M1-8 reset
         └─► M1-11 frontend
AFTER M1-5 (four tracks that can run in parallel) ─────────────────────────────────────────
  Profile/Tasks  M2-1 /me ─► M2-2 tasks ─► M2-3 Consenior ─► M2-4 rotation ─► (M5-4, M7-3)
                   └─► M2-6 Amt pages        └─► M2-5, M2-7
  Finance        M3-1 ledger ─┬─► M3-3 workspace ─┬─► M3-4 bulk/split ─► (M5-5)
                              │                   └─► M3-8 payments
                              ├─► M3-2 Saldo widget (+M2-1)
                              ├─► M3-5 xlsx   ├─► M3-7 payment mails (+M1-6)
                 M3-0 Excel ──┴─► M3-6 import ─► M3-9 go-live ◄── M8-3 backups (GATE)
  Laundry        M4-1 QR core (+M0-9) ─┐
                 M4-2 machines ────────┴─► M4-3 QR form ─┬─► M4-4 lists (+M2-1)
                                                         └─► M4-5 billing (+M3-3)
  Events         M5-1 events ─┬─► M5-2 program ─► M5-8 Google push (+M5-7, M1-6) ─► M5-9 sharing
                              ├─► M5-3 RSVP ─► M5-5 costs (+M3-4)
                              └─► M5-6 ICS · M5-10 reminders
LATER ─────────────────────────────────────────────────────────────────────────────────────
  Drinks         M6-0 discovery ─► M6-1 catalog ─┬─► M6-2 API contract ─► M6-6 API v1
                                                 ├─► M6-3 Strichliste ─► M6-5 settlement (+M3-3)
                                                 └─► M6-4 stock (+M2-2)
  Office wf.     M7-1 Fux ─► M7-2 rank workflow;  M7-3 rollover (+M2-4, M3-5);  M7-4 docs ─► M7-5
OPS (any time)   M8-1 domain ─► M8-2 deploy ─► M8-3 backups ─► gate M3-9
                 M8-1 ─► M8-6 club accounts ─► M5-7 spike, M5-8, M5-11
                 M8-7 GDPR ─► gate for bulk onboarding and for Google
```

**Critical path to the headline feature (Saldo on the profile):**
M0-3 → M0-4 → M0-6 → M0-7 → M0-9 … M0-12 → M0-13 → M1-1 → M1-3 → M1-4 → M1-5 → M3-1 → M3-3 → M3-6 → **M3-9**.
M3-9 is gated by M8-3 and needs the Excel file from [Q18].

### 8.14 Releases and recommended order
| Release | Contents | Value |
|---|---|---|
| R0 Läuft wieder | M0 | Safe, starting app with tests |
| R1 Mitglieder & Profil | M1, M2-1, M2-2, M2-6, M8-2 | Personal invites, ranks, Ämter, `/me` with tasks |
| R2 Wäsche per QR | M4-1 … M4-4 (+ M8-1 before printing) | Visible quick win, no money risk |
| R3 Kasse & Saldo | M3-0 … M3-9, M8-3 | Saldo on the profile; Excel retired |
| R4 Aufgaben & Putzplan | M2-3 … M2-8 | Consenior tooling |
| R5 Semesterprogramm & Kalender | M5 | Senior tooling, ICS, Google, reminders |
| R6 Getränke + API | M6 | Bierwart tooling, counter-app API |
| R7 Ämter-Workflows | M7 | Fux curriculum, rank workflow, handover, documents |
| R8 Eigene Community | M9 | Other clubs install their own copy: first-run wizard, presets, module switches, branding |

Recommended order for a single maintainer: **R0 → R1 → R2 → R3 → R4 → R5 → R6 → R7**. R2 (laundry) comes right after R1, by the owner's decision of 2026-10-12, because it replaces a Google Form in daily use. Start R3 as soon as the Excel file arrives. M8 tasks run alongside. Of R8, M9-1 is done; M9-2 (presets) fits best right after R3, before later modules add more club-specific seed data; the rest of M9 when another club actually wants to start. Until then every milestone follows D27: club-specific names and numbers are data, never code.

---

## 9. Risks & mitigations

| # | Risk | Impact | Mitigation |
|---|---|---|---|
| 1 | Leaked secrets and admin takeover: the repo is public, and `.env` with `ADMIN_EMAIL` and the invite code is in its history | Critical | M0-1 before any deploy that makes the app start; remove auto-promotion (M0-4); `create-admin` CLI; audit existing users; secret scanning |
| 2 | Never shipping: about 75 tasks and one hobby maintainer | High | Thin releases with early visible value (R1, R2); cut list (§11); small PRs; deferred beats half-built |
| 3 | Money errors (floats, rounding, double posting, import sign errors) | High | Integer cents; Decimal import; idempotency keys; immutable journals; reversal only; preview before posting; reconciliation and sign-off; invariant tests at ≥ 90 % coverage |
| 4 | Data loss | High | Nightly encrypted off-site backups, backup before migrations, restore drill as a hard gate (M8-3) |
| 5 | Bus factor: Ämter change every semester, and only one developer | High | Club-owned accounts with at least 2 holders (M8-6); this roadmap; `docs/decisions.md`; Amt job descriptions in the app; staging with a demo seed; CI |
| 6 | Self-dealing when one person holds several roles (Bierwart and Kassenwart, admin and Kassenwart) | Medium | Person-based four-eyes rules (§6.3); `office_incompatibility`; audit; notifications on sensitive changes |
| 7 | Permission escalation by whoever can edit the matrix | Medium | Sysadmin has no finance write; seed only by explicit command; sensitive changes audited and notified |
| 8 | Gaps at semester change (nobody can post or run drinks) | Medium | Open-ended terms (D8); acting assignments; vacancy fallback (§6.4) |
| 9 | Google limitations, verification delays, or consumer-account restrictions | Medium | ICS first; spike M5-7 before building; no per-user OAuth; features hidden when not configured |
| 10 | Expectation gap: "get notifications" is not automatic with shared calendars | Medium | App email reminders; one-time setup help; explicit decision [Q28] |
| 11 | GDPR: drinks data, Saldo visibility, transfer to Google, possibly confessional membership | Medium | Minimal data; Saldo private; Google opt-in; privacy notice; retention policy; professional advice (M8-7) |
| 12 | Bar tablet abuse (drinks booked on someone else's name) | Medium | Members see their own tallies and can dispute; Bierwart review; device tokens cannot settle |
| 13 | QR sticker swap leading to a phishing login page | Low–Medium | Long remember-me so the login prompt is rare; real domain printed on the sticker; "you are normally already logged in" hint; later Google sign-in or passkeys |
| 14 | Domain change breaks printed QR codes and Google redirect URIs | Medium | M8-1 before printing; `/q/` indirection; token rotation |
| 15 | Kassenwart rejects the web grid and insists on Excel | Medium | Ask now [Q19]; polished xlsx export; read-only Sheets mirror in the backlog |
| 16 | Excel data quality (names, merged cells, formulas) | Medium | M3-0 spike, mapping table, dry run, reconciliation, one month of parallel running |
| 17 | Unknown existing drinks database | Medium | M6-0 discovery before the schema is fixed |
| 18 | SQLite locking under concurrent writes | Low | WAL, `busy_timeout`, short transactions, a single job runner; Postgres as the upgrade path (D3) |
| 19 | Migration conflicts between parallel PRs | Low | One migration PR merged at a time; CI multiple-heads check |
| 20 | Pinned versions incompatible with the server's Python | Low | M0-3 verifies them on the chosen Python |

---

## 10. Open questions for the user

Each question names the tasks it blocks. Until a question is answered, the default in the referenced section applies.

### Before M0 (now)
1. Is something running in `~/community` on a server? Which commit, does it have a database with users, has anyone registered with the `ADMIN_EMAIL`, and who has server access? → M0-1
2. Which Python version runs on the server (3.12 or 3.13)? → M0-3
3. Should the repo stay public? Do you want the git history purged (force-push) after the secrets are rotated? → M0-1, M0-2
4. Do you confirm SQLite (recommended) as the database, decided once? → M0-6

### Before M1 (identity and org)
5. Is there a domain with HTTPS yet, and where should the app be hosted (EU)? → M8-1, and it gates QR printing and Google
6. What is the exact rank list and spelling ("Spefux" or "Speefux")? Are there inactive Burschen, Ehrenmitglieder or Konkneipanten? May Spefuxe see the member directory? → M1-4, §6.5
7. Who may invite new members? Must a new Spefux be approved before getting access? → M1-7
8. Who decides rank changes (a Convent vote?), and who records them in the app (proposal: Senior and Consenior, plus the Fuxmajor for Spefux→Fux)? → M1-4, M7-2
9. Office rules:
   - Bursch-only, or may Alte Herren hold some Ämter?
   - Are there deputies?
   - May one person hold two Ämter, and which combinations are forbidden?
   - What happens when an Amt is vacant?
   - Does a term end with the semester or when the successor takes over (proposal: successor)?
   - When are elections held?
   → M1-5
10. What are the Schriftwart's duties? Are there other offices: Kassenprüfer, Hauswart, Fechtwart, IT-Wart? → M1-5 seed, M7-5
11. Do Alte Herren log in? Which modules may they see? Do they pay through the Aktivenkasse or the AHV? → §6.5, M3
12. Who counts as a Hausbewohner, and who maintains that list when people move in or out mid-semester? → M1-5, M4
13. Roughly how many members? German only, or English too? → sizing, D24
14. Who maintains the app when you are not available? → M8-6, how much structure is worth building
15. Where do semester dates come from (university calendar or club-defined)? → M1-4

### Before M2 (profile and tasks)
16. Who may create announcements: all Ämter, only the Schriftwart, or only Senior and Consenior? → M2-8
17. How does the Putzplan rotate today? Are Alte Herren or office holders exempt from tasks? → M2-4

### Before M3 (finance)
18. ~~Please share the Excel file(s)~~ **Answered** with the SS 25 journal (analysis in `docs/spikes/m3-0-kasse-excel.md`): one double-entry book, Kontokarte per member, positive = credit. Still open: the cutoff date and the latest closed semester for the opening balances. → M3-6
19. ~~Central decision: web grid or Excel?~~ **Answered 2026-10-13:** the Excel file stays untouched; Community gets its own Kasse with the Excel's structure and an export in the same layout. → D21, M3-3
20. Should the app also be the bank Kassenbuch (supplier payments, Auslagen), or only track member Saldi? → M3-8
21. **Partly answered 2026-10-13:** besides the main account there are separate Kassen, e.g. *Buka* (Burschenkasse) and *Fuka* (Fuxenkasse, which pays part of the Füxe's costs so they do not pay the full amount); one account is the club's **Heimverein**, the association that owns the house (named per club, optional; M9-1). Still open: (Aktivenkasse, Getränkekasse, Hauskasse, AHV)? Is the club a registered association (e.V.)? Is the AHV a separate legal association? → `fin_book` seed
22. ~~Who may see other members' Saldo?~~ **Answered 2026-10-13:** only the Kassenwart and the Kassenprüfer have access to the Kasse; every member sees their own Saldo. The Senior does not. → §6.5
23. How are event costs split (equal, fixed price, by rank, Füchse free)? Are there fines? Should a negative Saldo only warn or also block something? → M3-4, M5-5, §6.6
24. Which payment methods are used? What is the club IBAN (for GiroCode)? Is there a club email domain or SMTP server? → M3-7, M8-6
25. Who co-signs entries that benefit the Kassenwart personally (proposal: Senior or Consenior)? → §6.3

### Before M4 (laundry)
26. ~~Laundry list columns and billing~~ answered 2026-10-12 (see §8.7). Still open: how many washers and dryers exist; what the "Timer" column is for; whether residents without membership get their own login; whether the old entries should be imported. → M4-2, M4-3, M4-5

### Before M5 (events and Google)
27. Who owns the club Google Calendar today: a normal Gmail account or Google Workspace? Does the club (not a person) own that account? → §7.1, M5-7
28. Are email reminders plus a one-time notification setup by each member acceptable, or must notifications be fully automatic (which means per-user OAuth and Google verification)? → §7.1
29. Do all members have Google accounts? Are some iPhone-only? → M5-6, M5-9

### Before M6 (drinks)
30. What is the existing drinks database "behind the scenes"? Technology, host, schema, maintainer? Should Community import it, integrate with it, or replace it? → M6-0
31. Should your planned counter app be a separate web app or a Community module (shared login, members and prices, nothing to sync)? Which devices (bar tablet, phones, NFC chips)? How will it identify members? → M6-0, §7.3
32. Who may record tallies for other people? Is self-service recording allowed? → M6-3, §6.5
33. Who sets prices (recommended: the Bierwart, in Community)? → M6-1

### Before M7 (office workflows)
34. What does the Fux curriculum contain, and what are the prerequisites for the Burschung? → M7-1, M7-2
35. What exactly are the "limits" per rank: credit, bookings, guests, laundry, drinks? → M7-7

### Legal (before M8-7, flags only, not legal advice)
36. Is the Verbindung confessional? Membership data might then reveal religious belief (Art. 9 GDPR).
37. Can a tax adviser or the Kassenprüfer confirm the record-retention duties (e.g. §147 AO, if applicable) for ledger and tally data, and whether per-drink detail may be deleted after settlement?

### Before M9 (other clubs)
38. ~~Which licence?~~ **Answered 2026-10-13:** commercial, not free; other Verbindungen pay to use it. Proprietary licence and agreement still to be drafted with legal advice. → M9-7, M9-8
39. ~~Which clubs are "others"?~~ **Answered 2026-10-13:** other Studentenverbindungen, with a similar structure. The first preset is "Studentenverbindung"; a general Verein preset is not needed for now. → M9-2
40. ~~Who hosts?~~ **Answered 2026-10-13:** each Verbindung hosts its own copy. → M9-6

---

## 11. Future ideas backlog

These are not scheduled. Pull an item into a milestone only when there is a concrete need.

**Finance**
- Bank CSV/camt.053 import with an unmatched-payments queue **[verify bank formats]**
- Expense claims with receipt upload and an approval flow
- Period close (`fin_period`)
- Automated recurring Semesterbeitrag per rank
- Kassenprüfer report pack
- Read-only Google Sheets mirror
- Interim improved Excel template (only if R3 is months away)
- Extract a generic "billable source" interface once three billing modules exist

**Calendar and notifications**
- Per-user Google OAuth (after verification)
- Import of Google-created events (read-only)
- PWA and Web Push
- Telegram bot
- Event QR check-in for Pflichtveranstaltungen

**Drinks and laundry**
- NFC or barcode member identifiers (`member_identifier`) once the counter app needs them
- Signed outbound webhooks
- Smart-plug detection of washes
- Laundry reservations through bookings

**Members and club life**
- Leibverhältnis family tree
- Polls and votes for convents
- Guest management for events
- Liederbuch
- Alumni newsletter for Alte Herren
- Statistics (attendance, consumption trends; private by default)
- Per-field privacy settings
- Passkeys

**Platform**
- Postgres migration if concurrency demands it
- htmx 2.x upgrade
- Flask-Babel and an English UI
- Content Security Policy
- import-linter contracts
- OpenAPI contract tests
- Playwright smoke test of the QR flow
- Module registry framework (only if the module count makes plain lists painful)
- RQ or Celery job backend
