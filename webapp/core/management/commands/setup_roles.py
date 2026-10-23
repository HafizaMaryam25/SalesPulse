"""Create the Admin / Analyst / Viewer groups and (optionally) one demo user each.

    python manage.py setup_roles
    python manage.py setup_roles --demo-users --password "Choose-A-Strong-One1"
"""
from django.contrib.auth.models import Group, Permission, User
from django.core.management.base import BaseCommand, CommandError

from core.roles import ADMIN, ANALYST, VIEWER

# Django model permissions per role (used by /admin and as a second safety net)
ROLE_PERMISSIONS = {
    ADMIN: ["add_inventoryitem", "change_inventoryitem", "delete_inventoryitem", "view_inventoryitem",
            "view_auditlog", "add_dataupload", "view_dataupload"],
    ANALYST: ["add_inventoryitem", "change_inventoryitem", "view_inventoryitem",
              "add_dataupload", "view_dataupload"],
    VIEWER: ["view_inventoryitem"],
}


class Command(BaseCommand):
    help = "Create SalesPulse roles and optional demo users"

    def add_arguments(self, parser):
        parser.add_argument("--demo-users", action="store_true",
                            help="also create users admin, analyst and viewer")
        parser.add_argument("--password", default="",
                            help="password for the demo users (required with --demo-users)")

    def handle(self, *args, **opts):
        for role, codenames in ROLE_PERMISSIONS.items():
            group, created = Group.objects.get_or_create(name=role)
            perms = Permission.objects.filter(content_type__app_label="core", codename__in=codenames)
            group.permissions.set(perms)
            self.stdout.write(f"{'Created' if created else 'Updated'} group {role} "
                              f"({perms.count()} permissions)")

        if not opts["demo_users"]:
            return
        password = opts["password"]
        if len(password) < 10:
            raise CommandError("Give --password with at least 10 characters.")

        for username, role in (("admin", ADMIN), ("analyst", ANALYST), ("viewer", VIEWER)):
            user, created = User.objects.get_or_create(username=username, defaults={
                "email": f"{username}@salespulse.local", "is_staff": role == ADMIN})
            user.set_password(password)
            user.save()
            user.groups.set([Group.objects.get(name=role)])
            self.stdout.write(self.style.SUCCESS(
                f"{'Created' if created else 'Reset'} user '{username}' with role {role}"))
