import hashlib
import hmac
import json

from django.conf import settings

from .models import LedgerAuditTrail


def _event_hash(previous_hash: str, payload: dict) -> str:
    material = f"{previous_hash}:{json.dumps(payload, sort_keys=True)}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), material, hashlib.sha256).hexdigest()


def append_ledger_audit_event(entry, event_type: str, actor=None) -> LedgerAuditTrail:
    """Append an event to the hash chain.

    A concurrent append that read the same tail violates the unique ``previous_hash``
    constraint and raises IntegrityError, rolling back the whole posting transaction.
    """
    previous = LedgerAuditTrail.objects.order_by("-created_at", "-id").first()
    previous_hash = previous.event_hash if previous else ""
    payload = {"entry_id": str(entry.pk), "reference": entry.reference, "event_type": event_type}
    return LedgerAuditTrail.objects.create(
        journal_entry=entry,
        event_type=event_type,
        actor=actor,
        previous_hash=previous_hash,
        event_hash=_event_hash(previous_hash, payload),
        payload=payload,
    )


def verify_hash_chain() -> bool:
    expected_previous_hash = ""
    for event in LedgerAuditTrail.objects.order_by("created_at", "id"):
        expected_hash = _event_hash(expected_previous_hash, event.payload)
        if event.previous_hash != expected_previous_hash or not hmac.compare_digest(event.event_hash, expected_hash):
            return False
        expected_previous_hash = event.event_hash
    return True
