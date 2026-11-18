from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .events import log_event


@receiver(user_logged_in)
def record_login(sender, request, user, **kwargs):
    log_event(request, "auth.login", actor=user, username=user.get_username())


@receiver(user_logged_out)
def record_logout(sender, request, user, **kwargs):
    if user is not None:
        log_event(request, "auth.logout", actor=user, username=user.get_username())


@receiver(user_login_failed)
def record_login_failure(sender, credentials, request=None, **kwargs):
    # Never store the attempted password; only the username.
    log_event(request, "auth.login_failed", actor=None, username=str(credentials.get("username", ""))[:150])
