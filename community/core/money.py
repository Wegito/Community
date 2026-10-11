"""Money is stored as integer cents (EUR). Never use floats for amounts."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation


def parse_cents(text) -> int:
    """Parse '12,50', '12.50', '-3' or '1.234,56' into cents. Raises ValueError."""
    if isinstance(text, int):
        return text * 100
    raw = str(text).strip().replace('€', '').replace(' ', '')
    if ',' in raw:
        raw = raw.replace('.', '').replace(',', '.')
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        raise ValueError(f'Kein gültiger Betrag: {text!r}') from None
    if not amount.is_finite():
        raise ValueError(f'Kein gültiger Betrag: {text!r}')
    return int((amount * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def format_cents(cents: int) -> str:
    """Format cents the German way: 123456 -> '1.234,56 €', -50 -> '-0,50 €'."""
    sign = '-' if cents < 0 else ''
    euros, rest = divmod(abs(cents), 100)
    return f'{sign}{euros:,}'.replace(',', '.') + f',{rest:02d} €'
