from apps.audit.models import SecurityEventLog

EVENT_LABELS = {
    "auth.login": "Masuk ke portal",
    "auth.logout": "Keluar dari portal",
    "auth.login_failed": "Gagal masuk",
    "user.created": "Membuat akun",
    "user.updated": "Mengubah akun",
    "user.activated": "Mengaktifkan akun",
    "user.deactivated": "Menonaktifkan akun",
    "user.password_reset": "Mengatur ulang kata sandi",
    "user.password_changed": "Mengganti kata sandi sendiri",
    "user.deleted": "Menghapus akun",
    "agent.updated": "Mengubah agen AI",
    "agent.restored": "Memulihkan versi agen AI",
    "internal.unlocked": "Membuka area internal dengan token",
    "internal.locked": "Mengunci CMS",
    "internal.unlock_failed": "Token akses salah",
}

EVENT_GROUPS = {
    "auth": ("Login & keluar", ["auth.login", "auth.logout", "auth.login_failed"]),
    "access": ("Token akses internal", ["internal.unlocked", "internal.unlock_failed", "internal.locked"]),
    "user": ("Perubahan akun", ["user.created", "user.updated", "user.activated", "user.deactivated", "user.password_reset", "user.password_changed", "user.deleted"]),
    "agent": ("Agen AI", ["agent.updated", "agent.restored"]),
}


def client_ip(request):
    # REMOTE_ADDR only: X-Forwarded-For can be forged unless a trusted proxy sets it.
    return request.META.get("REMOTE_ADDR") if request is not None else None


def log_event(request, event_type, *, actor=None, **details):
    if actor is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        actor = request.user
    SecurityEventLog.objects.create(actor=actor, event_type=event_type, details=details, ip_address=client_ip(request))


def cms_event(request, event_type, **details):
    """CMS actions have no portal login: record them as done through the access token."""
    SecurityEventLog.objects.create(actor=None, event_type=event_type, details={**details, "via": "cms"}, ip_address=client_ip(request))
