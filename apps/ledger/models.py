from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.core.constants import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS
from apps.core.models import TimeStampedModel, UUIDModel


class TransactionBatch(UUIDModel, TimeStampedModel):
    reference = models.CharField(max_length=100, unique=True)
    source = models.CharField(max_length=64)
    payload_hash = models.CharField(max_length=64, unique=True)


class JournalHeader(UUIDModel, TimeStampedModel):
    batch = models.ForeignKey(TransactionBatch, on_delete=models.PROTECT, related_name="entries", null=True, blank=True)
    reference = models.CharField(max_length=100, unique=True)
    narration = models.TextField()
    posting_date = models.DateField()
    is_posted = models.BooleanField(default=False)
    posted_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    reversal_of = models.OneToOneField("self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversal")


class JournalLine(UUIDModel, TimeStampedModel):
    header = models.ForeignKey(JournalHeader, on_delete=models.PROTECT, related_name="lines")
    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="journal_lines")
    narration = models.CharField(max_length=255, blank=True)
    debit = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=0)
    credit = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=0)
    is_posted = models.BooleanField(default=False, editable=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0)),
                name="journal_line_exactly_one_side_positive",
            )
        ]


class BankStatement(UUIDModel, TimeStampedModel):
    source_file = models.FileField(upload_to="bank_statements/%Y/%m/")
    source_name = models.CharField(max_length=255)
    file_hash = models.CharField(max_length=64, unique=True)
    bank_name = models.CharField(max_length=64)
    account_number = models.CharField(max_length=64)
    period_start = models.DateField()
    period_end = models.DateField()
    opening_balance = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    closing_balance = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    is_reconciled = models.BooleanField(default=False)
    bank_account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bank_statements",
    )
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="verified_statements",
    )
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="uploaded_statements")

    class Meta:
        ordering = ["-period_end", "-created_at"]


class BankStatementLine(UUIDModel, TimeStampedModel):
    statement = models.ForeignKey(BankStatement, on_delete=models.PROTECT, related_name="lines")
    source_row = models.PositiveIntegerField()
    transaction_at = models.DateTimeField()
    reference = models.CharField(max_length=100, blank=True)
    description = models.TextField()
    debit = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=0)
    credit = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES, default=0)
    balance = models.DecimalField(max_digits=MONEY_MAX_DIGITS, decimal_places=MONEY_DECIMAL_PLACES)
    suggested_account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="statement_lines",
    )
    journal_entry = models.OneToOneField(
        JournalHeader,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="bank_statement_line",
    )
    needs_review = models.BooleanField(default=True)

    class Meta:
        ordering = ["transaction_at", "source_row"]
        constraints = [
            models.UniqueConstraint(fields=["statement", "source_row"], name="unique_statement_source_row"),
            models.CheckConstraint(
                condition=(Q(debit__gt=0, credit=0) | Q(credit__gt=0, debit=0)),
                name="statement_line_exactly_one_side_positive",
            ),
        ]


class StatementDocumentStatus(models.TextChoices):
    EXTRACTED = "extracted", "Text extracted"
    NEEDS_REVIEW = "needs_review", "Needs review"
    VERIFIED = "verified", "Verified"


class BankStatementDocument(UUIDModel, TimeStampedModel):
    statement = models.ForeignKey(
        BankStatement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="source_documents",
    )
    source_file = models.FileField(upload_to="bank_statement_documents/%Y/%m/")
    source_name = models.CharField(max_length=255)
    source_type = models.CharField(max_length=16)
    file_hash = models.CharField(max_length=64, unique=True)
    extracted_text = models.TextField(blank=True)  # legacy OCR/PDF text (before the Gemini reader)
    ai_result = models.JSONField(default=dict, blank=True)
    ai_model = models.CharField(max_length=100, blank=True)
    status = models.CharField(
        max_length=16,
        choices=StatementDocumentStatus.choices,
        default=StatementDocumentStatus.NEEDS_REVIEW,
    )
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["-created_at"]
