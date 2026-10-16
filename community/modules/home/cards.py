"""The start page's own cards: unread notifications, news from the Bund, the Bund in numbers."""

from community.core.widgets import CLUB, home_card

from . import services


@home_card('home/_unread.html', order=5)
def unread_card(member):
    items = services.unread_notifications(member)
    return {'items': items} if items else None


@home_card('home/_news.html', permission='members.directory.view', order=10, area=CLUB)
def news_card(member):
    items = services.bund_news()
    return {'items': items} if items else None


@home_card('home/_numbers.html', permission='members.directory.view', order=20, area=CLUB)
def numbers_card(member):
    rows, total = services.bund_numbers()
    return {'rows': rows, 'total': total} if total else None
