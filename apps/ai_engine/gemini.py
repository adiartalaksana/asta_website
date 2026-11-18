"""Read bank-statement PDFs/images with the Google Gemini API.

Gemini returns a structured JSON transcription plus a short recap. Every number is
re-checked on our side (see ``apps.ledger.document_reader``) before anyone can turn it
into ledger data, so the model is a reader, never the source of truth.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Inline requests are limited to ~20 MB after base64 encoding (4/3 growth).
MAX_INLINE_BYTES = 14 * 1024 * 1024
RETRY_STATUSES = {429, 500, 502, 503, 504}


class GeminiError(Exception):
    """Raised with a user-facing (Indonesian) message."""


_AMOUNT = {
    "type": "STRING",
    "description": "Plain decimal number: digits, optional leading '-', '.' as decimal separator, no thousands separator, no currency. Empty string if not printed.",
}

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_bank_statement": {"type": "BOOLEAN"},
        "bank_name": {"type": "STRING"},
        "account_number": {"type": "STRING"},
        "account_holder": {"type": "STRING"},
        "currency": {"type": "STRING", "description": "ISO code, e.g. IDR"},
        "period_start": {"type": "STRING", "description": "YYYY-MM-DD or empty"},
        "period_end": {"type": "STRING", "description": "YYYY-MM-DD or empty"},
        "opening_balance": _AMOUNT,
        "closing_balance": _AMOUNT,
        "printed_total_in": {**_AMOUNT, "description": "Total money in as printed in the statement footer/summary; empty if absent."},
        "printed_total_out": {**_AMOUNT, "description": "Total money out as printed in the statement footer/summary; empty if absent."},
        "transactions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "date": {"type": "STRING", "description": "YYYY-MM-DD"},
                    "time": {"type": "STRING", "description": "HH:MM or empty"},
                    "reference": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "money_in": {**_AMOUNT, "description": "Amount credited to the account holder (incoming, CR/Kredit/Masuk). Empty if outgoing."},
                    "money_out": {**_AMOUNT, "description": "Amount debited from the account holder (outgoing, DB/Debet/Keluar). Empty if incoming."},
                    "balance": {**_AMOUNT, "description": "Running balance printed on this row; empty if not printed."},
                    "suggested_account_code": {"type": "STRING", "description": "Best matching code from the chart of accounts list, or empty."},
                },
                "required": ["date", "description", "money_in", "money_out", "balance"],
            },
        },
        "summary": {"type": "STRING", "description": "Recap in Bahasa Indonesia, 3-6 sentences."},
        "warnings": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Unreadable parts or doubts, in Bahasa Indonesia."},
    },
    "required": ["is_bank_statement", "opening_balance", "closing_balance", "transactions", "summary", "warnings"],
}

PROMPT = """You are an accounting assistant reading an Indonesian bank statement (rekening koran / mutasi rekening).
Transcribe it exactly. Rules:
- Include EVERY transaction row on EVERY page, in the printed order. Do not merge, skip or invent rows.
- Skip rows that are only carried-forward/opening/closing balance lines (e.g. "SALDO AWAL", "SALDO AKHIR"); put those values in opening_balance / closing_balance.
- Amounts: copy digits exactly as printed, converted to plain decimals ("Rp 1.234.567,89" -> "1234567.89").
- money_in is money received by the account holder (CR / Kredit / Masuk); money_out is money leaving (DB / Debet / Keluar). Exactly one of them is filled per row.
- If the statement prints no opening balance, derive it from the first row's balance and amount.
- Dates as YYYY-MM-DD. Indonesian statements use day/month order.
- suggested_account_code: choose from this chart of accounts only (code - name - type), or leave empty:
{accounts}
- summary: a recap in Bahasa Indonesia of the period, number of transactions, total in/out, the largest or notable transactions and anything unusual.
- warnings: list anything blurry, cut off, ambiguous or inconsistent. Never guess silently.
If the document is not a bank statement, set is_bank_statement=false and explain in warnings."""


def is_configured() -> bool:
    return bool(getattr(settings, "GEMINI_API_KEY", ""))


def read_bank_statement(content: bytes, mime_type: str, accounts_text: str) -> dict:
    """Send the document to Gemini and return the parsed JSON object."""
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        raise GeminiError("Pembaca AI belum dikonfigurasi. Isi GEMINI_API_KEY pada file .env lalu restart server.")
    if len(content) > MAX_INLINE_BYTES:
        raise GeminiError("Dokumen terlalu besar untuk dibaca AI (maksimum 14 MB). Pisahkan per bulan atau kompres file.")

    model = settings.GEMINI_MODEL
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(content).decode()}},
                {"text": PROMPT.format(accounts=accounts_text or "(belum ada akun)")},
            ],
        }],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "maxOutputTokens": 65536,
        },
    }
    payload = _post(API_URL.format(model=model), api_key, body)

    feedback = payload.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise GeminiError(f"Dokumen ditolak oleh filter Gemini ({feedback['blockReason']}).")
    candidates = payload.get("candidates") or []
    if not candidates:
        raise GeminiError("Gemini tidak mengembalikan hasil. Coba lagi.")
    candidate = candidates[0]
    if candidate.get("finishReason") == "MAX_TOKENS":
        raise GeminiError("Dokumen terlalu panjang untuk sekali baca. Pisahkan menjadi beberapa file (misalnya per bulan).")
    text = "".join(part.get("text", "") for part in (candidate.get("content") or {}).get("parts", []) if not part.get("thought"))
    try:
        result = json.loads(text)
    except json.JSONDecodeError as error:
        logger.warning("Gemini returned non-JSON output (finishReason=%s)", candidate.get("finishReason"))
        raise GeminiError("Hasil Gemini tidak dapat diproses. Coba baca ulang dokumen.") from error
    if not isinstance(result, dict):
        raise GeminiError("Hasil Gemini tidak dapat diproses. Coba baca ulang dokumen.")
    result["_model"] = payload.get("modelVersion") or model
    return result


def _post(url: str, api_key: str, body: dict, attempts: int = 3) -> dict:
    data = json.dumps(body).encode()
    for attempt in range(1, attempts + 1):
        request = Request(
            url,
            data=data,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST",
        )
        try:
            with urlopen(request, timeout=settings.GEMINI_TIMEOUT_SECONDS) as response:
                return json.loads(response.read())
        except HTTPError as error:
            detail = _error_detail(error)
            logger.warning("Gemini HTTP %s: %s", error.code, detail)
            if error.code in RETRY_STATUSES and attempt < attempts:
                time.sleep(2 * attempt)
                continue
            if error.code in (401, 403) or "API key" in detail:
                raise GeminiError("GEMINI_API_KEY tidak valid atau tidak memiliki akses ke model ini.") from error
            if error.code == 404:
                raise GeminiError(f"Model Gemini '{settings.GEMINI_MODEL}' tidak ditemukan. Periksa GEMINI_MODEL.") from error
            if error.code == 429:
                raise GeminiError("Kuota Gemini habis atau terlalu banyak permintaan. Coba lagi beberapa saat lagi.") from error
            if error.code == 400:
                raise GeminiError("Gemini tidak dapat memproses dokumen ini. Pastikan file tidak rusak atau terkunci kata sandi.") from error
            raise GeminiError("Layanan Gemini sedang bermasalah. Coba lagi beberapa saat lagi.") from error
        except (URLError, TimeoutError, OSError) as error:
            logger.warning("Gemini connection error: %s", error)
            if attempt < attempts:
                time.sleep(2 * attempt)
                continue
            raise GeminiError("Tidak dapat terhubung ke Gemini. Periksa koneksi internet server.") from error
    raise GeminiError("Layanan Gemini sedang bermasalah.")  # pragma: no cover


def _error_detail(error: HTTPError) -> str:
    try:
        return json.loads(error.read()).get("error", {}).get("message", "")[:300]
    except Exception:  # noqa: BLE001 - diagnostics only
        return ""
