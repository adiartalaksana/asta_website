from django.contrib import admin

from .models import LedgerAuditTrail, SecurityEventLog


@admin.register(LedgerAuditTrail, SecurityEventLog)
class ReadOnlyAuditAdmin(admin.ModelAdmin):
    list_display = ("event_type", "actor", "created_at")
    list_filter = ("event_type",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
