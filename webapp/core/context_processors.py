from .roles import CAPABILITIES, can, get_role


def role_context(request):
    """Adds `user_role` and `allowed` (capability -> bool) to every template."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"user_role": None, "allowed": {}}
    return {
        "user_role": get_role(user),
        "allowed": {name: can(user, name) for name in CAPABILITIES},
    }
