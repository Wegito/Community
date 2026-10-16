from community.core.authz.registry import declare

declare('laundry.use.create_own', 'Eigene Wäsche eintragen (Hausbewohner)')
declare('laundry.view_own', 'Eigene Wäsche und Kosten sehen')
declare('laundry.view_all', 'Wäscheliste und Monatsabrechnung aller sehen')
declare('laundry.machines.manage', 'Maschinen, Preise und QR-Code verwalten, Einträge stornieren')
