"""Start page cards of the Kasse: my Saldo for everyone, the Kasse for those who keep it."""

from flask_login import current_user

from community.core.time import today_local
from community.core.widgets import MAIN, OFFICE, home_card

from . import charges, cosign
from . import services as ledger


@home_card('finance/_home_saldo.html', permission='finance.saldo.view_own', order=10, area=MAIN)
def saldo_card(member):
    account = ledger.member_account(member, create=False)
    if account is None:
        return None
    rows = ledger.statement(account)[1][-3:][::-1]
    waiting = [item for item in cosign.open_requests() if item.requested_by_user_id == current_user.id]
    return {'saldo': ledger.saldo(member), 'recent': rows, 'payment_ref': member.payment_ref, 'waiting': waiting}


@home_card('finance/_home_kasse.html', permission='finance.journal.post', order=10, area=OFFICE)
def kasse_card(member):
    saldi = [cents for _, cents in ledger.member_saldi()]
    month = today_local().replace(day=1)
    unbooked = [item for item in charges.recurring_items() if charges.recurring_posted(item, month) is None]
    return {
        'owed': sum(cents for cents in saldi if cents < 0),
        'owing': sum(1 for cents in saldi if cents < 0),
        'waiting': len(cosign.open_requests()),
        'unbooked': unbooked,
        'month': charges.month_label(month),
    }


@home_card('finance/_home_cosign.html', permission='finance.journal.cosign', order=11, area=OFFICE)
def cosign_card(member):
    if not cosign.may_confirm(member):
        return None
    mine = [item for item in cosign.open_requests() if item.requested_by.member_id != member.id]
    return {'items': mine}
