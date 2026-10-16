"""Start page cards of the laundry: my month so far; the month and broken machines for the Consenior."""

from community.core.time import today_local
from community.core.widgets import MAIN, OFFICE, home_card

from . import services


@home_card('laundry/_home_mine.html', permission='laundry.view_own', order=30, area=MAIN)
def my_laundry_card(member):
    today = today_local()
    uses = services.uses_in_month(today.year, today.month, member=member, include_void=False)
    if not uses:
        return None
    return {
        'month': today,
        'washes': sum(use.washes for use in uses),
        'dries': sum(1 for use in uses if use.dried),
        'total_cents': sum(use.total_cents for use in uses),
        'last': uses[0],
    }


@home_card('laundry/_home_overview.html', permission='laundry.view_all', order=40, area=OFFICE)
def laundry_overview_card(member):
    today = today_local()
    rows, totals = services.monthly_summary(today.year, today.month)
    return {'month': today, 'people': len(rows), 'totals': totals, 'defect': services.defect_machines()}
