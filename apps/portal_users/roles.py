"""Portal roles, stored as Django groups so permissions stay in one place."""

from django.contrib.auth.models import Group, Permission

ADMINISTRATOR = "administrator"
ACCOUNTANT = "akuntan"
CLIENT = "klien"

ROLE_GROUPS = {
    ADMINISTRATOR: "Administrator Portal",
    ACCOUNTANT: "Akuntan",
    CLIENT: "Klien (lihat saja)",
}
ROLE_LABELS = {
    ADMINISTRATOR: "Administrator",
    ACCOUNTANT: "Akuntan",
    CLIENT: "Klien (lihat saja)",
}
ROLE_DESCRIPTIONS = {
    ADMINISTRATOR: "Akses penuh: kelola pengguna, Chart of Accounts, jurnal, dan rekening koran.",
    ACCOUNTANT: "Mengimpor rekening koran, membuat, memposting, dan membalik jurnal. Tidak dapat mengelola pengguna.",
    CLIENT: "Hanya dapat melihat ringkasan, jurnal, rekening koran, dan laporan. Tidak dapat mengubah data.",
}
ROLE_CHOICES = [(code, ROLE_LABELS[code]) for code in (CLIENT, ACCOUNTANT, ADMINISTRATOR)]
SUPERUSER = "superuser"

LEDGER_MODELS = ["journalheader", "journalline", "bankstatement", "bankstatementline", "bankstatementdocument"]
ACTIONS = ["add", "change", "delete", "view"]


def _codenames():
    ledger_all = [("ledger", f"{action}_{model}") for model in LEDGER_MODELS for action in ACTIONS]
    ledger_view = [("ledger", f"view_{model}") for model in LEDGER_MODELS]
    accounts_view = [("accounts", "view_account"), ("accounts", "view_currency")]
    return {
        ADMINISTRATOR: ledger_all
        + [("accounts", f"{action}_{model}") for model in ("account", "currency") for action in ACTIONS]
        + [("auth", f"{action}_user") for action in ACTIONS]
        + [("audit", "view_ledgeraudittrail"), ("audit", "view_securityeventlog")],
        ACCOUNTANT: ledger_all + accounts_view + [("accounts", "add_account"), ("accounts", "change_account")],
        CLIENT: ledger_view + accounts_view,
    }


def ensure_roles():
    """Create or refresh the role groups. Safe to call repeatedly."""
    groups = {}
    for role, codenames in _codenames().items():
        group, _ = Group.objects.get_or_create(name=ROLE_GROUPS[role])
        permissions = []
        for app_label, codename in codenames:
            permission = Permission.objects.filter(content_type__app_label=app_label, codename=codename).first()
            if permission:
                permissions.append(permission)
        group.permissions.set(permissions)
        groups[role] = group
    return groups


def role_of(user) -> str | None:
    if user.is_superuser:
        return SUPERUSER
    names = {group.name for group in user.groups.all()}
    for role in (ADMINISTRATOR, ACCOUNTANT, CLIENT):
        if ROLE_GROUPS[role] in names:
            return role
    return None


def role_label(role) -> str:
    return "Super admin" if role == SUPERUSER else ROLE_LABELS.get(role, "Tanpa peran")


def assign_role(user, role):
    groups = ensure_roles()
    user.groups.remove(*[group for code, group in groups.items() if code != role])
    user.groups.add(groups[role])
    # Administrators and accountants may open /admin/ (Chart of Accounts); clients may not.
    staff = role in (ADMINISTRATOR, ACCOUNTANT)
    if user.is_staff != staff and not user.is_superuser:
        user.is_staff = staff
        user.save(update_fields=["is_staff"])


def can_manage_users(user) -> bool:
    return bool(user.is_authenticated and user.is_active and (user.is_superuser or user.has_perm("auth.change_user")))


def is_manager_account(user) -> bool:
    """Accounts that count toward 'at least one active administrator'."""
    return user.is_active and (user.is_superuser or user.groups.filter(name=ROLE_GROUPS[ADMINISTRATOR]).exists())
