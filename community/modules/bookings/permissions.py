from community.core.authz.registry import declare

declare('bookings.create_own', 'Räume und Geräte buchen, eigene Buchungen stornieren')
declare('bookings.manage_any', 'Alle Buchungen stornieren, Ressourcen verwalten')
