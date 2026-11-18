from decimal import Decimal

from django.db.models import Sum

from apps.ledger.models import JournalLine


def account_balance(account_id) -> Decimal:
    totals = JournalLine.objects.filter(account_id=account_id, is_posted=True).aggregate(
        debits=Sum("debit"),
        credits=Sum("credit"),
    )
    return (totals["debits"] or Decimal("0")) - (totals["credits"] or Decimal("0"))


def validate_postable_account(account) -> None:
    if not account.is_active:
        raise ValueError(f"Account {account.code} is inactive.")
