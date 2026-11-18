import logging
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from uuid import UUID

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Count, Exists, Max, OuterRef, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Account
from apps.audit.models import LedgerAuditTrail
from apps.audit.services import verify_hash_chain
from apps.core.money import parse_money

from .bank_statements import import_statement, import_statement_document
from .document_reader import read_document, statement_lines
from .models import (
    BankStatement,
    BankStatementDocument,
    BankStatementLine,
    JournalHeader,
    JournalLine,
    StatementDocumentStatus,
)
from .services import post_entry, reversal_entry
from .statement_exports import build_statement_export
from .statement_posting import verify_statement_and_create_drafts

logger = logging.getLogger(__name__)

STRUCTURED_SUFFIXES = {"xlsx", "csv"}
CONCURRENCY_MESSAGE = "Data sedang diproses oleh pengguna lain. Silakan coba lagi."


def _suffix(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


@login_required(login_url="portal-login")
def general_ledger_dashboard(request):
    totals = JournalLine.objects.filter(is_posted=True).aggregate(
        debit_total=Sum("debit", default=Decimal("0")),
        credit_total=Sum("credit", default=Decimal("0")),
    )
    context = {
        "posted_count": JournalHeader.objects.filter(is_posted=True).count(),
        "draft_count": JournalHeader.objects.filter(is_posted=False).count(),
        "line_count": JournalLine.objects.filter(is_posted=True).count(),
        "debit_total": totals["debit_total"],
        "credit_total": totals["credit_total"],
        "is_balanced": totals["debit_total"] == totals["credit_total"],
        "recent_entries": JournalHeader.objects.order_by("-posting_date", "-created_at")[:8],
    }
    return render(request, "ledger/dashboard.html", context)


@login_required(login_url="portal-login")
def journal_list(request):
    entries = list(
        JournalHeader.objects.select_related("posted_by", "reversal_of")
        .prefetch_related("lines__account")
        .annotate(has_reversal=Exists(JournalHeader.objects.filter(reversal_of=OuterRef("pk"))))
        .order_by("-posting_date", "-created_at")
    )
    for entry in entries:
        lines = entry.lines.all()
        entry.debit_total = sum((line.debit for line in lines), Decimal("0"))
        entry.credit_total = sum((line.credit for line in lines), Decimal("0"))
        entry.can_post = (
            not entry.is_posted
            and len(lines) >= 2
            and entry.debit_total == entry.credit_total
            and entry.debit_total > 0
        )
        entry.can_reverse = entry.is_posted and not entry.has_reversal and entry.reversal_of_id is None
    return render(request, "ledger/journal_list.html", {"entries": entries, "active_section": "journals"})


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.change_journalheader", raise_exception=True)
def post_journal(request, entry_id):
    entry = get_object_or_404(JournalHeader, pk=entry_id)
    try:
        entry = post_entry(entry, request.user)
    except ValueError as error:
        messages.error(request, f"{entry.reference}: {error}")
    except (OperationalError, IntegrityError):
        # Serialization failure or a concurrent audit-chain append; the transaction rolled back.
        logger.warning("Concurrent posting conflict for journal %s", entry.pk, exc_info=True)
        messages.error(request, CONCURRENCY_MESSAGE)
    else:
        messages.success(request, f"Jurnal {entry.reference} berhasil diposting.")
    return redirect("ledger:journal-list")


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.add_journalheader", raise_exception=True)
def reverse_journal(request, entry_id):
    entry = get_object_or_404(JournalHeader, pk=entry_id)
    try:
        reversal = reversal_entry(entry, request.user)
    except ValueError as error:
        messages.error(request, f"{entry.reference}: {error}")
    except (OperationalError, IntegrityError):
        logger.warning("Reversal conflict for journal %s", entry.pk, exc_info=True)
        messages.error(request, CONCURRENCY_MESSAGE)
    else:
        messages.success(request, f"Jurnal pembalik {reversal.reference} berhasil diposting.")
    return redirect("ledger:journal-list")


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.delete_journalheader", raise_exception=True)
def delete_draft_journal(request, entry_id):
    with transaction.atomic():
        entry = get_object_or_404(JournalHeader.objects.select_for_update(), pk=entry_id)
        if entry.is_posted:
            messages.error(request, "Jurnal yang sudah diposting tidak dapat dihapus. Gunakan jurnal pembalik.")
            return redirect("ledger:journal-list")

        statement_line = BankStatementLine.objects.filter(journal_entry=entry).first()
        if statement_line:
            statement = BankStatement.objects.select_for_update().get(pk=statement_line.statement_id)
            statement_line.journal_entry = None
            statement_line.save(update_fields=["journal_entry", "updated_at"])
            statement.is_verified = False
            statement.verified_at = None
            statement.verified_by = None
            statement.save(update_fields=["is_verified", "verified_at", "verified_by", "updated_at"])

        JournalLine.objects.filter(header=entry).delete()
        entry.delete()
    messages.success(request, "Jurnal draf berhasil dihapus.")
    return redirect("ledger:journal-list")


@login_required(login_url="portal-login")
def account_list(request):
    accounts = list(
        Account.objects.select_related("currency")
        .annotate(
            posted_debits=Sum(
                "journal_lines__debit",
                filter=Q(journal_lines__is_posted=True),
                default=Decimal("0"),
            ),
            posted_credits=Sum(
                "journal_lines__credit",
                filter=Q(journal_lines__is_posted=True),
                default=Decimal("0"),
            ),
        )
        .order_by("code")
    )
    for account in accounts:
        account.current_balance = account.opening_balance + account.posted_debits - account.posted_credits
    return render(request, "ledger/account_list.html", {"accounts": accounts, "active_section": "accounts"})


@login_required(login_url="portal-login")
def bank_statement_list(request):
    if request.method == "POST":
        uploaded_file = request.FILES.get("statement_file")
        if not uploaded_file:
            messages.error(request, "Pilih file rekening koran terlebih dahulu.")
            return redirect("ledger:bank-statement-list")

        is_structured = _suffix(uploaded_file.name) in STRUCTURED_SUFFIXES
        permission = "ledger.add_bankstatement" if is_structured else "ledger.add_bankstatementdocument"
        if not request.user.has_perm(permission):
            raise PermissionDenied
        try:
            if is_structured:
                statement = import_statement(uploaded_file, request.user)
            else:
                document = import_statement_document(uploaded_file, request.user)
        except ValidationError as error:
            messages.error(request, " ".join(error.messages))
            return redirect("ledger:bank-statement-list")
        if is_structured:
            messages.success(request, f"Rekening koran {statement.source_name} berhasil diimpor.")
            return redirect("ledger:bank-statement-detail", statement_id=statement.id)
        messages.success(request, f"Dokumen {document.source_name} berhasil dibaca oleh AI dan menunggu review.")
        return redirect("ledger:bank-statement-document-detail", document_id=document.id)

    statements = list(
        BankStatement.objects.select_related("uploaded_by").annotate(
            transaction_count=Count("lines"),
            review_count=Count("lines", filter=Q(lines__needs_review=True)),
        )
    )
    for statement in statements:
        digits = statement.account_number.strip()
        statement.masked_account_number = f"•••• {digits[-4:]}" if len(digits) > 4 else digits
    documents = BankStatementDocument.objects.select_related("uploaded_by", "statement")[:20]
    return render(
        request,
        "ledger/bank_statement_list.html",
        {"statements": statements, "documents": documents, "active_section": "statements"},
    )


@login_required(login_url="portal-login")
def bank_statement_detail(request, statement_id):
    statement = get_object_or_404(
        BankStatement.objects.select_related("uploaded_by", "bank_account", "verified_by"),
        pk=statement_id,
    )
    lines = statement.lines.select_related("suggested_account", "journal_entry")
    totals = lines.aggregate(
        debit_total=Sum("debit", default=Decimal("0")),
        credit_total=Sum("credit", default=Decimal("0")),
    )
    calculated_closing = statement.opening_balance + totals["debit_total"] - totals["credit_total"]
    accounts = Account.objects.filter(is_active=True).select_related("currency").order_by("code")
    return render(
        request,
        "ledger/bank_statement_detail.html",
        {
            "statement": statement,
            "lines": lines,
            "accounts": accounts,
            "unmapped_count": lines.filter(suggested_account__isnull=True).count(),
            "calculated_closing": calculated_closing,
            "closing_difference": statement.closing_balance - calculated_closing,
            "active_section": "statements",
        },
    )


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.add_bankstatement", raise_exception=True)
def create_statement_from_document(request, document_id):
    """Create a structured statement from a document.

    ``source=ai`` copies every checked Gemini row; it is only allowed when the AI result
    passed all required checks. ``source=manual`` (or legacy OCR documents) creates an
    empty statement after the reading has been verified, and rows are typed in by hand.
    """
    document = get_object_or_404(BankStatementDocument, pk=document_id)
    if document.statement_id:
        return redirect("ledger:bank-statement-detail", statement_id=document.statement_id)
    use_ai = request.POST.get("source") == "ai"
    if use_ai and not (document.ai_result or {}).get("is_consistent"):
        messages.error(request, "Hasil AI belum lolos semua pemeriksaan. Baca ulang dokumen atau gunakan input manual.")
        return redirect("ledger:bank-statement-document-detail", document_id=document.id)
    if not use_ai and document.status != StatementDocumentStatus.VERIFIED:
        messages.error(request, "Verifikasi hasil pembacaan dokumen terlebih dahulu.")
        return redirect("ledger:bank-statement-document-detail", document_id=document.id)

    try:
        bank_name = request.POST["bank_name"].strip()
        account_number = request.POST["account_number"].strip()
        period_start = date.fromisoformat(request.POST["period_start"])
        period_end = date.fromisoformat(request.POST["period_end"])
        opening_balance = parse_money(request.POST["opening_balance"])
        closing_balance = parse_money(request.POST["closing_balance"])
    except (InvalidOperation, KeyError, ValueError):
        messages.error(request, "Lengkapi metadata rekening koran dengan format yang benar.")
        return redirect("ledger:bank-statement-document-detail", document_id=document.id)
    if not bank_name or not account_number or period_end < period_start:
        messages.error(request, "Nama bank, nomor rekening, dan periode harus valid.")
        return redirect("ledger:bank-statement-document-detail", document_id=document.id)

    with transaction.atomic():
        document = BankStatementDocument.objects.select_for_update().get(pk=document.pk)
        if document.statement_id:  # created by a concurrent/double submit
            return redirect("ledger:bank-statement-detail", statement_id=document.statement_id)
        statement = BankStatement.objects.create(
            source_name=document.source_name,
            file_hash=sha256(f"{document.file_hash}:manual".encode()).hexdigest(),
            bank_name=bank_name[:64],
            account_number=account_number[:64],
            period_start=period_start,
            period_end=period_end,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            uploaded_by=request.user,
        )
        line_count = 0
        if use_ai:
            lines = statement_lines(document.ai_result)
            BankStatementLine.objects.bulk_create(BankStatementLine(statement=statement, **line) for line in lines)
            line_count = len(lines)
        document.statement = statement
        document.status = StatementDocumentStatus.VERIFIED
        document.save(update_fields=["statement", "status", "updated_at"])
    if use_ai:
        messages.success(
            request,
            f"Rekening koran dibuat dengan {line_count} mutasi hasil AI. Periksa pemetaan akun, lalu rekonsiliasi saldo.",
        )
    else:
        messages.success(request, "Rekening koran manual dibuat. Tambahkan transaksi untuk direkonsiliasi.")
    return redirect("ledger:bank-statement-detail", statement_id=statement.id)


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.change_bankstatementdocument", raise_exception=True)
def reread_bank_statement_document(request, document_id):
    """Run the Gemini reader again (e.g. after a failed check or for a legacy OCR document)."""
    document = get_object_or_404(BankStatementDocument, pk=document_id)
    if document.statement_id:
        messages.error(request, "Dokumen ini sudah dibuatkan rekening koran dan tidak dapat dibaca ulang.")
        return redirect("ledger:bank-statement-document-detail", document_id=document.id)
    try:
        with document.source_file.open("rb") as source:
            content = source.read()
        result = read_document(_suffix(document.source_file.name), content)
    except FileNotFoundError:
        messages.error(request, "File asli dokumen tidak ditemukan di server.")
        return redirect("ledger:bank-statement-document-detail", document_id=document.id)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("ledger:bank-statement-document-detail", document_id=document.id)
    with transaction.atomic():
        document = BankStatementDocument.objects.select_for_update().get(pk=document.pk)
        if document.statement_id:
            return redirect("ledger:bank-statement-document-detail", document_id=document.id)
        document.ai_result = result
        document.ai_model = result["model"]
        document.status = StatementDocumentStatus.NEEDS_REVIEW
        document.save(update_fields=["ai_result", "ai_model", "status", "updated_at"])
    messages.success(request, "Dokumen telah dibaca ulang oleh AI.")
    return redirect("ledger:bank-statement-document-detail", document_id=document.id)


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.change_bankstatement", raise_exception=True)
def configure_statement_bank_account(request, statement_id):
    statement = get_object_or_404(BankStatement, pk=statement_id)
    if statement.is_verified:
        messages.error(request, "Rekening koran yang sudah diverifikasi tidak dapat diubah.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    account = Account.objects.filter(pk=_uuid_or_none(request.POST.get("bank_account")), is_active=True).first()
    if account is None:
        messages.error(request, "Pilih akun bank yang aktif.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    statement.bank_account = account
    statement.save(update_fields=["bank_account", "updated_at"])
    messages.success(request, f"Akun bank {account.code} — {account.name} berhasil dipilih.")
    return redirect("ledger:bank-statement-detail", statement_id=statement.id)


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.change_bankstatementline", raise_exception=True)
def configure_statement_line_account(request, statement_id, line_id):
    statement = get_object_or_404(BankStatement, pk=statement_id)
    if statement.is_verified:
        messages.error(request, "Rekening koran yang sudah diverifikasi tidak dapat diubah.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    line = get_object_or_404(statement.lines, pk=line_id)
    account = Account.objects.filter(pk=_uuid_or_none(request.POST.get("account")), is_active=True).first()
    if account is None:
        messages.error(request, f"Baris {line.source_row}: pilih akun COA yang aktif.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    line.suggested_account = account
    line.needs_review = False
    line.save(update_fields=["suggested_account", "needs_review", "updated_at"])
    messages.success(request, f"Baris {line.source_row} berhasil dipetakan ke {account.code}.")
    return redirect("ledger:bank-statement-detail", statement_id=statement.id)


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.add_bankstatementline", raise_exception=True)
def add_manual_statement_line(request, statement_id):
    statement = get_object_or_404(BankStatement, pk=statement_id)
    if statement.is_reconciled or statement.is_verified:
        messages.error(request, "Rekening koran yang sudah direkonsiliasi tidak dapat ditambah mutasinya.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    try:
        transaction_at = datetime.fromisoformat(request.POST["transaction_at"])
        amount = parse_money(request.POST["amount"])
        balance = parse_money(request.POST["balance"])
    except (InvalidOperation, KeyError, ValueError):
        messages.error(request, "Tanggal, nominal, atau saldo transaksi tidak valid.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    if amount <= 0:
        messages.error(request, "Nominal transaksi harus lebih dari nol.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    description = request.POST.get("description", "").strip()
    if not description:
        messages.error(request, "Keterangan transaksi wajib diisi.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    direction = request.POST.get("direction")
    if direction not in {"debit", "credit"}:
        messages.error(request, "Pilih arah dana masuk atau keluar.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    account = Account.objects.filter(pk=_uuid_or_none(request.POST.get("account")), is_active=True).first()
    if account is None:
        messages.error(request, "Pilih akun COA yang aktif.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)
    if timezone.is_naive(transaction_at):
        transaction_at = timezone.make_aware(transaction_at, timezone.get_current_timezone())

    with transaction.atomic():
        # Lock the statement so two quick submits cannot claim the same source_row.
        statement = BankStatement.objects.select_for_update().get(pk=statement.pk)
        source_row = (statement.lines.aggregate(last_row=Max("source_row"))["last_row"] or 0) + 1
        BankStatementLine.objects.create(
            statement=statement,
            source_row=source_row,
            transaction_at=transaction_at,
            reference=request.POST.get("reference", "").strip()[:100],
            description=description,
            debit=amount if direction == "debit" else Decimal("0"),
            credit=amount if direction == "credit" else Decimal("0"),
            balance=balance,
            suggested_account=account,
            needs_review=False,
        )
        if transaction_at.date() < statement.period_start or transaction_at.date() > statement.period_end:
            messages.warning(request, "Tanggal mutasi berada di luar periode rekening koran.")
    messages.success(request, "Mutasi manual berhasil ditambahkan.")
    return redirect("ledger:bank-statement-detail", statement_id=statement.id)


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.delete_bankstatementline", raise_exception=True)
def delete_manual_statement_line(request, statement_id, line_id):
    with transaction.atomic():
        statement = get_object_or_404(BankStatement.objects.select_for_update(), pk=statement_id)
        if statement.is_reconciled or statement.is_verified:
            messages.error(request, "Mutasi pada rekening koran yang sudah direkonsiliasi tidak dapat dihapus.")
            return redirect("ledger:bank-statement-detail", statement_id=statement.id)
        line = get_object_or_404(statement.lines, pk=line_id, journal_entry__isnull=True)
        line.delete()
    messages.success(request, f"Mutasi baris {line.source_row} berhasil dihapus.")
    return redirect("ledger:bank-statement-detail", statement_id=statement.id)


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.change_bankstatement", raise_exception=True)
def reconcile_manual_statement(request, statement_id):
    with transaction.atomic():
        statement = get_object_or_404(BankStatement.objects.select_for_update(), pk=statement_id)
        if statement.is_verified:
            messages.error(request, "Rekening koran yang sudah diverifikasi tidak dapat diubah.")
            return redirect("ledger:bank-statement-detail", statement_id=statement.id)
        lines = list(statement.lines.select_for_update())
        if not lines:
            messages.error(request, "Tambahkan minimal satu mutasi sebelum rekonsiliasi.")
            return redirect("ledger:bank-statement-detail", statement_id=statement.id)
        calculated_closing = statement.opening_balance + sum(line.debit - line.credit for line in lines)
        if calculated_closing != statement.closing_balance:
            difference = statement.closing_balance - calculated_closing
            messages.error(
                request,
                f"Saldo akhir tidak cocok dengan total mutasi (selisih Rp {difference:,.2f}). Periksa kembali data.",
            )
            return redirect("ledger:bank-statement-detail", statement_id=statement.id)
        statement.is_reconciled = True
        statement.save(update_fields=["is_reconciled", "updated_at"])
    messages.success(request, "Saldo rekening koran sesuai. Anda dapat membuat jurnal draf.")
    return redirect("ledger:bank-statement-detail", statement_id=statement.id)


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.change_bankstatement", raise_exception=True)
def verify_bank_statement(request, statement_id):
    statement = get_object_or_404(BankStatement, pk=statement_id)
    try:
        statement, created_count = verify_statement_and_create_drafts(statement, request.user)
    except ValueError as error:
        messages.error(request, str(error))
    except (OperationalError, IntegrityError):
        logger.warning("Statement verification conflict for %s", statement.pk, exc_info=True)
        messages.error(request, CONCURRENCY_MESSAGE)
    else:
        messages.success(request, f"Rekening koran diverifikasi. {created_count} jurnal draf telah dibuat.")
    return redirect("ledger:bank-statement-detail", statement_id=statement.id)


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.delete_bankstatement", raise_exception=True)
def delete_bank_statement(request, statement_id):
    with transaction.atomic():
        statement = get_object_or_404(BankStatement.objects.select_for_update(), pk=statement_id)
        lines = list(statement.lines.select_related("journal_entry").select_for_update(of=("self",)))
        entries = [line.journal_entry for line in lines if line.journal_entry_id]
        if any(entry.is_posted for entry in entries):
            messages.error(request, "Rekening koran memiliki jurnal yang sudah diposting dan tidak dapat dihapus.")
            return redirect("ledger:bank-statement-detail", statement_id=statement.id)

        source_file = statement.source_file
        BankStatementDocument.objects.filter(statement=statement).update(statement=None)
        BankStatementLine.objects.filter(pk__in=[line.pk for line in lines]).update(journal_entry=None)
        JournalLine.objects.filter(header__in=entries).delete()
        JournalHeader.objects.filter(pk__in=[entry.pk for entry in entries]).delete()
        BankStatementLine.objects.filter(pk__in=[line.pk for line in lines]).delete()
        statement.delete()
        if source_file:
            transaction.on_commit(lambda: source_file.delete(save=False))
    messages.success(request, "Rekening koran dan jurnal draf terkait berhasil dihapus.")
    return redirect("ledger:bank-statement-list")


@login_required(login_url="portal-login")
def bank_statement_export(request, statement_id, export_format):
    statement = get_object_or_404(BankStatement, pk=statement_id)
    try:
        return build_statement_export(statement, export_format)
    except ValueError:
        messages.error(request, "Format ekspor tidak didukung.")
        return redirect("ledger:bank-statement-detail", statement_id=statement.id)


@login_required(login_url="portal-login")
def bank_statement_document_detail(request, document_id):
    document = get_object_or_404(
        BankStatementDocument.objects.select_related("uploaded_by", "statement"),
        pk=document_id,
    )
    return render(
        request,
        "ledger/bank_statement_document_detail.html",
        {"document": document, "active_section": "statements"},
    )


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.delete_bankstatementdocument", raise_exception=True)
def delete_bank_statement_document(request, document_id):
    with transaction.atomic():
        document = get_object_or_404(BankStatementDocument.objects.select_for_update(), pk=document_id)
        source_file = document.source_file
        document.delete()
        if source_file:
            transaction.on_commit(lambda: source_file.delete(save=False))
    messages.success(request, "Dokumen rekening koran berhasil dihapus.")
    return redirect("ledger:bank-statement-list")


@require_POST
@login_required(login_url="portal-login")
@permission_required("ledger.change_bankstatementdocument", raise_exception=True)
def verify_bank_statement_document(request, document_id):
    document = get_object_or_404(BankStatementDocument, pk=document_id)
    if document.status != StatementDocumentStatus.VERIFIED:
        document.status = StatementDocumentStatus.VERIFIED
        document.save(update_fields=["status", "updated_at"])
        messages.success(request, "Hasil pembacaan telah diverifikasi.")
    return redirect("ledger:bank-statement-document-detail", document_id=document.id)


@login_required(login_url="portal-login")
def reconciliation_overview(request):
    entries = list(
        JournalHeader.objects.filter(is_posted=True)
        .annotate(
            debit_total=Sum("lines__debit", default=Decimal("0")),
            credit_total=Sum("lines__credit", default=Decimal("0")),
        )
        .order_by("-posting_date", "-created_at")
    )
    for entry in entries:
        entry.is_balanced = entry.debit_total == entry.credit_total
    return render(
        request,
        "ledger/reconciliation.html",
        {
            "entries": entries,
            "balanced_count": sum(entry.is_balanced for entry in entries),
            "active_section": "reconciliation",
        },
    )


@login_required(login_url="portal-login")
def audit_trail(request):
    events = LedgerAuditTrail.objects.select_related("journal_entry", "actor").order_by("-created_at")[:100]
    return render(
        request,
        "ledger/audit_trail.html",
        {"events": events, "chain_is_valid": verify_hash_chain(), "active_section": "audit"},
    )


def _uuid_or_none(value):
    """Return a value safe to filter a UUID primary key with (invalid input → no match)."""
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
