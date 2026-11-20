from django.conf import settings
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


class AgentProcess(models.TextChoices):
    STATEMENT_READER = "statement_reader", "Pembaca rekening koran"
    ACCOUNT_MAPPER = "account_mapper", "Pemeta akun COA"
    STATEMENT_SUMMARIZER = "statement_summarizer", "Perangkum & rekap"


class AgentProfile(UUIDModel, TimeStampedModel):
    """Editable instruction for one AI process, managed from the control center."""

    process = models.CharField(max_length=40, choices=AgentProcess.choices, unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=255, blank=True)
    instruction = models.TextField()
    is_enabled = models.BooleanField(default=True)
    model_name = models.CharField(max_length=100, blank=True, help_text="Empty = GEMINI_MODEL from .env")
    temperature = models.DecimalField(max_digits=3, decimal_places=2, default=0)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["process"]

    def __str__(self):
        return f"{self.name} (v{self.version})"


class AgentInstructionVersion(UUIDModel, TimeStampedModel):
    """Immutable snapshot of every saved agent configuration, for history and restore."""

    agent = models.ForeignKey(AgentProfile, on_delete=models.CASCADE, related_name="versions")
    version = models.PositiveIntegerField()
    instruction = models.TextField()
    is_enabled = models.BooleanField()
    model_name = models.CharField(max_length=100, blank=True)
    temperature = models.DecimalField(max_digits=3, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")

    class Meta:
        ordering = ["-version"]
        constraints = [models.UniqueConstraint(fields=["agent", "version"], name="unique_agent_version")]
