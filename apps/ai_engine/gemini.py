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

def is_configured() -> bool:
    return bool(getattr(settings, "GEMINI_API_KEY", ""))


def read_bank_statement(content: bytes, mime_type: str, accounts_text: str) -> dict:
    """Send the document to Gemini and return the parsed JSON object."""
    api_key = getattr(settings, "GEMINI_API_KEY", "")
    if not api_key:
        raise GeminiError("Pembaca AI belum dikonfigurasi. Isi GEMINI_API_KEY pada file .env lalu restart server.")
    if len(content) > MAX_INLINE_BYTES:
        raise GeminiError("Dokumen terlalu besar untuk dibaca AI (maksimum 14 MB). Pisahkan per bulan atau kompres file.")

    from .agents import MAPPER, READER, SUMMARIZER, compose_prompt, load_configs

    configs = load_configs()
    reader = configs[READER]
    if not reader.is_enabled:
        raise GeminiError("Agen pembaca rekening koran sedang dinonaktifkan oleh administrator (Pusat Kendali → Agen AI).")
    model = reader.model_name
    body = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(content).decode()}},
                {"text": compose_prompt(accounts_text, configs)},
            ],
        }],
        "generationConfig": {
            "temperature": float(reader.temperature),
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
            "maxOutputTokens": 65536,
        },
    }
    payload = _post(API_URL.format(model=model), api_key, body, model=model)

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
    result["_agents"] = {process: {"version": config.version, "enabled": config.is_enabled} for process, config in configs.items()}
    if not configs[MAPPER].is_enabled:
        for row in result.get("transactions") or []:
            if isinstance(row, dict):
                row["suggested_account_code"] = ""
    if not configs[SUMMARIZER].is_enabled:
        result["summary"] = ""
    return result


def _post(url: str, api_key: str, body: dict, attempts: int = 3, model: str = "") -> dict:
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
                raise GeminiError(f"Model Gemini '{model or settings.GEMINI_MODEL}' tidak ditemukan. Periksa model di Pusat Kendali → Agen AI atau GEMINI_MODEL.") from error
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
