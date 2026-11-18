from django.contrib import admin

from .models import BankStatement, BankStatementDocument, BankStatementLine, JournalHeader, JournalLine


class ReadOnlyAdmin(admin.ModelAdmin):
    """Ledger records change only through the portal workflows that enforce the invariants."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    fields = ("account", "narration", "debit", "credit", "is_posted")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(JournalHeader)
class JournalHeaderAdmin(ReadOnlyAdmin):
    list_display = ("reference", "posting_date", "is_posted", "posted_by", "posted_at")
    list_filter = ("is_posted", "posting_date")
    search_fields = ("reference", "narration")
    inlines = [JournalLineInline]


class BankStatementLineInline(admin.TabularInline):
    model = BankStatementLine
    fields = ("source_row", "transaction_at", "description", "debit", "credit", "balance", "suggested_account", "journal_entry")
    readonly_fields = fields
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(BankStatement)
class BankStatementAdmin(ReadOnlyAdmin):
    list_display = ("bank_name", "account_number", "period_start", "period_end", "is_reconciled", "is_verified")
    list_filter = ("is_reconciled", "is_verified", "bank_name")
    inlines = [BankStatementLineInline]


@admin.register(BankStatementDocument)
class BankStatementDocumentAdmin(ReadOnlyAdmin):
    list_display = ("source_name", "source_type", "status", "statement", "created_at")
    list_filter = ("status", "source_type")
