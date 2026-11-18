from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel, UUIDModel


class LedgerAuditTrail(UUIDModel, TimeStampedModel):
    journal_entry = models.ForeignKey("ledger.JournalHeader", on_delete=models.PROTECT, related_name="audit_events")
    event_type = models.CharField(max_length=64)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    previous_hash = models.CharField(max_length=64, blank=True)
    event_hash = models.CharField(max_length=64, unique=True)
    payload = models.JSONField()

    class Meta:
        ordering = ["created_at"]
        constraints = [
            # Each event may have only one successor; a concurrent append that read a stale
            # chain tail fails here instead of silently forking the hash chain.
            models.UniqueConstraint(fields=["previous_hash"], name="audit_chain_single_successor"),
        ]


class SecurityEventLog(UUIDModel, TimeStampedModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    event_type = models.CharField(max_length=64)
    details = models.JSONField(default=dict)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
