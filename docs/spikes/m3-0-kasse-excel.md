# M3-0: Analysis of the Kassenwart's Excel journal

The owner shared the Kassenwart's journal for one semester (SS 25) as an example. It was analysed locally only. **The file and every name and amount in it stay out of this public repository**; this note describes structure only.

## What the workbook is

A complete double-entry bookkeeping ("amerikanisches Journal") for one semester. Nine sheets:

| Sheet | Purpose | In the app |
|---|---|---|
| **Journal** | One row per posting: date, Beleg number, text, amount, then one Soll and one Haben column per account. Each row checks Soll = Haben = amount. Page subtotals ("Übertrag Seite n") and a running total. Closing postings at the end. | `fin_journal` + `fin_entry` (M3-1). Balance is enforced on every posting, so page subtotals and check columns disappear. |
| **Alle** | Sub-ledger of the Mitglieder account: one column per person (about 115, Aktive and some Alte Herren and guests), one row per journal row. The column sum is the person's **Kontokarte (KK)**, i.e. their Saldo. Positive = credit, negative = owes the club. | One MEMBER account per person; Saldo computed (§5.6). |
| **Versch** | Sub-ledger of the Verschiedene account: one column per cost centre (about 30: Kneipen, Vorträge, Gemeinsames Essen, Herrenabend, Stiftungsfest, Party, Renovierung, Verbandstagung, …) plus earmarked reserves (Spenden, Fuxenkasse, Waschmaschine, LAN). | Cost centres as data on each entry (`fin_category` becomes editable per semester; events from M5 become cost centres). Reserves become their own accounts. |
| **Konti** | Report to the AH-Bund on how the semester's **AH-Kontingent** (a fixed budget) was spent, grouped (Semesterprogramm, Veranstaltungen, Medienkosten, Anschaffungen, Sonstiges), with the remaining amount. | A generated report (new task M3-10). |
| **Bilanz**, **Bilanz Neu** | Semester closing: balance sheet (Vorräte, Forderungen, Kassenbestand / Eigenkapital, Rücklagen, Fremdkapital AH) and profit and loss (Beiträge, Küche, Konti, Spenden), surplus or deficit, new Eigenkapital. Two versions of the same layout. | Generated from the journal at semester close (M3-10). |
| **Kasse Zählung** | Cash count by denomination (rolls, coins, notes). | Cash count form that books the difference (M3-10). |
| **Party** | Cash reconciliation of one party (change money, takings, purchases, PayPal, profit). | Covered by an event cost centre plus a cash count. |
| **Tabelle1** | Scratch: bank statement reconciliation. | Not needed. |

### Accounts (columns of the Journal)

Giro (bank), Bar (cash box), Küche (drinks and food stock), Mitglieder (control account of all Kontokarten), Verschiedene (control account of the cost centres), Konti (AH-Kontingent), Buka (Burschenkasse), Büro, AH, Telefon, Beiträge (VDSt dues), the Heimverein (the association that owns the house; each club names it, optional), Sparkonto, Eigenkapital.

The club keeps separate Kassen (owner, 2026-10-13): the Burschenkasse, and the Fuxenkasse (Fuka, a reserve in Versch), which pays part of the Füxe's costs so they do not pay the full amount. In the app each Kasse is its own account, and a split can let a Kasse pay part of a share for a rank (M3-4).

### Beleg numbers

`G…` bank (Giro), `K…` cash (Kasse), `B…` internal postings (mostly Kontokarten and monthly charges), `GS…` collective postings of all member transfers on one monthly bank statement. In the app the number is assigned per series automatically.

## What happens to a member's Kontokarte

From the posting patterns (194 postings):

- **Auslagen** ("auf KK Bbr. …"): a member pays for the club out of pocket; the amount is credited to their card. The most common pattern after bank payments.
- **Payments**: members transfer money to the club; each monthly bank statement is one collective posting split across the members who paid ("Kartenausgleich", "Kontokartenausgleich").
- **Monthly charges**, the same amount for a group: Aktivenbeiträge / VDSt-Beiträge (all Aktive) and Internetbeiträge (Hausbewohner, two price levels).
- **Bierabrechnung**: drinks consumed in a period, a different amount per person, charged in one posting. This is what the Bierwart app (M6) will deliver.
- **Event shares**: e.g. a Stiftungsfest trip charged per person at different prices, or subsidies credited to several members at once.
- **Donations** credited to a reserve.

These map onto the planned journal kinds: PAYMENT, CHARGE (single and bulk), SPLIT, SETTLEMENT (drinks, laundry), MANUAL (Auslage, supplier invoice).

## Problems the app removes

Found in this one file:

- **Small differences slip through**: the check columns flag several rows, including the final totals, and the sub-ledgers do not always add up to their control accounts. Excel only marks these; nothing stops the file from being saved. In the app every posting must balance before it is saved, and sub-ledgers cannot drift because they are computed.
- **Broken references**: 66 formulas contain `#REF!` (in Alle, Konti and Bilanz), so parts of the AH-Kontingent report and the balance sheet show errors.
- **Sub-ledgers drift**: rows in Alle and Versch point at journal rows by position; after inserting rows, several point at the wrong row or hold typed-in text instead.
- **Copied leftovers**: closing postings and headings still carry the previous semester's dates and names.
- **Arithmetic typed into cells** (`=5.26+22.23`) in 56 places; amounts are floating-point numbers.
- **One column per member**: about 115 columns; a new member means a new column in several places.

## Consequences for M3

1. The planned design (D6 compact double-entry, integer cents, immutable journal, reversal only) fits the existing bookkeeping without changing how the Kassenwart thinks: same accounts, same Belege, same Kontokarten.
2. Add **cost centres** (the Versch columns) as editable data and require one on every entry to or from the Verschiedene accounts.
3. Add **recurring monthly charges** to M3-4 (Aktivenbeiträge, Internetbeiträge) with a preview, posted once per month and group.
4. Record **member payments from a bank statement** in one form (M3-3): pick the statement, enter or paste who paid how much, one collective journal.
5. New task **M3-10 Semesterabschluss**: Bilanz and GuV from the journal, AH-Kontingent report, cash count, carrying balances into the next semester. The Excel export (M3-5) produces these as sheets.
6. The opening balances (M3-6) come from the **latest closed semester**, not from this example file: every Kontokarte, every account and every reserve.
7. Laundry (M4-5) posts each month's totals to the members' cards; whether the income goes to the Waschmaschine reserve or to a cost centre is to be confirmed.
8. Drinks (M6) replace the manual Bierabrechnung with a SETTLEMENT per period.
