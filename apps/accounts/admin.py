from django.contrib import admin

from .models import Account, Currency


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "decimal_places")
    search_fields = ("code", "name")


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "account_type", "currency", "opening_balance", "is_active")
    list_filter = ("account_type", "is_active", "currency")
    search_fields = ("code", "name")
    ordering = ("code",)

    def has_delete_permission(self, request, obj=None):
        # Accounts referenced by journals are protected by the FK; deactivate instead of deleting.
        if obj is not None and obj.journal_lines.exists():
            return False
        return super().has_delete_permission(request, obj)
