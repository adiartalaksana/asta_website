from django.core.validators import MinLengthValidator
from django.db import models

from apps.core.constants import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.core.models import TimeStampedModel, UUIDModel


class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True, validators=[MinLengthValidator(3)])
    name = models.CharField(max_length=64)
    decimal_places = models.PositiveSmallIntegerField(default=4)

    def __str__(self):
        return self.code


class AccountType(models.TextChoices):
    ASSET = "asset", "Asset"
    LIABILITY = "liability", "Liability"
    EQUITY = "equity", "Equity"
    REVENUE = "revenue", "Revenue"
    EXPENSE = "expense", "Expense"


class Account(UUIDModel, TimeStampedModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=255)
    account_type = models.CharField(max_length=16, choices=AccountType.choices)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    is_active = models.BooleanField(default=True)
    opening_balance = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=0)

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} — {self.name}"
