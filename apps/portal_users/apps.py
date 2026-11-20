from django.apps import AppConfig
from django.db.models.signals import post_migrate


class PortalUsersConfig(AppConfig):
    name = "apps.portal_users"
    label = "portal_users"
    verbose_name = "Pengguna portal"

    def ready(self):
        from . import signals  # noqa: F401 - login/logout audit receivers
        from .roles import ensure_roles

        def setup(**kwargs):
            from apps.ai_engine.agents import ensure_agents

            ensure_roles()
            ensure_agents()

        post_migrate.connect(setup, sender=self, weak=False)
