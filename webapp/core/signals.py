from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver

from .audit import log_action
from .models import AuditLog


@receiver(user_logged_in)
def on_login(sender, request, user, **kwargs):
    log_action(user, AuditLog.LOGIN, "Signed in", request=request)


@receiver(user_logged_out)
def on_logout(sender, request, user, **kwargs):
    if user is not None:
        log_action(user, AuditLog.LOGOUT, "Signed out", request=request)


@receiver(user_login_failed)
def on_login_failed(sender, credentials, request=None, **kwargs):
    name = (credentials or {}).get("username", "")
    log_action(None, AuditLog.LOGIN_FAILED, f"Failed sign-in for '{name}'",
               request=request, username=name[:150])
