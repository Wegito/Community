"""Permission codes owned by core: members, offices, groups, semesters, audit, system."""

from .registry import declare

declare('members.directory.view', 'Mitgliederverzeichnis ansehen')
declare('members.contact.view', 'Kontaktdaten (Telefon, Adresse) anderer Mitglieder ansehen')
declare('members.invite', 'Neue Mitglieder anlegen und einladen')
declare('members.import', 'Mitglieder aus CSV/Excel importieren')
declare('members.edit_any', 'Daten aller Mitglieder bearbeiten, Logins sperren')
declare('members.rank.change', 'Rang von Mitgliedern ändern', sensitive=True)
declare('members.exit.record', 'Austritt eines Mitglieds eintragen', sensitive=True)
declare('offices.assign', 'Ämter besetzen und beenden', sensitive=True)
declare('groups.manage', 'Gruppen anlegen und Mitglieder zuordnen')
declare('org.semesters.manage', 'Semester anlegen und bearbeiten')
declare('audit.view', 'Protokoll ansehen')
declare('authz.matrix.edit', 'Rechte von Rollen bearbeiten', sensitive=True)
declare('system.admin', 'Technische Administration', sensitive=True)
