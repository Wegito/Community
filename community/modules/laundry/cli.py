import click

from . import bp, services


@bp.cli.command('import-xlsx')
@click.argument('path', type=click.Path(exists=True, dir_okay=False))
@click.option('--apply', is_flag=True, help='Really import (default: only show what would happen).')
def import_xlsx(path, apply):
    """Import the old Google-Form Waschliste (sheet "LISTE"), matched to members by email."""
    try:
        stats = services.import_google_form_xlsx(path, dry_run=not apply)
    except services.LaundryError as error:
        raise click.ClickException(str(error)) from None
    click.echo(f'{"Importiert" if apply else "Würde importieren"}: {stats["imported"]}')
    click.echo(
        f'Schon vorhanden: {stats["duplicates"]}, ungültig: {stats["invalid"]}, ohne E-Mail: {stats["no_email"]}'
    )
    if stats['unknown_email']:
        click.echo('Keinem Mitglied zugeordnet (E-Mail unbekannt):')
        for email, count in sorted(stats['unknown_email'].items()):
            click.echo(f'  {email}: {count}')
    if not apply:
        click.echo('Nichts gespeichert. Mit --apply wirklich importieren.')
