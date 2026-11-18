from decimal import Decimal

from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.services import validate_postable_account
from apps.audit.services import append_ledger_audit_event
from apps.core.constants import POSTING_ISOLATION_LEVEL

from .models import JournalHeader, JournalLine


def set_posting_isolation_level() -> None:
    """Raise the isolation level; must be the first statement of an outermost atomic block."""
    if connection.vendor != "postgresql" or connection.savepoint_ids:
        return  # Nested atomic blocks (savepoints) cannot change the isolation level.
    with connection.cursor() as cursor:
        cursor.execute(f"SET LOCAL TRANSACTION ISOLATION LEVEL {POSTING_ISOLATION_LEVEL}")


def _post_entry_locked(entry: JournalHeader, actor=None) -> JournalHeader:
    entry = JournalHeader.objects.select_for_update().get(pk=entry.pk)
    if entry.is_posted:
        raise ValueError("Jurnal ini sudah diposting.")

    lines = list(entry.lines.select_related("account").select_for_update(of=("self",)))
    if len(lines) < 2:
        raise ValueError("Jurnal harus memiliki minimal dua baris sebelum diposting.")

    debit_total = sum((line.debit for line in lines), Decimal("0"))
    credit_total = sum((line.credit for line in lines), Decimal("0"))
    if debit_total != credit_total:
        raise ValueError("Total debit dan kredit jurnal harus seimbang.")
    if debit_total <= 0:
        raise ValueError("Nilai jurnal harus lebih dari nol.")

    for line in lines:
        validate_postable_account(line.account)
    JournalLine.objects.filter(pk__in=[line.pk for line in lines]).update(is_posted=True)
    entry.is_posted = True
    entry.posted_at = timezone.now()
    entry.posted_by = actor
    entry.save(update_fields=["is_posted", "posted_at", "posted_by", "updated_at"])
    append_ledger_audit_event(entry, "journal.posted", actor)
    return entry


def post_entry(entry: JournalHeader, actor=None) -> JournalHeader:
    with transaction.atomic():
        set_posting_isolation_level()
        return _post_entry_locked(entry, actor)


def reversal_entry(entry: JournalHeader, actor=None) -> JournalHeader:
    with transaction.atomic():
        set_posting_isolation_level()
        original = JournalHeader.objects.select_for_update().get(pk=entry.pk)
        if not original.is_posted:
            raise ValueError("Hanya jurnal yang sudah diposting yang dapat dibalik.")
        if JournalHeader.objects.filter(reversal_of=original).exists():
            raise ValueError("Jurnal ini sudah memiliki jurnal pembalik.")

        reversal = JournalHeader.objects.create(
            reference=f"{original.reference}-REV"[:100],
            narration=f"Pembalikan {original.reference}: {original.narration}",
            posting_date=timezone.localdate(),
            reversal_of=original,
        )
        JournalLine.objects.bulk_create([
            JournalLine(
                header=reversal,
                account=line.account,
                narration=f"Pembalikan: {line.narration}"[:255],
                debit=line.credit,
                credit=line.debit,
            )
            for line in original.lines.select_related("account")
        ])
        return _post_entry_locked(reversal, actor)
