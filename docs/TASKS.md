# Task cards

Small, self-contained pieces of work, ranked by how hard they are, so that each one can go to the
cheapest agent that can do it reliably (see `docs/AGENTS.md`). Milestones and the reasons behind them
are in `docs/ROADMAP.md`; this file is the work queue.

## Tiers

| Tier | Who | Typical card | Reviewed by |
|---|---|---|---|
| **T1** light | `implementer-light` (Haiku) | Follows an existing example exactly: a field, a label, a link, a filter, a template tweak, a test for existing behaviour. 1–3 files, no design decision. | `reviewer` |
| **T2** standard | `implementer` (Sonnet) | A feature inside one module that follows existing patterns: page, form, service, migration with seed data, tests. Small choices allowed, written to `docs/decisions.md`. | `reviewer` |
| **T3** expert | `implementer-expert` (Opus) | Money, permissions, security, data migrations of existing data, concurrency, imports and reconciliation, design across modules. | `reviewer`, and a person reads the diff |
| **T4** person | the owner | Decisions, accounts, servers, legal questions, real files with personal data. Agents prepare, a person decides. | — |

Rules:
- **Risk raises the review, not only the work:** a T2 card that touches money or permissions is
  reviewed like T3 ("Review T3").
- **Escalation:** a card that fails review gets one retry at the same tier with the reviewer's notes;
  if it fails again it moves one tier up. A T3 card that fails twice goes to a person.
- **Split just in time:** cards for the next weeks are detailed; later milestones stay coarse until
  the `planner` agent splits them, because details go stale.
- **Done** means: `tools/check.sh` passes, every acceptance criterion is met, the reviewer says PASS,
  and the change is committed as `<id>: <what>`.

Card format: **ID · title** — tier · review · depends on · size · then *Goal*, *Files / example*,
*Steps*, *Done when*.

---

## Wave 1: finish the Kasse (M3) and put laundry on the Kontokarte

Recommended order; cards on the same line of the order table can run in parallel (separate worktrees).

| Order | Card | Tier | Review | Depends on |
|---|---|---|---|---|
| 1 | P1 · Number columns right-aligned | T1 | T1 | — |
| 1 | P2 · Kontokarte link on the member page | T1 | T1 | — |
| 1 | P3 · Kassenbuch filter by semester | T1 | T1 | — |
| 2 | M3-3b · Sammelzahlung from a bank statement | T2 | T3 | — |
| 2 | M3-5a · Excel export: Kassenbuch | T2 | T2 | — |
| 3 | M3-5b · Excel export: Kontokarten and cost centres | T2 | T2 | M3-5a |
| 3 | M3-7a · Club IBAN and payment details | T2 | T2 | — |
| 4 | M3-4b · Monthly charges booked automatically (opt-in) | T2 | T3 | — |
| 4 | M3-10a · Kassenzählung (cash count) | T2 | T3 | — |
| 5 | M3-8 · Second-person confirmation (cosign) and Auslagen ✅ | T3 | T3 | — |
| 5 | M4-5 · Laundry month onto the Kontokarten | T3 | T3 | — |
| 6 | M3-7b · Zahlungsaufforderung by email | T2 | T3 | M3-7a |
| 6 | M3-10b · Bilanz and GuV | T3 | T3 | M3-5a |
| 7 | M3-10c · AH-Kontingent report | T4 → T2 | T2 | owner decision |
| 7 | M3-6a · Column mapping for Excel import and export | T3 | T3 | M3-5a |
| 8 | M3-6b · Import wizard (bookings from Excel) | T3 | T3 | M3-6a |
| 8 | M3-6c · Opening balances from the Excel | T3 + T4 | T3 | M3-6b, owner file, M8-3 |

### P1 · Number columns right-aligned
T1 · review T1 · depends on — · ~30 lines
- **Goal:** In the Kasse tables, the headers of amount columns line up with the right-aligned numbers.
- **Files / example:** `community/templates/macros/ui.html` macro `table(headers, class)`;
  templates in `community/modules/finance/templates/finance/`.
- **Steps:** 1. Give `table()` an optional `align_right=()` parameter: indexes of columns whose `<th>`
  gets `text-right`. 2. Use it in `overview.html`, `journal.html`, `booking.html`, `accounts.html`,
  `umlage.html`, `recurring.html`, `_macros.html`. 3. `tools/build-css.sh`.
- **Done when:** every amount header is right-aligned; other tables are unchanged; `tools/check.sh` passes.

### P2 · Kontokarte link on the member page
T1 · review T1 · depends on — · ~20 lines
- **Goal:** Holders of `finance.ledger.view_all` see a link "Kontokarte" on a member's page.
- **Files / example:** `community/modules/members/templates/members/detail.html`; the link pattern of
  "Rang ändern" there; target `url_for('finance.card', public_id=...)`.
- **Steps:** add the link inside `{% if can('finance.ledger.view_all') %}`; add a test in
  `tests/test_finance_pages.py` (Kassenwart sees it, a Bursch does not).
- **Done when:** the test passes; `tools/check.sh` passes.

### P3 · Kassenbuch filter by semester
T1 · review T1 · depends on — · ~60 lines
- **Goal:** The Kassenbuch can be limited to one semester.
- **Files / example:** `finance/routes.py` `journal()` and its account/cost-centre filters;
  `ledger.journals(start=, end=)`; semesters from `community.core.org.models.Semester`.
- **Steps:** add a `semester` select (newest first, "alle" default) next to the other filters; pass the
  semester's `starts_on`/`ends_on`; test that a booking outside the semester is hidden.
- **Done when:** the test passes; `tools/check.sh` passes.

### M3-3b · Sammelzahlung from a bank statement
T2 · review T3 (money) · depends on — · ~250 lines
- **Goal:** The Kassenwart enters all member transfers of one bank statement in one form and books them
  as one booking (Beleg series `GS`), like the Excel's "Kontoauszug" rows.
- **Files / example:** `finance/charges.py` (how one booking with many lines is built),
  `finance/routes.py` `umlage()` (preview → confirm with token), `ledger.post_journal`.
- **Steps:** 1. Service `charges.collect_payments(rows, booked_on, money_account, title, beleg, posted_by,
  idempotency_key)` with rows `(member, cents)`; one PAYMENT booking, one line per row (D money account /
  C Kontokarte). Reject duplicates of the same member and zero amounts. 2. Page `/kasse/sammelzahlung`
  (`finance.journal.post`): date, account (Girokonto default), Beleg (next `GS`), text "Kontoauszug …",
  then rows of member + amount (start with 10 empty rows, "weitere Zeilen" adds 10). 3. Preview with
  totals, then "Jetzt buchen" (token as idempotency key). 4. Link from the Kasse overview.
- **Done when:** tests prove: one booking with N lines, every Saldo changed, a double click books once,
  an empty or duplicate row is refused with a German message, a Kassenprüfer gets 403;
  `tools/check.sh` passes.

### M3-5a · Excel export: Kassenbuch
T2 · review T2 · depends on — · ~200 lines
- **Goal:** One click downloads the Kassenbuch as `.xlsx` in the familiar layout: Datum, Beleg, Buchung,
  Betrag, then one Soll and one Haben column per account (like the Excel journal), plus Kostenstelle.
- **Files / example:** `laundry/services.py` `export_month_xlsx()` and the route that sends it;
  `ledger.accounts()`, `ledger.journals()`.
- **Steps:** service `exports.kassenbuch_xlsx(start, end)` returning bytes; amounts as numbers with
  € format, frozen header, autofilter, column widths; one row per entry; a totals row per column that
  must show Soll = Haben. Route `/kasse/kassenbuch.xlsx` (`finance.ledger.view_all`) with the same
  filters as the page; button on the Kassenbuch.
- **Done when:** a test opens the file with openpyxl and checks headers, one row per entry, column sums
  equal the ledger, Soll total = Haben total; `tools/check.sh` passes.

### M3-5b · Excel export: Kontokarten and cost centres
T2 · review T2 · depends on M3-5a · ~200 lines
- **Goal:** Two more sheets in the same file: "Kontokarten" (one row per person with name, Verwendungszweck
  and Saldo, plus a sheet "Kontoauszüge" with every entry on every Kontokarte) and "Kostenstellen"
  (income, spending and result per cost centre).
- **Files / example:** M3-5a; `ledger.member_saldi()`, `ledger.category_totals()`.
- **Done when:** a test checks both sheets against the ledger; `tools/check.sh` passes.

### M3-7a · Club IBAN and payment details
T2 · review T2 · depends on — · ~150 lines
- **Goal:** The club's IBAN and account holder are stored in Verwaltung → Verein and shown with the
  Verwendungszweck on "Mein Saldo" and the Kontoauszug when the member owes money.
- **Files / example:** `community/core/club.py` (how `heimverein` was added), migration `0013`,
  `admin/routes.py` `ClubForm`, `finance/templates/finance/_saldo_card.html`.
- **Steps:** add `iban` and `account_holder`; validate the IBAN checksum (mod 97) and store it without
  spaces; show it grouped in fours; tests for a valid, an invalid and an empty IBAN.
- **Done when:** tests pass; `tools/check.sh` passes.

### M3-4b · Monthly charges booked automatically (opt-in)
T2 · review T3 (money) · depends on — · ~150 lines
- **Goal:** A monthly charge can be marked "automatisch buchen"; then `flask jobs tick` books it on the
  1st of each month and tells the Kassenwart.
- **Files / example:** `core/platform/jobs.py` `@daily_job`; `finance/charges.py` `post_recurring()`
  (already idempotent per month); `core/platform/notify.py` `notify()`.
- **Steps:** column `auto` on `fin_recurring` (migration); checkbox in the form; a daily job that books
  the current month for every `auto` charge not yet booked (posted_by = None); notify current
  Kassenwart holders with a link; errors (empty audience) notify instead of failing the job.
- **Done when:** tests: the job books once even if run twice; a non-auto charge is not booked; the
  notification exists; `tools/check.sh` passes.

### M3-10a · Kassenzählung (cash count)
T2 · review T3 (money) · depends on — · ~200 lines
- **Goal:** The Kassenwart counts the Barkasse by denomination (rolls, coins, notes, like the Excel sheet
  "Kasse Zählung"); the app shows the difference to the book and, on confirmation, books it.
- **Files / example:** `finance/routes.py` `umlage()` (preview → confirm), `ledger.account_balance()`.
- **Steps:** form with counts per denomination; total in cents; difference to `account_balance(bar)`;
  a new seeded RESULT account "Kassendifferenz"; booking D/C between Barkasse and Kassendifferenz;
  store the count (denominations) in the booking note.
- **Done when:** tests for no difference (nothing booked), a shortfall and a surplus; `tools/check.sh` passes.

### M3-8 · Push-TAN: the Kassenprüfer confirms (cosign) the Kassenwart's own bookings ✅
T3 · review T3 · depends on — · ~350 lines
- **Goal:** A booking that credits the Kassenwart's own Kontokarte (an Auslage, a reversed charge) is
  prepared by the Kassenwart and confirmed by the **Kassenprüfer**, like a push-TAN (owner, 2026-10-13);
  only then is it booked. The Kassenprüfer gets a notification (bell and email) and confirms or rejects
  on one page.
- **Files / example:** `ledger._check_cosign()`, `post_journal(cosigned_by=)`; roadmap §6.3 rule 2.
- **Steps:** a `fin_request` table (prepared booking as JSON lines, status open/confirmed/rejected,
  requested_by, decided_by); the booking form offers "zur Gegenzeichnung vorlegen" when
  `CosignRequired` is raised; notification to cosigners; a page listing open requests for holders of
  `finance.journal.cosign`; confirm books with `cosigned_by`, reject needs a reason; the requester can
  never confirm; audit everything.
- **Done when:** tests for each invariant (self-confirm refused, confirm books once, reject books
  nothing, a person without the right gets 403); `tools/check.sh` passes; decisions written down.

### M4-5 · Laundry month onto the Kontokarten
T3 · review T3 · depends on — · ~300 lines
- **Goal:** The Consenior submits a month of laundry; the Kassenwart books it: one booking, one line per
  person (D Kontokarte / C account "Rücklage Waschmaschine" or a cost centre, owner to confirm).
- **Files / example:** `laundry/services.py` `monthly_summary()`; roadmap §5.6 `settlement` and §6.3
  rule 3 (submitter ≠ poster, otherwise cosign).
- **Steps:** `settlement` table (module, period, status DRAFT/SUBMITTED/POSTED/REJECTED, totals,
  journal id); submit page for `laundry.settlement.submit`; review and post page for the Kassenwart;
  idempotency key `laundry:YYYY-MM`; entries of a posted month can no longer be undone or cancelled.
- **Done when:** tests for every state change and invariant; `tools/check.sh` passes.

### M3-7b · Zahlungsaufforderung by email
T2 · review T3 (mails to many people) · depends on M3-7a · ~250 lines
- **Goal:** The Kassenwart picks everyone with a Saldo below a limit, previews the emails and sends
  them (through the job queue) with Saldo, IBAN and Verwendungszweck.
- **Files / example:** `core/platform/mail.py` `queue_mail()`, `templates/mail/*.txt`.
- **Done when:** tests: preview lists the right people, sending queues one mail each with a dedupe key
  per person and day, nobody without an email address is silently skipped (they are listed);
  `tools/check.sh` passes.

### M3-10b · Bilanz and GuV
T3 · review T3 · depends on M3-5a · ~300 lines
- **Goal:** Semester closing like the Excel sheets "Bilanz" and "GuV": assets and liabilities by
  account kind, income and spending, surplus or deficit; as a page and as sheets in the Excel export.
  **Not automatic** (owner, 2026-10-13): the Kassenwart opens the closing for a semester, checks it and
  presses one button; it should be easy, with no extra steps.
- **Done when:** totals balance in tests built from known bookings; `tools/check.sh` passes.

### M3-10c · AH-Kontingent report
T4 → T2 · review T2 · depends on an owner decision
- **Owner first:** which spending counts against the AH-Kontingent, in which groups, and the budget
  per semester. Then a T2 card for the report page and sheet.

### M3-6a · Column mapping for Excel import and export
T3 · review T3 · depends on M3-5a · ~300 lines
- **Goal (owner, 2026-10-13):** Import and export are optional and guided. The user points each
  Community field (Datum, Beleg, Buchungstext, Betrag, Soll, Haben, Kostenstelle, and the
  "one column pair per account" layout of the Journal) to a column of their Excel, and can point extra
  columns their file has but Community does not to **Zusatzfelder**. The default mapping is our
  Journal layout (`docs/spikes/m3-0-kasse-excel.md`).
- **Steps:** a mapping template table (name, layout "row per booking" or "column pair per account",
  field → column letter or header, list of extra columns); bookings get an optional `extra` JSON field
  (shown on the booking page and in the Kassenbuch export, never used for amounts); the export from
  M3-5a can use any saved template; a page to create, test (show the first rows as they would be read)
  and save templates.
- **Done when:** tests for both layouts, extra columns round-trip (import → export keeps them),
  wrong mappings give German errors naming the column; `tools/check.sh` passes.

### M3-6b · Import wizard (bookings from Excel)
T3 · review T3 · depends on M3-6a · ~350 lines
- **Goal:** Upload an `.xlsx`, choose a template, map names to members (remembered), see every row as it
  would be booked and every problem (unbalanced rows, unknown accounts, `#REF!`, typed arithmetic,
  dates outside the semester), then book the good rows in one go. Dry run by default; the file is read,
  never written, and not kept after the import.
- **Done when:** tests with a made-up file in the Journal layout (no real data) cover a clean import,
  each problem type, a second import of the same file (nothing booked twice), and the Kassenprüfer's
  read-only view; `tools/check.sh` passes.

### M3-6c · Opening balances from the Excel
T3 + T4 · review T3 · depends on M3-6b, the latest closed Excel file (owner) and M8-3 (backups)
- **Goal:** From the last closed semester's file: every Kontokarte and account balance as one OPENING
  booking, with a reconciliation report the Kassenwart and the Kassenprüfer sign off. The real file stays
  outside the repository.

---

## Operations (M8): agents prepare, the owner runs

| Card | Tier | Review |
|---|---|---|
| O1 · `/healthz`, gunicorn config, systemd units for the app and the jobs timer, nginx example, `deploy.sh` | T2 | T3 |
| O2 · Nightly SQLite backup (online backup API), encrypted, with retention, plus a written restore drill | T2 | T3 |
| O3 · Server: M0-1 checklist, domain and HTTPS, first deploy, restore drill performed | T4 | — |
| O4 · Repository private, licence agreement with a lawyer, club-owned accounts | T4 | — |

---

## Later waves (coarse; the `planner` splits them before they start)

| Card | Tier | Notes |
|---|---|---|
| M2-1 · Start page from cards (`core/widgets.py`) ✅ | T2 | done 2026-10-16 |
| M2-2 · Chores become tasks (assignee member, group or Amt) with data migration | T3 | migrates existing data |
| M2-3 · Consenior workspace | T2 | |
| M2-4 · Putzplan rotation and templates | T3 | scheduling rules |
| M2-5 · Task notifications | T2 | |
| M2-6 · Amt pages (job description, holders) | T1 | the tiles exist on the start page |
| M2-7 · Einkaufsliste | T1 | |
| M2-8 · Announcements: audience, pinning | T2 | |
| M5-1 … M5-6, M5-10 · Events, program, RSVP, costs, ICS feeds, reminders | T2 (M5-5 costs: T3) | |
| M5-7 … M5-9, M5-11 · Google calendar and sign-in | T3 + T4 | needs club Google account |
| M6-0 · Drinks discovery: the Bierwart's Excel (products, prices per rank?, guests, billing) | T4 → T2 | owner sends the file |
| M6-1, M6-3, M6-8 · Catalog, Strichliste, Deckel, Bierkönig, Fass-Barometer ✅ | T2 | done 2026-10-16; prices per rank and guests after M6-0 |
| M6-4 · Stock: Kisten and Fässer, counts, Schwund | T2 | Bierwart |
| M6-3b · Members mark their own Striche as disputed | T2 | |
| M6-2, M6-6 · API contract and device tokens ✅ | T3 | done 2026-10-16 (`docs/api/openapi.yaml`) |
| M6-5 · Drinks onto the Kontokarten (monthly settlement, like M4-5) | T3 | after M6-0 |
| M6-7 · Import of the Bierwart's old Excel | T2 | after M6-0 |
| M7-1 … M7-7 · Fux curriculum, rank workflow, handover, documents, matrix editor, limits | T2 (M7-2, M7-6: T3) | |
| M9-2 · Presets instead of seed migrations | T3 | must not change existing installs |
| M9-3 · First-run wizard with setup code | T3 | security |
| M9-4, M9-5 · Module switches, branding | T2 | |
| M9-6 · Install kit | T2 | |
| M9-7, M9-8 · Licence text, licence key | T4 / T2 | lawyer first |
