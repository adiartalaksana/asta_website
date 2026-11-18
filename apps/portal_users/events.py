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
}


def client_ip(request):
    # REMOTE_ADDR only: X-Forwarded-For can be forged unless a trusted proxy sets it.
    return request.META.get("REMOTE_ADDR") if request is not None else None


def log_event(request, event_type, *, actor=None, **details):
    if actor is None and request is not None and getattr(request, "user", None) and request.user.is_authenticated:
        actor = request.user
    SecurityEventLog.objects.create(actor=actor, event_type=event_type, details=details, ip_address=client_ip(request))
