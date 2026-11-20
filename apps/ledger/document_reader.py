"""Turn a Gemini transcription of a bank statement into checked, reviewable data.

Gemini only reads the document. Everything that matters for the ledger is recomputed
here with Decimal arithmetic: running balances, opening + in - out = closing, printed
totals and period dates. The resulting ``checks`` decide whether a statement may be
created from the document.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.core.exceptions import ValidationError
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from apps.accounts.models import Account
from apps.ai_engine import gemini
from apps.core.money import parse_money

IMAGE_MIME_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
DOCUMENT_SUFFIXES = {"pdf", "tif", "tiff", *IMAGE_MIME_TYPES}
ZERO = Decimal("0")


# --------------------------------------------------------------------------- input


def prepare_for_gemini(suffix: str, content: bytes) -> tuple[bytes, str]:
    """Validate the file locally and return (bytes, mime type) Gemini accepts."""
    unreadable = ValidationError("Dokumen tidak dapat dibaca. Pastikan file tidak rusak atau terkunci kata sandi.")
    if suffix == "pdf":
        if not content.lstrip()[:5] == b"%PDF-":
            raise unreadable
        return content, "application/pdf"
    try:
        with Image.open(BytesIO(content)) as image:
            image.verify()
        if suffix in IMAGE_MIME_TYPES:
            return content, IMAGE_MIME_TYPES[suffix]
        # Gemini does not take TIFF: convert every page into one PDF.
        with Image.open(BytesIO(content)) as image:
            pages = []
            for index in range(getattr(image, "n_frames", 1)):
                image.seek(index)
                pages.append(image.convert("RGB"))
        output = BytesIO()
        pages[0].save(output, format="PDF", save_all=True, append_images=pages[1:], resolution=200)
        return output.getvalue(), "application/pdf"
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
        raise unreadable from error


def chart_of_accounts_text() -> str:
    accounts = Account.objects.filter(is_active=True).order_by("code").values_list("code", "name", "account_type")[:400]
    return "\n".join(f"{code} - {name} - {kind}" for code, name, kind in accounts)


def read_document(suffix: str, content: bytes) -> dict:
    """Read a PDF/image with Gemini and return the checked result (JSON-serialisable)."""
    payload, mime_type = prepare_for_gemini(suffix, content)
    try:
        raw = gemini.read_bank_statement(payload, mime_type, chart_of_accounts_text())
    except gemini.GeminiError as error:
        raise ValidationError(str(error)) from error
    return analyse(raw)


# --------------------------------------------------------------------------- analysis


def _text(value, limit=None) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:limit] if limit else text


def _amount(value) -> Decimal | None:
    text = _text(value)
    if not text:
        return None
    try:
        return parse_money(text)  # Gemini is told to send plain decimals
    except (InvalidOperation, ValueError):
        try:
            return parse_money(text, lenient=True)
        except (InvalidOperation, ValueError):
            return None


def _date(value) -> date | None:
    text = _text(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _time(value) -> time:
    text = _text(value)
    for fmt in ("%H:%M:%S", "%H:%M", "%H.%M"):
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return time(0, 0)


def _fmt(amount: Decimal | None) -> str:
    return "" if amount is None else format(amount.quantize(Decimal("0.01")), "f")


def _rp(amount: Decimal) -> str:
    text = f"{amount:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"Rp {text}"


def analyse(raw: dict) -> dict:
    active_codes = set(Account.objects.filter(is_active=True).values_list("code", flat=True))
    rows = []
    total_in = total_out = ZERO
    invalid_rows = balance_breaks = 0
    opening = _amount(raw.get("opening_balance"))
    closing = _amount(raw.get("closing_balance"))
    period_start = _date(raw.get("period_start"))
    period_end = _date(raw.get("period_end"))

    raw_rows = raw.get("transactions") if isinstance(raw.get("transactions"), list) else []
    for index, item in enumerate(raw_rows, start=1):
        item = item if isinstance(item, dict) else {}
        money_in, money_out = _amount(item.get("money_in")) or ZERO, _amount(item.get("money_out")) or ZERO
        row_date = _date(item.get("date"))
        issues = []
        if row_date is None:
            issues.append("Tanggal tidak terbaca")
        if (money_in > 0) == (money_out > 0) or money_in < 0 or money_out < 0:
            issues.append("Harus ada tepat satu nominal masuk atau keluar")
        if not _text(item.get("description")):
            issues.append("Keterangan kosong")
        if issues:
            invalid_rows += 1
        total_in += max(money_in, ZERO)
        total_out += max(money_out, ZERO)
        code = _text(item.get("suggested_account_code"), 32)
        rows.append({
            "row": index,
            "date": row_date.isoformat() if row_date else _text(item.get("date"), 20),
            "time": _time(item.get("time")).strftime("%H:%M"),
            "reference": _text(item.get("reference"), 100),
            "description": _text(item.get("description"), 1000),
            "money_in": _fmt(money_in) if money_in else "",
            "money_out": _fmt(money_out) if money_out else "",
            "balance": _fmt(_amount(item.get("balance"))),
            "account_code": code if code in active_codes else "",
            "issues": issues,
        })

    # Derive a missing opening balance from the first printed running balance.
    if opening is None and rows and rows[0]["balance"]:
        first = rows[0]
        opening = Decimal(first["balance"]) - Decimal(first["money_in"] or 0) + Decimal(first["money_out"] or 0)

    # Running-balance check, row by row.
    running = opening
    for row in rows:
        if running is not None:
            running = running + Decimal(row["money_in"] or 0) - Decimal(row["money_out"] or 0)
        printed = Decimal(row["balance"]) if row["balance"] else None
        if printed is not None and running is not None and printed != running.quantize(Decimal("0.01")):
            row["issues"].append(f"Saldo berjalan seharusnya {_rp(running)}, tertulis {_rp(printed)}")
            balance_breaks += 1
            running = printed  # re-anchor so one misread row does not flag every later row
        if printed is None and running is not None:
            row["balance"] = _fmt(running)
            row["balance_computed"] = True
    if closing is None and rows and rows[-1]["balance"]:
        closing = Decimal(rows[-1]["balance"])

    dates = [d for d in (_date(row["date"]) for row in rows) if d]
    period_start = period_start or (min(dates) if dates else None)
    period_end = period_end or (max(dates) if dates else None)
    outside = [row["row"] for row in rows if period_start and period_end and (d := _date(row["date"])) and not period_start <= d <= period_end]

    calculated_closing = (opening + total_in - total_out) if opening is not None else None
    difference = (closing - calculated_closing) if closing is not None and calculated_closing is not None else None
    printed_in, printed_out = _amount(raw.get("printed_total_in")), _amount(raw.get("printed_total_out"))

    checks = [
        _check("Dokumen dikenali sebagai rekening koran", bool(raw.get("is_bank_statement")), required=True),
        _check("Mutasi ditemukan", bool(rows), f"{len(rows)} baris", required=True),
        _check("Semua baris lengkap (tanggal, keterangan, satu nominal)", invalid_rows == 0,
               f"{invalid_rows} baris bermasalah" if invalid_rows else "", required=True),
        _check("Saldo awal dan saldo akhir terbaca", opening is not None and closing is not None, required=True),
        _check("Saldo awal + masuk − keluar = saldo akhir", difference == 0,
               "" if difference in (None, 0) else f"selisih {_rp(difference)}", required=True),
        _check("Saldo berjalan setiap baris cocok", balance_breaks == 0,
               f"{balance_breaks} baris tidak cocok" if balance_breaks else ""),
    ]
    if printed_in is not None:
        checks.append(_check("Total masuk sesuai ringkasan dokumen", printed_in == total_in, f"tertulis {_rp(printed_in)}"))
    if printed_out is not None:
        checks.append(_check("Total keluar sesuai ringkasan dokumen", printed_out == total_out, f"tertulis {_rp(printed_out)}"))
    checks.append(_check("Semua tanggal di dalam periode", not outside,
                         f"baris {', '.join(map(str, outside[:10]))}" if outside else ""))

    largest_in = max((r for r in rows if r["money_in"]), key=lambda r: Decimal(r["money_in"]), default=None)
    largest_out = max((r for r in rows if r["money_out"]), key=lambda r: Decimal(r["money_out"]), default=None)
    return {
        "model": _text(raw.get("_model"), 100),
        "bank_name": _text(raw.get("bank_name"), 64),
        "account_number": _text(raw.get("account_number"), 64),
        "account_holder": _text(raw.get("account_holder"), 255),
        "currency": _text(raw.get("currency"), 8) or "IDR",
        "period_start": period_start.isoformat() if period_start else "",
        "period_end": period_end.isoformat() if period_end else "",
        "opening_balance": _fmt(opening),
        "closing_balance": _fmt(closing),
        "transactions": rows,
        "summary": _text(raw.get("summary"), 4000),
        "agents": raw.get("_agents") if isinstance(raw.get("_agents"), dict) else {},
        "warnings": [_text(w, 500) for w in (raw.get("warnings") or []) if _text(w)][:20],
        "recap": {
            "count": len(rows),
            "count_in": sum(1 for r in rows if r["money_in"]),
            "count_out": sum(1 for r in rows if r["money_out"]),
            "total_in": _fmt(total_in),
            "total_out": _fmt(total_out),
            "net": _fmt(total_in - total_out),
            "calculated_closing": _fmt(calculated_closing),
            "closing_difference": _fmt(difference),
            "largest_in": {"description": largest_in["description"][:120], "amount": largest_in["money_in"], "date": largest_in["date"]} if largest_in else None,
            "largest_out": {"description": largest_out["description"][:120], "amount": largest_out["money_out"], "date": largest_out["date"]} if largest_out else None,
        },
        "checks": checks,
        "is_consistent": all(check["ok"] for check in checks if check["required"]),
        "read_at": timezone.now().isoformat(),
    }


def _check(label: str, ok: bool, detail: str = "", *, required: bool = False) -> dict:
    return {"label": label, "ok": bool(ok), "detail": detail, "required": required}


# --------------------------------------------------------------------------- output


def statement_lines(result: dict) -> list[dict]:
    """BankStatementLine field values for a consistent result."""
    accounts = {a.code: a for a in Account.objects.filter(is_active=True, code__in={r["account_code"] for r in result["transactions"] if r["account_code"]})}
    tz = timezone.get_current_timezone()
    lines = []
    for row in result["transactions"]:
        transaction_at = timezone.make_aware(datetime.combine(_date(row["date"]), _time(row["time"])), tz)
        lines.append({
            "source_row": row["row"],
            "transaction_at": transaction_at,
            "reference": row["reference"],
            "description": row["description"],
            "debit": Decimal(row["money_in"] or 0),  # debit = money into the bank account
            "credit": Decimal(row["money_out"] or 0),
            "balance": Decimal(row["balance"]),
            "suggested_account": accounts.get(row["account_code"]),
            "needs_review": True,
        })
    return lines
