from django.db import models

from apps.core.models import TimeStampedModel, UUIDModel


class RawTransactionRecord(UUIDModel, TimeStampedModel):
    """Untrusted source payload held outside the immutable ledger until approved."""

    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        CLASSIFIED = "classified", "Classified"
        REVIEW_REQUIRED = "review_required", "Review required"
        STAGED = "staged", "Staged"
        REJECTED = "rejected", "Rejected"

    source = models.CharField(max_length=64)
    external_id = models.CharField(max_length=255)
    raw_payload = models.JSONField()
    payload_hash = models.CharField(max_length=64, unique=True)
    transaction_date = models.DateField(null=True, blank=True)
    vendor = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=18, decimal_places=4, null=True, blank=True)
    assigned_account = models.ForeignKey(
        "accounts.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="raw_transactions",
    )
    ai_confidence = models.DecimalField(max_digits=5, decimal_places=4, null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["source", "external_id"], name="unique_source_transaction"),
        ]
        indexes = [
            models.Index(fields=["status", "transaction_date"]),
            models.Index(fields=["source", "external_id"]),
        ]
