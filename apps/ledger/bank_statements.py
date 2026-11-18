from __future__ import annotations

from csv import DictReader
from io import BytesIO, StringIO
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import logging
from zipfile import BadZipFile

from django.core.files.base import ContentFile
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone
from openpyxl import load_workbook

from apps.accounts.models import Account
from apps.core.money import parse_money

from .document_reader import DOCUMENT_SUFFIXES, read_document
from .models import BankStatement, BankStatementDocument, BankStatementLine, StatementDocumentStatus

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
PREFERRED_SHEET = "2026"
HEADER_ROW = 9
FIRST_DATA_ROW = 10
REQUIRED_COLUMNS = {
    "date_time": "Tanggal & Waktu",
    "date": "Tanggal",
    "reference": "No. Jurnal",
    "description": "Keterangan",
    "debit": "Debit (IDR)",
    "credit": "Kredit (IDR)",
    "balance": "Saldo (IDR)",
    "account": "NAMA AKUN",
}


def _decimal(value, field_name: str) -> Decimal:
    if value is None or (isinstance(value, str) and not value.strip()):
        return Decimal("0")
    try:
        return parse_money(value, lenient=True)
    except (InvalidOperation, ValueError) as error:
        raise ValidationError(f"Nilai {field_name} tidak valid: {value!r}.") from error


DATE_FORMATS = (
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S",
    "%d-%m-%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
)


def _parse_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    if isinstance(value, str) and value.strip():
        for date_format in DATE_FORMATS:
            try:
                return datetime.strptime(value.strip(), date_format)
            except ValueError:
                continue
    return None


def _transaction_datetime(value, fallback_date, row_label) -> datetime:
    transaction_at = _parse_datetime(value) or _parse_datetime(fallback_date)
    if transaction_at is None:
        raise ValidationError(f"{row_label}: tanggal transaksi tidak valid.")
    if timezone.is_naive(transaction_at):
        return timezone.make_aware(transaction_at, timezone.get_current_timezone())
    return transaction_at


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _account_lookup() -> dict[str, Account]:
    """Match the statement's account column by account name or code, case-insensitively."""
    lookup = {}
    for account in Account.objects.filter(is_active=True):
        lookup.setdefault(account.code.strip().casefold(), account)
        lookup.setdefault(account.name.strip().casefold(), account)
    return lookup


def _build_record(source_row, row_label, values, accounts) -> dict | None:
    description = _text(values["description"])
    transaction_value = values["date_time"]
    if not description and not _text(transaction_value):
        return None

    debit = _decimal(values["debit"], "debit")
    credit = _decimal(values["credit"], "kredit")
    if debit < 0 or credit < 0:
        raise ValidationError(f"{row_label}: nilai debit dan kredit tidak boleh negatif.")
    if (debit > 0) == (credit > 0):
        raise ValidationError(f"{row_label}: tepat satu nilai debit atau kredit harus diisi.")

    suggested_account = accounts.get(_text(values["account"]).casefold()) if _text(values["account"]) else None
    return {
        "source_row": source_row,
        "transaction_at": _transaction_datetime(transaction_value, values["date"], row_label),
        "reference": _text(values["reference"])[:100],
        "description": description or "—",
        "debit": debit,
        "credit": credit,
        "balance": _decimal(values["balance"], "saldo"),
        "suggested_account": suggested_account,
        "needs_review": suggested_account is None,
    }


def _create_statement(content, uploaded_file, actor, file_hash, bank_name, account_number, opening_balance, records):
    closing_balance = records[-1]["balance"]
    calculated_closing = opening_balance + sum(record["debit"] - record["credit"] for record in records)
    if calculated_closing != closing_balance:
        raise ValidationError(
            f"Saldo akhir tidak cocok dengan transaksi pada rekening koran "
            f"(dihitung {calculated_closing:,.2f}, tertulis {closing_balance:,.2f})."
        )

    transaction_dates = [timezone.localtime(record["transaction_at"]).date() for record in records]
    try:
        with transaction.atomic():
            statement = BankStatement(
                source_name=uploaded_file.name[:255],
                file_hash=file_hash,
                bank_name=(bank_name or "—")[:64],
                account_number=(account_number or "—")[:64],
                period_start=min(transaction_dates),
                period_end=max(transaction_dates),
                opening_balance=opening_balance,
                closing_balance=closing_balance,
                is_reconciled=True,
                uploaded_by=actor,
            )
            statement.source_file.save(uploaded_file.name, ContentFile(content), save=False)
            statement.save()
            BankStatementLine.objects.bulk_create(
                [BankStatementLine(statement=statement, **record) for record in records]
            )
    except IntegrityError as error:
        if statement.source_file:
            statement.source_file.delete(save=False)
        raise ValidationError("File rekening koran ini sudah pernah diimpor.") from error
    return statement


def _find_statement_sheet(workbook):
    """Prefer the historical '2026' worksheet, otherwise any sheet with the expected header row."""
    names = [PREFERRED_SHEET] if PREFERRED_SHEET in workbook.sheetnames else []
    names += [name for name in workbook.sheetnames if name != PREFERRED_SHEET]
    for name in names:
        rows = list(workbook[name].iter_rows(values_only=True))
        if len(rows) >= HEADER_ROW:
            headers = {_text(value) for value in rows[HEADER_ROW - 1] if value is not None}
            if set(REQUIRED_COLUMNS.values()) <= headers:
                return rows
    return None


def _import_xlsx_statement(uploaded_file, actor, content, file_hash) -> BankStatement:
    try:
        workbook = load_workbook(filename=BytesIO(content), read_only=True, data_only=True)
    except (BadZipFile, KeyError, OSError, ValueError) as error:
        raise ValidationError("File XLSX tidak dapat dibaca. Pastikan file tidak rusak.") from error
    try:
        rows = _find_statement_sheet(workbook)
    finally:
        workbook.close()
    if rows is None:
        raise ValidationError(
            f"Worksheet tidak dikenali. Baris {HEADER_ROW} harus berisi kolom: "
            f"{', '.join(REQUIRED_COLUMNS.values())}."
        )

    def cell(row_number, column_number):
        row = rows[row_number - 1] if row_number <= len(rows) else ()
        return row[column_number - 1] if column_number <= len(row) else None

    header = rows[HEADER_ROW - 1]
    positions = {_text(value): index + 1 for index, value in enumerate(header) if value is not None}
    columns = {key: positions[label] for key, label in REQUIRED_COLUMNS.items()}
    accounts = _account_lookup()
    records = []
    for row_number in range(FIRST_DATA_ROW, len(rows) + 1):
        values = {key: cell(row_number, column) for key, column in columns.items()}
        record = _build_record(row_number, f"Baris {row_number}", values, accounts)
        if record:
            records.append(record)
    if not records:
        raise ValidationError("Tidak ada transaksi yang dapat dibaca dari rekening koran.")

    return _create_statement(
        content,
        uploaded_file,
        actor,
        file_hash,
        _text(cell(3, 2)),  # B3
        _text(cell(5, 2)),  # B5
        _decimal(cell(3, 6), "saldo awal"),  # F3
        records,
    )


def _import_csv_statement(uploaded_file, actor, content, file_hash) -> BankStatement:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValidationError("CSV harus menggunakan encoding UTF-8.") from error
    rows = DictReader(StringIO(text))
    fieldnames = [_text(name) for name in (rows.fieldnames or [])]
    rows.fieldnames = fieldnames
    missing = [label for label in REQUIRED_COLUMNS.values() if label not in fieldnames]
    if missing:
        raise ValidationError(f"Format rekening koran tidak lengkap: {', '.join(missing)}.")

    accounts = _account_lookup()
    records = []
    for source_row, row in enumerate(rows, start=2):
        values = {key: row.get(label) for key, label in REQUIRED_COLUMNS.items()}
        record = _build_record(source_row, f"Baris {source_row}", values, accounts)
        if record:
            records.append(record)
    if not records:
        raise ValidationError("Tidak ada transaksi yang dapat dibaca dari rekening koran.")
    first = records[0]
    opening_balance = first["balance"] - first["debit"] + first["credit"]
    return _create_statement(
        content,
        uploaded_file,
        actor,
        file_hash,
        "CSV Import",
        "—",
        opening_balance,
        records,
    )


def _file_suffix(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def import_statement(uploaded_file, actor) -> BankStatement:
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise ValidationError("Ukuran file maksimum adalah 20 MB.")
    suffix = _file_suffix(uploaded_file.name)
    if suffix not in {"xlsx", "csv"}:
        raise ValidationError("Format transaksi terstruktur yang didukung adalah XLSX dan CSV.")
    content = uploaded_file.read()
    file_hash = sha256(content).hexdigest()
    if BankStatement.objects.filter(file_hash=file_hash).exists():
        raise ValidationError("File rekening koran ini sudah pernah diimpor.")
    if suffix == "xlsx":
        return _import_xlsx_statement(uploaded_file, actor, content, file_hash)
    return _import_csv_statement(uploaded_file, actor, content, file_hash)


def import_statement_document(uploaded_file, actor) -> BankStatementDocument:
    """Read a PDF/image statement with Gemini; the checked result waits for human review."""
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        raise ValidationError("Ukuran file maksimum adalah 20 MB.")
    suffix = _file_suffix(uploaded_file.name)
    if suffix not in DOCUMENT_SUFFIXES:
        raise ValidationError("Format dokumen yang didukung adalah PDF, PNG, JPG, WEBP, dan TIFF.")
    content = uploaded_file.read()
    file_hash = sha256(content).hexdigest()
    if BankStatementDocument.objects.filter(file_hash=file_hash).exists():
        raise ValidationError("Dokumen rekening koran ini sudah pernah diunggah.")

    result = read_document(suffix, content)
    document = BankStatementDocument(
        source_name=uploaded_file.name[:255],
        source_type="pdf" if suffix == "pdf" else "image",
        file_hash=file_hash,
        ai_result=result,
        ai_model=result["model"],
        status=StatementDocumentStatus.NEEDS_REVIEW,
        uploaded_by=actor,
    )
    try:
        with transaction.atomic():
            document.source_file.save(uploaded_file.name, ContentFile(content), save=False)
            document.save()
    except IntegrityError as error:
        document.source_file.delete(save=False)
        raise ValidationError("Dokumen rekening koran ini sudah pernah diunggah.") from error
    return document
