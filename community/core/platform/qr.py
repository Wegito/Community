"""QR codes as inline SVG (segno), e.g. an invitation link shown on the inviter's phone.

The shared qr_token table for laundry, fridge and check-in (roadmap D10) comes with M4.
"""

import segno
from markupsafe import Markup


def qr_svg(data, *, title=None, scale=6):
    """An <svg> element encoding `data`, ready to put into a template.

    Error correction M survives a scratched screen or print; the quiet zone (border) is part
    of the image, so the code scans on dark backgrounds too.
    """
    code = segno.make(data, error='m', micro=False)
    # segno escapes the title and builds the rest itself, so the markup is safe.
    return Markup(code.svg_inline(scale=scale, title=title, dark='#000', light='#fff'))  # noqa: S704
