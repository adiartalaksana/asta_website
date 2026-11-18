from django.urls import path

from . import views

app_name = "ledger"

urlpatterns = [
    path("", views.general_ledger_dashboard, name="dashboard"),
    path("journals/", views.journal_list, name="journal-list"),
    path("journals/<uuid:entry_id>/post/", views.post_journal, name="journal-post"),
    path("journals/<uuid:entry_id>/reverse/", views.reverse_journal, name="journal-reverse"),
    path("journals/<uuid:entry_id>/delete/", views.delete_draft_journal, name="journal-delete"),
    path("accounts/", views.account_list, name="account-list"),
    path("bank-statements/", views.bank_statement_list, name="bank-statement-list"),
    path("bank-statements/<uuid:statement_id>/", views.bank_statement_detail, name="bank-statement-detail"),
    path("bank-statements/<uuid:statement_id>/delete/", views.delete_bank_statement, name="bank-statement-delete"),
    path("bank-statements/<uuid:statement_id>/bank-account/", views.configure_statement_bank_account, name="bank-statement-bank-account"),
    path("bank-statements/<uuid:statement_id>/lines/<uuid:line_id>/account/", views.configure_statement_line_account, name="bank-statement-line-account"),
    path("bank-statements/<uuid:statement_id>/verify/", views.verify_bank_statement, name="bank-statement-verify"),
    path("bank-statements/<uuid:statement_id>/export/<str:export_format>/", views.bank_statement_export, name="bank-statement-export"),
    path("bank-statements/documents/<uuid:document_id>/", views.bank_statement_document_detail, name="bank-statement-document-detail"),
    path("bank-statements/documents/<uuid:document_id>/delete/", views.delete_bank_statement_document, name="bank-statement-document-delete"),
    path("bank-statements/documents/<uuid:document_id>/verify/", views.verify_bank_statement_document, name="bank-statement-document-verify"),
    path("bank-statements/documents/<uuid:document_id>/reread/", views.reread_bank_statement_document, name="bank-statement-document-reread"),
    path("bank-statements/documents/<uuid:document_id>/statement/", views.create_statement_from_document, name="bank-statement-document-create-statement"),
    path("bank-statements/<uuid:statement_id>/lines/manual/", views.add_manual_statement_line, name="bank-statement-line-manual-add"),
    path("bank-statements/<uuid:statement_id>/lines/<uuid:line_id>/delete/", views.delete_manual_statement_line, name="bank-statement-line-delete"),
    path("bank-statements/<uuid:statement_id>/reconcile/", views.reconcile_manual_statement, name="bank-statement-reconcile"),
    path("reconciliation/", views.reconciliation_overview, name="reconciliation"),
    path("audit/", views.audit_trail, name="audit-trail"),
]
