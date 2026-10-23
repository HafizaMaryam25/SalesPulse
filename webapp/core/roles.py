"""Three roles, stored as Django Groups: Admin, Analyst, Viewer.

Views ask "can this user do X?" through `can()` instead of checking group
names everywhere, so the permission matrix lives in one place.
"""
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import SAFE_METHODS, BasePermission

ADMIN, ANALYST, VIEWER = "Admin", "Analyst", "Viewer"
ROLES = [ADMIN, ANALYST, VIEWER]          # highest first

CAPABILITIES = {
    "view_dashboard":   {ADMIN, ANALYST, VIEWER},
    "view_inventory":   {ADMIN, ANALYST, VIEWER},
    "edit_inventory":   {ADMIN, ANALYST},
    "delete_inventory": {ADMIN},
    "upload_data":      {ADMIN, ANALYST},
    "view_etl":         {ADMIN, ANALYST},
    "view_audit":       {ADMIN},
    # Sidebar link to the browsable REST API. Viewers can still call the
    # read-only endpoints; the link is just not shown to them.
    "use_api":          {ADMIN, ANALYST},
}


def get_role(user):
    if not user or not user.is_authenticated:
        return None
    if user.is_superuser:
        return ADMIN
    if not hasattr(user, "_sp_role"):
        groups = set(user.groups.values_list("name", flat=True))
        user._sp_role = next((r for r in ROLES if r in groups), VIEWER)  # no group = read-only
    return user._sp_role


def can(user, capability):
    return get_role(user) in CAPABILITIES[capability]


def capability_required(capability):
    """View decorator: sign-in required, then 403 unless the role allows it."""
    def decorator(view):
        @wraps(view)
        @login_required
        def wrapper(request, *args, **kwargs):
            if not can(request.user, capability):
                raise PermissionDenied(f"Your role cannot {capability.replace('_', ' ')}.")
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


class InventoryPermission(BasePermission):
    """API: everyone signed in can read, Analyst+ can write, only Admin can delete."""

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False
        if request.method in SAFE_METHODS:
            return can(request.user, "view_inventory")
        if request.method == "DELETE":
            return can(request.user, "delete_inventory")
        return can(request.user, "edit_inventory")
