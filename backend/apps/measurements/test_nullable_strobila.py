"""NULL strobilae mean "not measured"; 0 stays a measured scientific zero."""

from datetime import date

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.measurements.forms import BiologicalMeasurementForm
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain


class NullableStrobilaModelTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Strobila lab")
        species = Species.objects.create(scientific_name="Aurelia strobila", genus_species_code="AST")
        strain = Strain.objects.create(species=species, organization=self.org, code="AST-QA-1")
        self.box = Box.objects.create(organization=self.org, strain=strain, global_code="AST-QA-1.001", box_number="001")

    def test_historical_row_accepts_null_and_zero_stays_zero(self):
        unknown = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 1, 5), polyp_count=4, ephyrae_count=0, strobila_count=None
        )
        zero = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 1, 12), polyp_count=4, ephyrae_count=0, strobila_count=0
        )
        default = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 1, 19), polyp_count=4, ephyrae_count=0
        )
        for row in (unknown, zero, default):
            row.refresh_from_db()
        self.assertIsNone(unknown.strobila_count)
        self.assertEqual(zero.strobila_count, 0)
        self.assertEqual(default.strobila_count, 0)  # the live default is unchanged
        self.assertEqual(list(BiologicalMeasurement.objects.filter(strobila_count__isnull=True)), [unknown])

    def test_entry_form_still_requires_an_explicit_strobila_value(self):
        data = {
            "measured_on": "2026-01-05",
            "polyp_count": 1,
            "ephyrae_count": 0,
            "culture_status": "good",
            "strobila_count": "",
        }
        self.assertIn("strobila_count", BiologicalMeasurementForm(data).errors)
        data["strobila_count"] = "0"
        self.assertTrue(BiologicalMeasurementForm(data).is_valid())


class NullableStrobilaApiTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Strobila API lab")
        self.user = get_user_model().objects.create_user(username="strobila-admin", email="strobila@example.org", password="secret")
        OrganizationMembership.objects.create(user=self.user, organization=self.org, role="admin")
        species = Species.objects.create(scientific_name="Aurelia api", genus_species_code="AAP")
        strain = Strain.objects.create(species=species, organization=self.org, code="AAP-QA-1")
        self.box = Box.objects.create(
            organization=self.org, strain=strain, global_code="AAP-QA-1.001", box_number="001",
            status=Box.Status.ACTIVE,
        )
        self.client.force_login(self.user)
        self.headers = {"HTTP_X_ORGANIZATION_ID": str(self.org.pk)}

    def historical(self, week, strobila):
        return BiologicalMeasurement.objects.create(
            box=self.box,
            measured_on=date.fromisocalendar(2026, week, 1),
            polyp_count=80,
            ephyrae_count=0,
            strobila_count=strobila,
        )

    def test_read_api_reports_null_distinctly_from_zero(self):
        unknown, zero = self.historical(1, None), self.historical(2, 0)
        response = self.client.get(reverse("api_box_measurements", args=[self.box.pk]), **self.headers)
        rows = {row["id"]: row for row in response.json()["results"]}
        self.assertIsNone(rows[unknown.pk]["strobila_count"])
        self.assertEqual(rows[zero.pk]["strobila_count"], 0)

    def test_live_entry_cannot_write_null_but_still_defaults_to_zero(self):
        url = reverse("api_box_measurements", args=[self.box.pk])
        rejected = self.client.post(
            url,
            {"measured_on": "2026-02-02", "polyp_count": 1, "ephyrae_count": 0, "strobila_count": None},
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(rejected.status_code, 400)
        created = self.client.post(
            url,
            {"measured_on": "2026-02-02", "polyp_count": 1, "ephyrae_count": 0},
            content_type="application/json",
            **self.headers,
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["strobila_count"], 0)

    def test_correcting_a_historical_row_keeps_null_and_audits_it_as_null(self):
        row = self.historical(3, None)
        url = reverse("api_box_measurement_detail", args=[self.box.pk, row.pk])
        rejected = self.client.patch(url, {"strobila_count": None}, content_type="application/json", **self.headers)
        self.assertEqual(rejected.status_code, 400)
        response = self.client.patch(url, {"polyp_count": 81}, content_type="application/json", **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["strobila_count"])
        row.refresh_from_db()
        self.assertIsNone(row.strobila_count)
        audit = AuditLog.objects.get(object_id=self.box.global_code, action=AuditLog.Action.UPDATE)
        self.assertIsNone(audit.metadata["after"]["strobiles"])
        self.assertNotIn("strobiles", audit.metadata["modifications"])

    def test_dashboard_total_is_null_when_nothing_was_measured_and_zero_when_measured(self):
        url = reverse("api_dashboard")

        def total():
            return self.client.get(url, **self.headers).json()["stats"]["measured_strobilae"]

        self.assertEqual(total(), 0)  # no measurement at all
        self.historical(1, None)
        self.assertIsNone(total())
        self.historical(2, 0)
        self.assertEqual(total(), 0)
        self.historical(3, 4)
        self.assertEqual(total(), 4)


class NullableStrobilaMigrationTests(TransactionTestCase):
    def test_upgrade_does_not_rewrite_existing_rows(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [node for node in latest if node[0] != "measurements"] + [
            ("measurements", "0006_quantitative_subculture")
        ]
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        org = apps.get_model("organizations", "Organization").objects.create(name="Strobila upgrade")
        species = apps.get_model("taxonomy", "Species").objects.create(scientific_name="Aurelia upgrade")
        strain = apps.get_model("taxonomy", "Strain").objects.create(species=species, code="UPG-QA-1")
        box = apps.get_model("cultures", "Box").objects.create(
            organization=org, strain=strain, global_code="UPG-QA-1.001", box_number="001"
        )
        Measurement = apps.get_model("measurements", "BiologicalMeasurement")
        for index, value in enumerate((0, 0, 7)):
            day = date(2026, 1, 5 + 7 * index)
            Measurement.objects.create(
                box=box, measured_on=day, week_start=day,
                polyp_count=10 + index, ephyrae_count=index, strobila_count=value,
            )
        snapshot = list(Measurement.objects.order_by("pk").values())

        executor = MigrationExecutor(connection)
        executor.migrate(latest)
        Measurement = executor.loader.project_state(latest).apps.get_model("measurements", "BiologicalMeasurement")
        self.assertEqual(list(Measurement.objects.order_by("pk").values()), snapshot)
        self.assertEqual(list(Measurement.objects.order_by("pk").values_list("strobila_count", flat=True)), [0, 0, 7])
        self.assertFalse(Measurement.objects.filter(strobila_count__isnull=True).exists())
        Measurement.objects.create(
            box_id=box.pk, measured_on=date(2026, 3, 2), week_start=date(2026, 3, 2),
            polyp_count=1, ephyrae_count=0, strobila_count=None,
        )
        self.assertEqual(Measurement.objects.filter(strobila_count__isnull=True).count(), 1)
