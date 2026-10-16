"""Start page cards of the drinks: my Deckel, the Bierkönig, the Fass-Barometer, the Bierwart's tile."""

from community.core.time import MONTH_NAMES
from community.core.widgets import CLUB, MAIN, OFFICE, home_card

from . import services as drinks

KING_PLACES = 3


@home_card('drinks/_home_deckel.html', permission='drinks.view_own', order=15, area=MAIN)
def deckel_card(member):
    month = drinks.month_range()
    deckel = drinks.deckel(member, *month)
    semester = drinks.deckel(member, *drinks.semester_range())
    if not semester.drinks:
        return None
    return {'month': deckel, 'semester': semester, 'month_name': MONTH_NAMES[month[0].month - 1]}


@home_card('drinks/_home_king.html', permission='drinks.view_own', order=5, area=CLUB)
def king_card(member):
    month = drinks.month_range()
    ranking = drinks.beer_ranking(*month)
    if not ranking:
        return None
    place = next((index for index, (who, _) in enumerate(ranking, start=1) if who.id == member.id), None)
    mine = ranking[place - 1][1] if place else 0
    return {
        'top': ranking[:KING_PLACES],
        'place': place,
        'mine': mine,
        'of': len(ranking),
        'month_name': MONTH_NAMES[month[0].month - 1],
    }


@home_card('drinks/_home_barometer.html', permission='drinks.view_own', order=6, area=CLUB)
def barometer_card(member):
    beers, litres = drinks.beer_totals(*drinks.semester_range())
    if not beers:
        return None
    goal = drinks.next_milestone(beers)
    return {'beers': beers, 'litres': litres, 'goal': goal, 'percent': round(100 * (beers % 100) / 100)}


@home_card('drinks/_home_bierwart.html', permission='drinks.tally.record', order=50, area=OFFICE)
def bierwart_card(member):
    month = drinks.month_range()
    return {
        'last': drinks.last_evening(),
        'rows': drinks.count_rows(*month),
        'month_name': MONTH_NAMES[month[0].month - 1],
    }
