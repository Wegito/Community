from community.core.authz.registry import declare

declare('drinks.view_own', 'Eigene Striche sehen, Bierkönig und Fass-Barometer')
declare('drinks.view_all', 'Striche aller sehen')
declare('drinks.tally.record', 'Strichliste eintragen und korrigieren (Bierwart)')
declare('drinks.catalog.manage', 'Getränke und Preise verwalten')
declare('drinks.devices.manage', 'Geräte (Theken-Tablet) für die Getränke-Schnittstelle verwalten', sensitive=True)
