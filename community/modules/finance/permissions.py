from community.core.authz.registry import declare

declare('finance.saldo.view_own', 'Eigenen Saldo und Kontoauszug sehen')
declare('finance.ledger.view_all', 'Kasse einsehen: alle Kontokarten, Buchungen und Konten', sensitive=True)
declare('finance.journal.post', 'Buchungen erfassen', sensitive=True)
declare('finance.journal.reverse', 'Buchungen stornieren', sensitive=True)
declare(
    'finance.journal.cosign', 'Buchungen gegenzeichnen, die dem Buchenden selbst gutgeschrieben werden', sensitive=True
)
declare('finance.reports.view', 'Berichte der Kasse sehen (Bilanz, AH-Kontingent)')
declare('finance.accounts.manage', 'Konten, Kassen und Kostenstellen verwalten', sensitive=True)
