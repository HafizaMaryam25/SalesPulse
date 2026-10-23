"""Run with SQL Server:          python manage.py test core
Run without SQL Server:          set DB_ENGINE=sqlite  (Windows)  then  python manage.py test core

Warehouse queries are mocked, so these tests check the web app's own rules:
roles, CRUD, audit log, upload validation and the API.
"""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import Group, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from core import services
from core.models import AuditLog, DataUpload, InventoryItem
from core.roles import ADMIN, ANALYST, VIEWER, can, get_role

FAKE_KPI = {"total_revenue": 1000.0, "total_freight": 100.0, "total_orders": 10, "total_units": 12,
            "avg_order_value": 100.0, "total_customers": 9, "repeat_customers": 1,
            "repeat_rate_pct": 11.1, "avg_review_score": 4.1, "late_delivery_rate_pct": 7.5,
            "avg_delivery_days": 12.0}


def warehouse_mocks():
    """Patch every warehouse call with small fake results."""
    return mock.patch.multiple(
        services,
        available_years=mock.MagicMock(return_value=[2017, 2018]),
        data_range=mock.MagicMock(return_value={"first": date(2016, 9, 4), "last": date(2018, 10, 17)}),
        kpi_summary=mock.MagicMock(return_value=FAKE_KPI),
        monthly_revenue=mock.MagicMock(return_value=[
            {"month_start": date(2018, 1, 1), "revenue": 500.0, "orders": 150, "mom_growth_pct": None},
            {"month_start": date(2018, 2, 1), "revenue": 600.0, "orders": 160, "mom_growth_pct": 20.0}]),
        top_categories=mock.MagicMock(return_value=[
            {"category": "health_beauty", "orders": 5, "units": 6, "revenue": 600.0,
             "revenue_share_pct": 60.0, "revenue_rank": 1}]),
        region_sales=mock.MagicMock(return_value=[{"region": "Southeast", "orders": 8, "revenue": 800.0, "states": 4}]),
        delivery_trend=mock.MagicMock(return_value=[]),
        review_vs_delay=mock.MagicMock(return_value=[]),
        recent_etl_runs=mock.MagicMock(return_value=[]),
        warehouse_categories=mock.MagicMock(return_value=["health_beauty"]),
        top_products=mock.MagicMock(return_value=[]),
        category_summary=mock.MagicMock(return_value=[]),
    )


class RoleTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_roles", verbosity=0)
        cls.users = {}
        for name, role in (("admin", ADMIN), ("analyst", ANALYST), ("viewer", VIEWER)):
            user = User.objects.create_user(name, password="test-pass-123")
            user.groups.add(Group.objects.get(name=role))
            cls.users[role] = user
        cls.item = InventoryItem.objects.create(sku="SP-00001", name="Face serum", category="health_beauty",
                                                unit_price=Decimal("50.00"), unit_cost=Decimal("20.00"),
                                                stock_qty=3, reorder_level=10)

    def login(self, role):
        self.client.force_login(self.users[role])

    def setUp(self):
        self.patcher = warehouse_mocks()
        self.patcher.start()
        self.addCleanup(self.patcher.stop)


class RoleRulesTests(RoleTestCase):
    def test_roles_resolve(self):
        self.assertEqual(get_role(self.users[ADMIN]), ADMIN)
        self.assertEqual(get_role(self.users[VIEWER]), VIEWER)
        self.assertTrue(can(self.users[ANALYST], "upload_data"))
        self.assertFalse(can(self.users[VIEWER], "upload_data"))
        self.assertFalse(can(self.users[ANALYST], "delete_inventory"))

    def test_user_without_group_is_viewer(self):
        nobody = User.objects.create_user("nobody", password="x-pass-12345")
        self.assertEqual(get_role(nobody), VIEWER)


class PageAccessTests(RoleTestCase):
    def test_anonymous_is_sent_to_login(self):
        response = self.client.get(reverse("dashboard"))
        self.assertRedirects(response, f"{reverse('login')}?next=/")

    def test_dashboard_for_every_role(self):
        for role in (ADMIN, ANALYST, VIEWER):
            self.login(role)
            response = self.client.get(reverse("dashboard") + "?year=2018")
            self.assertEqual(response.status_code, 200, role)
            self.assertContains(response, "R$")

    def test_viewer_cannot_upload_or_edit(self):
        self.login(VIEWER)
        self.assertEqual(self.client.get(reverse("upload")).status_code, 403)
        self.assertEqual(self.client.get(reverse("inventory_create")).status_code, 403)
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 403)
        self.assertEqual(self.client.get(reverse("inventory_list")).status_code, 200)

    def test_analyst_can_edit_but_not_delete(self):
        self.login(ANALYST)
        self.assertEqual(self.client.get(reverse("inventory_create")).status_code, 200)
        self.assertEqual(self.client.post(reverse("inventory_delete", args=[self.item.pk])).status_code, 403)

    def test_admin_sees_audit_log(self):
        self.login(ADMIN)
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 200)


class InventoryCrudTests(RoleTestCase):
    def payload(self, **overrides):
        data = {"sku": "sp-00002", "name": "Yoga mat", "category": "Sports leisure",
                "unit_price": "80.00", "unit_cost": "30.00", "stock_qty": "40",
                "reorder_level": "10", "is_active": "on"}
        data.update(overrides)
        return data

    def test_create_writes_audit_entry(self):
        self.login(ANALYST)
        response = self.client.post(reverse("inventory_create"), self.payload())
        self.assertRedirects(response, reverse("inventory_list"))
        item = InventoryItem.objects.get(sku="SP-00002")          # SKU upper-cased
        self.assertEqual(item.category, "sports_leisure")          # category normalised
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.CREATE, object_id=str(item.pk)).exists())

    def test_cost_above_price_is_rejected(self):
        self.login(ANALYST)
        response = self.client.post(reverse("inventory_create"), self.payload(unit_cost="99.00"))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(InventoryItem.objects.filter(sku="SP-00002").exists())

    def test_update_logs_changed_fields(self):
        self.login(ANALYST)
        data = self.payload(sku="SP-00001", name="Face serum", category="health_beauty",
                            unit_price="50.00", unit_cost="20.00", stock_qty="25")
        self.client.post(reverse("inventory_update", args=[self.item.pk]), data)
        entry = AuditLog.objects.get(action=AuditLog.UPDATE)
        self.assertIn("stock_qty", entry.changes)
        self.assertEqual(entry.changes["stock_qty"], [3, 25])

    def test_admin_delete(self):
        self.login(ADMIN)
        self.client.post(reverse("inventory_delete", args=[self.item.pk]))
        self.assertFalse(InventoryItem.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.DELETE).exists())

    def test_status_filter(self):
        self.login(VIEWER)
        response = self.client.get(reverse("inventory_list") + "?status=low")
        self.assertContains(response, "SP-00001")
        response = self.client.get(reverse("inventory_list") + "?status=out")
        self.assertNotContains(response, "SP-00001")


class UploadTests(RoleTestCase):
    def test_missing_columns_are_rejected(self):
        self.login(ANALYST)
        bad = SimpleUploadedFile("orders.csv", b"order_id,foo\n1,2\n", content_type="text/csv")
        response = self.client.post(reverse("upload"), {"dataset": "orders", "file": bad}, follow=True)
        self.assertContains(response, "Missing required columns")
        upload = DataUpload.objects.get()
        self.assertEqual(upload.status, DataUpload.REJECTED)
        self.assertTrue(AuditLog.objects.filter(action=AuditLog.UPLOAD_REJECTED).exists())

    def test_non_csv_is_rejected_by_form(self):
        self.login(ANALYST)
        f = SimpleUploadedFile("orders.xlsx", b"abc")
        response = self.client.post(reverse("upload"), {"dataset": "orders", "file": f})
        self.assertContains(response, "Choose a .csv file.")
        self.assertFalse(DataUpload.objects.exists())

    def test_valid_file_starts_etl(self):
        self.login(ANALYST)
        csv = (b"product_category_name,product_category_name_english\n"
               b"beleza_saude,health_beauty\n")
        f = SimpleUploadedFile("translation.csv", csv, content_type="text/csv")
        with mock.patch("core.views.etl_runner.store_upload") as store, \
             mock.patch("core.views.etl_runner.start") as start:
            store.return_value = mock.MagicMock(name="path")
            store.return_value.name = "product_category_name_translation.csv"
            self.client.post(reverse("upload"), {"dataset": "category_translation", "file": f})
        upload = DataUpload.objects.get()
        self.assertEqual(upload.status, DataUpload.RUNNING)
        start.assert_called_once_with(upload.pk)


class ApiTests(RoleTestCase):
    def test_requires_login(self):
        self.assertIn(self.client.get(reverse("api_sales_summary")).status_code, (401, 403))

    def test_sales_summary_json(self):
        self.login(VIEWER)
        data = self.client.get(reverse("api_sales_summary") + "?year=2018").json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["kpis"]["total_orders"], 10)
        self.assertEqual(data["currency"], "BRL")

    def test_inventory_api_permissions(self):
        url = reverse("api-inventory-detail", args=[self.item.pk])
        self.login(VIEWER)
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.patch(url, {"stock_qty": 5}, content_type="application/json").status_code, 403)
        self.login(ANALYST)
        self.assertEqual(self.client.patch(url, {"stock_qty": 5}, content_type="application/json").status_code, 200)
        self.assertEqual(self.client.delete(url).status_code, 403)
        self.login(ADMIN)
        self.assertEqual(self.client.delete(url).status_code, 204)


class PeriodTests(TestCase):
    def test_partial_year_compares_same_months(self):
        rng = {"first": date(2016, 9, 4), "last": date(2018, 10, 17)}
        p = services.get_period(2018, rng)
        self.assertEqual((p.start, p.end), (date(2018, 1, 1), date(2018, 10, 17)))
        self.assertEqual((p.prev_start, p.prev_end), (date(2017, 1, 1), date(2017, 10, 17)))

    def test_first_year_has_no_comparison(self):
        rng = {"first": date(2016, 9, 4), "last": date(2018, 10, 17)}
        self.assertIsNone(services.get_period(2016, rng).prev_start)


class DeveloperToolsTests(RoleTestCase):
    def test_developer_links_hidden_from_viewers(self):
        api_url, admin_url = reverse("api_sales_summary"), reverse("admin:index")
        with warehouse_mocks():
            self.login(VIEWER)
            page = self.client.get(reverse("dashboard")).content.decode()
            self.assertNotIn(api_url, page)
            self.assertNotIn(admin_url, page)
            self.login(ANALYST)
            page = self.client.get(reverse("dashboard")).content.decode()
            self.assertIn(api_url, page)
            self.assertNotIn(admin_url, page)          # analyst is not staff

    def test_admin_and_api_pages_use_the_salespulse_skin(self):
        staff = User.objects.create_superuser("boss", password="test-pass-123")
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse("admin:index")), "core/css/admin-skin.css")
        with warehouse_mocks():
            self.assertContains(self.client.get(reverse("api_sales_summary"), HTTP_ACCEPT="text/html"),
                                "core/css/api-skin.css")

    def test_edits_in_django_admin_are_audited(self):
        staff = User.objects.create_superuser("boss", password="test-pass-123")
        self.client.force_login(staff)
        url = reverse("admin:core_inventoryitem_change", args=[self.item.pk])
        data = {"sku": self.item.sku, "name": self.item.name, "category": self.item.category,
                "product_id": "", "unit_price": "50.00", "unit_cost": "20.00",
                "stock_qty": 77, "reorder_level": self.item.reorder_level, "is_active": "on"}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        entry = AuditLog.objects.filter(action=AuditLog.UPDATE, object_id=str(self.item.pk)).latest("created_at")
        self.assertIn("Django admin", entry.description)
        self.assertEqual(entry.changes["stock_qty"][1], "77")
