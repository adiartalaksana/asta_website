from django.db import transaction
from django.utils import timezone

from apps.accounts.services import validate_postable_account

from .models import BankStatement, JournalHeader, JournalLine
from .services import set_posting_isolation_level


def _draft_lines(entry, statement, line):
    if line.debit:  # money into the bank account
        return [
            JournalLine(header=entry, account=statement.bank_account, narration=line.description[:255], debit=line.debit),
            JournalLine(header=entry, account=line.suggested_account, narration=line.description[:255], credit=line.debit),
        ]
    return [
        JournalLine(header=entry, account=line.suggested_account, narration=line.description[:255], debit=line.credit),
        JournalLine(header=entry, account=statement.bank_account, narration=line.description[:255], credit=line.credit),
    ]


def verify_statement_and_create_drafts(statement, actor):
    """Verify a reconciled statement and create one draft journal per mutation.

    Returns ``(statement, created_count)``. Lines that already have a journal keep it;
    an unposted draft that lost its lines is rebuilt.
    """
    created_count = 0
    with transaction.atomic():
        set_posting_isolation_level()
        statement = BankStatement.objects.select_for_update(of=("self",)).select_related("bank_account").get(pk=statement.pk)
        if statement.is_verified:
            raise ValueError("Rekening koran ini sudah diverifikasi.")
        if not statement.is_reconciled:
            raise ValueError("Rekening koran harus direkonsiliasi sebelum dibuatkan jurnal.")
        if statement.bank_account_id is None:
            raise ValueError("Pilih akun bank pada Chart of Accounts terlebih dahulu.")

        lines = list(statement.lines.select_related("suggested_account", "journal_entry").select_for_update(of=("self",)))
        if not lines:
            raise ValueError("Rekening koran tidak memiliki mutasi.")
        if any(line.suggested_account_id is None for line in lines):
            raise ValueError("Petakan semua mutasi ke akun COA sebelum verifikasi.")
        if any(line.suggested_account_id == statement.bank_account_id for line in lines):
            raise ValueError("Akun lawan mutasi tidak boleh sama dengan akun bank.")

        validate_postable_account(statement.bank_account)
        for line in lines:
            validate_postable_account(line.suggested_account)
            entry = line.journal_entry
            if entry is not None:
                if entry.is_posted or entry.lines.exists():
                    continue
                JournalLine.objects.bulk_create(_draft_lines(entry, statement, line))
                created_count += 1
                continue
            entry = JournalHeader.objects.create(
                reference=f"BS-{str(statement.id)[:8]}-{line.source_row}",
                narration=line.description,
                posting_date=timezone.localtime(line.transaction_at).date(),
            )
            JournalLine.objects.bulk_create(_draft_lines(entry, statement, line))
            line.journal_entry = entry
            line.save(update_fields=["journal_entry", "updated_at"])
            created_count += 1

        statement.is_verified = True
        statement.verified_at = timezone.now()
        statement.verified_by = actor
        statement.save(update_fields=["is_verified", "verified_at", "verified_by", "updated_at"])
    return statement, created_count
