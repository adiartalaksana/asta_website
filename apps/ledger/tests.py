import json
import shutil
import tempfile
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import DatabaseError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from apps.accounts.models import Account, AccountType, Currency
from apps.ai_engine import gemini
from apps.audit.models import LedgerAuditTrail
from apps.audit.services import verify_hash_chain
from apps.core.money import parse_money

from .document_reader import prepare_for_gemini
from .models import BankStatement, BankStatementDocument, BankStatementLine, JournalHeader, JournalLine
from .services import post_entry

_media_override = None


def setUpModule():
    """Keep uploaded test files out of the real MEDIA_ROOT."""
    global _media_override
    _media_override = override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="ledger-test-media-"))
    _media_override.enable()


def tearDownModule():
    from django.conf import settings

    media_root = settings.MEDIA_ROOT
    _media_override.disable()
    shutil.rmtree(media_root, ignore_errors=True)


CSV_HEADER = "Tanggal & Waktu,Tanggal,No. Jurnal,Keterangan,Debit (IDR),Kredit (IDR),Saldo (IDR),NAMA AKUN\n"


class ParseMoneyTests(TestCase):
    def test_formats(self):
        self.assertEqual(parse_money("1500.25"), Decimal("1500.2500"))
        self.assertEqual(parse_money("Rp 1.234.567,89", lenient=True), Decimal("1234567.89"))
        self.assertEqual(parse_money("1,234,567.89", lenient=True), Decimal("1234567.89"))
        self.assertEqual(parse_money("1.500.000", lenient=True), Decimal("1500000"))
        self.assertEqual(parse_money("(2.500,00)", lenient=True), Decimal("-2500"))
        self.assertEqual(parse_money(1250.5), Decimal("1250.5"))

    def test_rejects_unusable_values(self):
        for value in ("NaN", "Infinity", "", "abc", "1e20"):
            with self.subTest(value=value), self.assertRaises(InvalidOperation):
                parse_money(value)


class LedgerWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("auditor", "a@example.com", "s3cure-pass-123")
        idr = Currency.objects.create(code="IDR", name="Rupiah")
        cls.bank = Account.objects.create(code="1100", name="Bank BNI", account_type=AccountType.ASSET, currency=idr)
        cls.revenue = Account.objects.create(code="4100", name="Pendapatan Jasa", account_type=AccountType.REVENUE, currency=idr)
        cls.expense = Account.objects.create(code="6100", name="Biaya Admin", account_type=AccountType.EXPENSE, currency=idr)

    def setUp(self):
        self.client.force_login(self.user)

    def upload_csv(self, body, name="mutasi.csv"):
        upload = SimpleUploadedFile(name, (CSV_HEADER + body).encode(), content_type="text/csv")
        return self.client.post(reverse("ledger:bank-statement-list"), {"statement_file": upload})

    def import_and_verify(self):
        response = self.upload_csv(
            "01/03/2026 08:00:00,,R1,Fee konsultasi,\"1.500.000,00\",,\"11.500.000,00\",pendapatan jasa\n"
            "02/03/2026 05:30:00,,R2,Biaya admin,,\"15.000\",\"11.485.000,00\",6100\n"
        )
        statement = BankStatement.objects.get()
        self.assertRedirects(response, reverse("ledger:bank-statement-detail", args=[statement.id]))
        self.client.post(reverse("ledger:bank-statement-bank-account", args=[statement.id]), {"bank_account": self.bank.id})
        response = self.client.post(reverse("ledger:bank-statement-verify", args=[statement.id]), follow=True)
        self.assertContains(response, "jurnal draf telah dibuat")
        statement.refresh_from_db()
        return statement

    def test_csv_import_parses_local_formats_and_maps_by_name_or_code(self):
        statement = self.import_and_verify()
        self.assertTrue(statement.is_reconciled)
        self.assertEqual(statement.opening_balance, Decimal("10000000"))
        lines = list(statement.lines.order_by("source_row"))
        self.assertEqual([line.suggested_account for line in lines], [self.revenue, self.expense])
        self.assertTrue(statement.is_verified)
        self.assertEqual(JournalHeader.objects.filter(is_posted=False).count(), 2)
        # 05:30 WIB is the previous day in UTC; the journal must use the local date.
        self.assertEqual(lines[1].journal_entry.posting_date.isoformat(), "2026-03-02")

    def test_post_and_reverse_from_journal_list(self):
        self.import_and_verify()
        entry = JournalHeader.objects.get(reference__endswith="-2")
        response = self.client.post(reverse("ledger:journal-post", args=[entry.id]), follow=True)
        self.assertContains(response, "berhasil diposting")
        entry.refresh_from_db()
        self.assertTrue(entry.is_posted)
        self.assertEqual(entry.posted_by, self.user)

        response = self.client.post(reverse("ledger:journal-delete", args=[entry.id]), follow=True)
        self.assertContains(response, "tidak dapat dihapus")

        self.client.post(reverse("ledger:journal-reverse", args=[entry.id]))
        reversal = JournalHeader.objects.get(reversal_of=entry)
        self.assertTrue(reversal.is_posted)
        self.assertEqual(LedgerAuditTrail.objects.count(), 2)
        self.assertTrue(verify_hash_chain())

        accounts = self.client.get(reverse("ledger:account-list")).context["accounts"]
        balances = {account.code: account.current_balance for account in accounts}
        self.assertEqual(balances["6100"], Decimal("0"))

        response = self.client.post(reverse("ledger:journal-reverse", args=[entry.id]), follow=True)
        self.assertContains(response, "sudah memiliki jurnal pembalik")

    def test_posted_lines_are_immutable_in_database(self):
        self.import_and_verify()
        entry = JournalHeader.objects.first()
        post_entry(entry, self.user)
        with self.assertRaises(DatabaseError), transaction.atomic():
            JournalLine.objects.filter(header=entry).update(narration="tampered")

    def test_dashboard_reports_balance(self):
        response = self.client.get(reverse("ledger:dashboard"))
        self.assertTrue(response.context["is_balanced"])

    def test_verify_rebuilds_draft_without_lines(self):
        statement = self.import_and_verify()
        line = statement.lines.first()
        JournalLine.objects.filter(header=line.journal_entry).delete()
        BankStatement.objects.filter(pk=statement.pk).update(is_verified=False)
        self.client.post(reverse("ledger:bank-statement-verify", args=[statement.id]))
        self.assertEqual(line.journal_entry.lines.count(), 2)

    def test_manual_statement_lines_can_be_corrected_before_reconciliation(self):
        statement = BankStatement.objects.create(
            source_name="manual.pdf",
            file_hash="x" * 64,
            bank_name="BNI",
            account_number="1234567890",
            period_start=datetime(2026, 3, 1).date(),
            period_end=datetime(2026, 3, 31).date(),
            opening_balance=Decimal("100"),
            closing_balance=Decimal("150"),
            uploaded_by=self.user,
        )
        add_url = reverse("ledger:bank-statement-line-manual-add", args=[statement.id])
        payload = {"transaction_at": "2026-03-05T10:00", "description": "Setoran", "direction": "debit", "balance": "160", "account": self.revenue.id}
        self.client.post(add_url, {**payload, "amount": "60"})
        response = self.client.post(add_url, {**payload, "amount": "NaN"}, follow=True)
        self.assertContains(response, "tidak valid")
        response = self.client.post(add_url, {**payload, "amount": "10", "account": "not-a-uuid"}, follow=True)
        self.assertContains(response, "akun COA yang aktif")

        response = self.client.post(reverse("ledger:bank-statement-reconcile", args=[statement.id]), follow=True)
        self.assertContains(response, "selisih")

        wrong_line = statement.lines.get()
        self.client.post(reverse("ledger:bank-statement-line-delete", args=[statement.id, wrong_line.id]))
        self.client.post(add_url, {**payload, "amount": "50", "balance": "150"})
        self.client.post(reverse("ledger:bank-statement-reconcile", args=[statement.id]))
        statement.refresh_from_db()
        self.assertTrue(statement.is_reconciled)

    def test_exports(self):
        statement = self.import_and_verify()
        for export_format in ("csv", "xlsx", "pdf", "png", "json"):
            response = self.client.get(reverse("ledger:bank-statement-export", args=[statement.id, export_format]))
            self.assertEqual(response.status_code, 200, export_format)
        csv_body = self.client.get(reverse("ledger:bank-statement-export", args=[statement.id, "csv"])).content.decode()
        self.assertIn("02/03/2026 05:30", csv_body)  # local time, not UTC

    def test_rejects_bad_files_without_server_error(self):
        response = self.upload_csv("01/03/2026,,R1,X,10,10,10,\n", name="bad.csv")
        self.assertRedirects(response, reverse("ledger:bank-statement-list"))
        upload = SimpleUploadedFile("broken.pdf", b"not a pdf", content_type="application/pdf")
        response = self.client.post(reverse("ledger:bank-statement-list"), {"statement_file": upload}, follow=True)
        self.assertContains(response, "tidak dapat dibaca")
        upload = SimpleUploadedFile("noext", b"data")
        response = self.client.post(reverse("ledger:bank-statement-list"), {"statement_file": upload}, follow=True)
        self.assertContains(response, "Format dokumen")


class AuthTests(TestCase):
    def test_login_keeps_requested_page(self):
        get_user_model().objects.create_user("staff", password="s3cure-pass-123")
        response = self.client.get(reverse("ledger:journal-list"))
        self.assertEqual(response.status_code, 302)
        response = self.client.get(response["Location"])
        self.assertIn("next=%2Fledger%2Fjournals%2F", response["Location"])
        response = self.client.post(
            reverse("portal-login"),
            {"username": "staff", "password": "s3cure-pass-123", "next": "/ledger/journals/"},
        )
        self.assertRedirects(response, reverse("ledger:journal-list"))
        self.client.post(reverse("portal-logout"))
        self.assertEqual(self.client.get(reverse("ledger:dashboard")).status_code, 302)

    def test_external_next_is_rejected(self):
        get_user_model().objects.create_user("staff", password="s3cure-pass-123")
        response = self.client.post(
            reverse("portal-login"),
            {"username": "staff", "password": "s3cure-pass-123", "next": "https://evil.example/"},
        )
        self.assertRedirects(response, reverse("ledger:dashboard"))


class RealTransactionPostingTests(TransactionTestCase):
    """Runs outside a wrapping test transaction so the REPEATABLE READ path is exercised."""

    def test_post_verify_and_reverse_with_isolation_level(self):
        user = get_user_model().objects.create_superuser("poster", "p@example.com", "s3cure-pass-123")
        idr = Currency.objects.create(code="IDR", name="Rupiah")
        bank = Account.objects.create(code="1100", name="Bank", account_type=AccountType.ASSET, currency=idr)
        income = Account.objects.create(code="4100", name="Income", account_type=AccountType.REVENUE, currency=idr)
        statement = BankStatement.objects.create(
            source_name="s.csv", file_hash="y" * 64, bank_name="BNI", account_number="1", bank_account=bank,
            period_start=datetime(2026, 3, 1).date(), period_end=datetime(2026, 3, 1).date(),
            opening_balance=Decimal("0"), closing_balance=Decimal("10"), is_reconciled=True, uploaded_by=user,
        )
        BankStatementLine.objects.create(
            statement=statement, source_row=1, transaction_at=timezone.now(), description="Fee",
            debit=Decimal("10"), balance=Decimal("10"), suggested_account=income, needs_review=False,
        )
        self.client.force_login(user)
        self.client.post(reverse("ledger:bank-statement-verify", args=[statement.id]))
        entry = JournalHeader.objects.get()
        self.client.post(reverse("ledger:journal-post", args=[entry.id]))
        self.client.post(reverse("ledger:journal-reverse", args=[entry.id]))
        self.assertEqual(JournalHeader.objects.filter(is_posted=True).count(), 2)
        self.assertTrue(verify_hash_chain())


def _gemini_raw(**overrides):
    raw = {
        "is_bank_statement": True,
        "bank_name": "BNI",
        "account_number": "0123456789",
        "account_holder": "PT Asta Asa Abadi",
        "currency": "IDR",
        "period_start": "2026-03-01",
        "period_end": "2026-03-31",
        "opening_balance": "10000000.00",
        "closing_balance": "11485000.00",
        "printed_total_in": "1500000.00",
        "printed_total_out": "15000.00",
        "transactions": [
            {"date": "2026-03-01", "time": "08:00", "reference": "R1", "description": "Fee konsultasi",
             "money_in": "1500000.00", "money_out": "", "balance": "11500000.00", "suggested_account_code": "4100"},
            {"date": "2026-03-02", "time": "", "reference": "", "description": "Biaya admin",
             "money_in": "", "money_out": "15000", "balance": "11485000.00", "suggested_account_code": "9999"},
        ],
        "summary": "Rekening koran BNI Maret 2026 dengan 2 mutasi.",
        "warnings": [],
        "_model": "gemini-test",
    }
    raw.update(overrides)
    return raw


@override_settings(GEMINI_API_KEY="test-key")
class GeminiDocumentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser("reader", "r@example.com", "s3cure-pass-123")
        idr = Currency.objects.create(code="IDR", name="Rupiah")
        cls.bank = Account.objects.create(code="1100", name="Bank BNI", account_type=AccountType.ASSET, currency=idr)
        cls.revenue = Account.objects.create(code="4100", name="Pendapatan Jasa", account_type=AccountType.REVENUE, currency=idr)

    def setUp(self):
        self.client.force_login(self.user)

    def upload(self, raw, name="mutasi.pdf", content=b"%PDF-1.4 test"):
        upload = SimpleUploadedFile(name, content, content_type="application/pdf")
        with patch("apps.ai_engine.gemini.read_bank_statement", return_value=raw) as reader:
            response = self.client.post(reverse("ledger:bank-statement-list"), {"statement_file": upload}, follow=True)
        return response, reader

    def test_consistent_result_becomes_statement(self):
        response, reader = self.upload(_gemini_raw())
        self.assertEqual(reader.call_args.args[1], "application/pdf")
        self.assertIn("4100 - Pendapatan Jasa", reader.call_args.args[2])
        document = BankStatementDocument.objects.get()
        result = document.ai_result
        self.assertTrue(result["is_consistent"])
        self.assertEqual(result["recap"]["total_out"], "15000.00")  # strict parse: not 15 or 15,000,000
        self.assertEqual(result["transactions"][1]["account_code"], "")  # unknown code dropped
        self.assertContains(response, "Rekening koran BNI Maret 2026")
        self.assertContains(response, "LOLOS PEMERIKSAAN")

        form = {"source": "ai", "bank_name": "BNI", "account_number": "0123456789", "period_start": "2026-03-01",
                "period_end": "2026-03-31", "opening_balance": "10000000", "closing_balance": "11485000"}
        self.client.post(reverse("ledger:bank-statement-document-create-statement", args=[document.id]), form)
        statement = BankStatement.objects.get()
        lines = list(statement.lines.order_by("source_row"))
        self.assertEqual([(l.debit, l.credit) for l in lines], [(Decimal("1500000"), 0), (0, Decimal("15000"))])
        self.assertEqual(lines[0].suggested_account, self.revenue)
        self.assertEqual(timezone.localtime(lines[0].transaction_at).hour, 8)
        self.client.post(reverse("ledger:bank-statement-reconcile", args=[statement.id]))
        statement.refresh_from_db()
        self.assertTrue(statement.is_reconciled)

    def test_inconsistent_result_is_flagged_and_blocked(self):
        raw = _gemini_raw(closing_balance="11500000.00")
        raw["transactions"][1]["balance"] = "11400000.00"
        response, _ = self.upload(raw)
        document = BankStatementDocument.objects.get()
        result = document.ai_result
        self.assertFalse(result["is_consistent"])
        failed = {c["label"] for c in result["checks"] if not c["ok"]}
        self.assertIn("Saldo awal + masuk − keluar = saldo akhir", failed)
        self.assertIn("Saldo berjalan setiap baris cocok", failed)
        self.assertContains(response, "PERLU PERHATIAN")
        response = self.client.post(
            reverse("ledger:bank-statement-document-create-statement", args=[document.id]), {"source": "ai"}, follow=True
        )
        self.assertContains(response, "belum lolos")
        self.assertFalse(BankStatement.objects.exists())

    def test_reread_replaces_result(self):
        self.upload(_gemini_raw(transactions=[]))
        document = BankStatementDocument.objects.get()
        self.assertFalse(document.ai_result["is_consistent"])
        with patch("apps.ai_engine.gemini.read_bank_statement", return_value=_gemini_raw()):
            self.client.post(reverse("ledger:bank-statement-document-reread", args=[document.id]))
        document.refresh_from_db()
        self.assertTrue(document.ai_result["is_consistent"])

    @override_settings(GEMINI_API_KEY="")
    def test_missing_api_key_is_reported(self):
        upload = SimpleUploadedFile("m.pdf", b"%PDF-1.4 test", content_type="application/pdf")
        response = self.client.post(reverse("ledger:bank-statement-list"), {"statement_file": upload}, follow=True)
        self.assertContains(response, "GEMINI_API_KEY")
        self.assertFalse(BankStatementDocument.objects.exists())

    def test_tiff_is_converted_to_pdf(self):
        image = BytesIO()
        Image.new("RGB", (20, 20), "white").save(image, format="TIFF")
        payload, mime_type = prepare_for_gemini("tiff", image.getvalue())
        self.assertEqual(mime_type, "application/pdf")
        self.assertTrue(payload.startswith(b"%PDF"))

    def test_client_parses_response_and_reports_truncation(self):
        def fake_response(body):
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(body).encode()
            return response

        ok = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": json.dumps({"summary": "ok"})}]}}],
              "modelVersion": "gemini-x"}
        with patch("apps.ai_engine.gemini.urlopen", return_value=fake_response(ok)) as urlopen:
            result = gemini.read_bank_statement(b"%PDF", "application/pdf", "")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        self.assertEqual(result["summary"], "ok")
        self.assertEqual(result["_model"], "gemini-x")

        truncated = {"candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": "{"}]}}]}
        with patch("apps.ai_engine.gemini.urlopen", return_value=fake_response(truncated)):
            with self.assertRaisesMessage(gemini.GeminiError, "terlalu panjang"):
                gemini.read_bank_statement(b"%PDF", "application/pdf", "")
