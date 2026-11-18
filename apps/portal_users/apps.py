from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PortalUsersConfig(AppConfig):
    name = "apps.portal_users"
    label = "portal_users"
    verbose_name = "Pengguna portal"

    def ready(self):
        from . import signals  # noqa: F401 - login/logout audit receivers
        from .roles import ensure_roles

        post_migrate.connect(lambda **kwargs: ensure_roles(), sender=self, weak=False)
