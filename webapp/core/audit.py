"""One helper to write audit entries, so every view logs the same way."""
from decimal import Decimal

from .models import AuditLog


def client_ip(request):
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _plain(value):
    """Make values JSON-safe for the changes field."""
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "pk"):
        return str(value)
    return value


def diff(instance_before, form):
    """{field: [old, new]} for every field the form changed."""
    return {
        name: [_plain(instance_before.get(name)), _plain(form.cleaned_data.get(name))]
        for name in form.changed_data
    }


def log_action(user, action, description="", obj=None, changes=None, request=None,
               username=None):
    is_real_user = user is not None and getattr(user, "is_authenticated", False)
    return AuditLog.objects.create(
        user=user if is_real_user else None,
        username=username or (user.get_username() if is_real_user else ""),
        action=action,
        object_type=obj.__class__.__name__ if obj is not None else "",
        object_id=str(obj.pk) if obj is not None and obj.pk is not None else "",
        description=description[:500],
        changes=changes or None,
        ip_address=client_ip(request),
    )
