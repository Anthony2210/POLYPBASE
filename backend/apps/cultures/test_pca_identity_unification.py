"""Synthetic exact-ID tests. Run only on the independent, approved PostgreSQL QA."""

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import close_old_connections, connection, connections, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.audit.services import (
    classify_audit_log, impactful_audit_logs, resolve_audit_box_references, resolve_audit_measurements,
)
from apps.measurements import services as measurement_services
from apps.measurements.models import BiologicalMeasurement, MeasurementImportReceipt, Observation
from apps.organizations.models import Organization
from apps.taxonomy.models import Origin, Species, Strain

from . import pca_identity_unification as merge
from .historical_2026 import importer
from .test_historical_2026_import import box_spec, item, make_manifest
from .models import Box, BoxCodeNamespace, BoxLocation, BoxMovement, IdentificationTag, ThermalZone


@override_settings(HISTORICAL_TEMPERATURE_PGQA=False)
class DatabaseGuardTests(SimpleTestCase):
    def config(self, target=("127.0.0.1", "65432", "synthetic_pca_qa"), **overrides):
        config = {"ENGINE": "django.db.backends.postgresql", "HOST": target[0],
                  "PORT": target[1], "NAME": target[2], "OPTIONS": {}}
        config.update(overrides)
        return config

    @override_settings(HISTORICAL_TEMPERATURE_PGQA=True,
                       SETTINGS_MODULE="config.historical_temperature_pgqa_settings")
    def test_dedicated_parent_provisioned_target_requires_harness_validation(self):
        from types import ModuleType
        from unittest.mock import Mock

        harness = ModuleType("config.historical_temperature_pgqa")
        harness.assert_config = Mock()
        with patch.dict(sys.modules, {"config.historical_temperature_pgqa": harness}), patch.object(merge, "connection") as mocked:
            mocked.settings_dict = self.config(PORT="65432", NAME="synthetic_temperature_qa")
            self.assertEqual(merge.require_local_database(apply=True),
                             ("127.0.0.1", "65432", "synthetic_temperature_qa"))
            harness.assert_config.assert_called_once_with(mocked.settings_dict)
            mocked.ensure_connection.assert_not_called()
            harness.assert_config.side_effect = RuntimeError("Unapproved QA target")
            with self.assertRaisesMessage(RuntimeError, "Unapproved QA target"):
                merge.require_local_database(apply=True)
            mocked.ensure_connection.assert_not_called()

    def test_configuration_rejections_are_preconnect(self):
        configs = [self.config(HOST="shared.example.org"), self.config(NAME="polypbase"),
                   self.config(ENGINE="django.db.backends.sqlite3"), self.config(HOST="localhost"),
                   self.config(OPTIONS={"service": "redirect"}),
                   self.config(OPTIONS={"options": "-c search_path=other"}),
                   self.config(OPTIONS={"hostaddr": "192.0.2.1"}),
                   *[self.config(OPTIONS={"options": value}) for value in (
                       "", None, "-c lock_timeout=10000", merge.QA_TIMEOUT_OPTIONS + " ",
                       merge.QA_TIMEOUT_OPTIONS + " -c role=other",
                       "-c statement_timeout=30000 -c lock_timeout=10000",
                   )]]
        for config in configs:
            with self.subTest(config=config), patch.object(merge, "connection") as mocked:
                mocked.settings_dict = config
                with self.assertRaises(ValueError):
                    merge.require_local_database(apply=True)
                mocked.ensure_connection.assert_not_called()

    def test_timeout_options_do_not_authorize_an_unguarded_target(self):
        with patch.object(merge, "connection") as mocked, patch.dict(os.environ, {}, clear=True):
            mocked.settings_dict = self.config(OPTIONS={"options": "-c lock_timeout=10000 -c statement_timeout=30000"})
            with self.assertRaisesMessage(ValueError, "disposable PostgreSQL QA harness"):
                merge.require_local_database(apply=True)
            mocked.ensure_connection.assert_not_called()

    def test_historical_is_read_only_and_orm_schema_gate_is_preconnect(self):
        with patch.object(merge, "connection") as mocked, patch.dict(os.environ, {}, clear=True):
            mocked.settings_dict = self.config(("127.0.0.1", "55438", "synthetic_forbidden"))
            with self.assertRaises(ValueError):
                merge.require_local_database()
            with self.assertRaises(ValueError):
                merge.require_local_database(apply=True)
            with self.assertRaises(ValueError):
                merge.build_plan(1, actor=SimpleNamespace(pk=1))
            mocked.ensure_connection.assert_not_called()

    def test_libpq_environment_redirections_are_rejected(self):
        for key, value in (("PGSERVICE", "remote"), ("PGSERVICEFILE", "remote.conf"),
                           ("PGHOSTADDR", "192.0.2.1"), ("PGOPTIONS", "-c role=other")):
            with self.subTest(key=key), patch.object(merge, "connection") as mocked:
                mocked.settings_dict = self.config()
                with patch.dict(os.environ, {key: value}, clear=True), self.assertRaises(ValueError):
                    merge.require_local_database()
                mocked.ensure_connection.assert_not_called()

    def test_effective_connection_must_match_configuration(self):
        with patch.object(merge, "connection") as mocked, patch.object(
            merge, "require_local_database", return_value=("127.0.0.1", "65432", "synthetic_pca_qa"),
        ):
            mocked.settings_dict = self.config()
            mocked.connection.info = SimpleNamespace(hostaddr="192.0.2.1", port=65432, dbname="synthetic_pca_qa")
            with self.assertRaisesMessage(ValueError, "effective PostgreSQL"):
                merge._verify_connection(apply=True)

    def test_command_rejects_historical_apply_before_actor_lookup(self):
        with patch.object(merge, "connection") as mocked, patch.dict(os.environ, {}, clear=True):
            mocked.settings_dict = self.config(("127.0.0.1", "55438", "synthetic_forbidden"))
            with self.assertRaises(CommandError):
                call_command("unify_pca_identity", organization_id=1, actor_id=999, apply=True,
                             expected_plan_hash="a" * 64, backup_ref="qa-backup", review_ref="qa-review")
            mocked.ensure_connection.assert_not_called()


class PcaFixtureMixin:
    def _fixture_teardown(self):
        if getattr(settings, "HISTORICAL_TEMPERATURE_PGQA", False):
            from config.historical_temperature_pgqa import cleanup_test_data

            cleanup_test_data()
        else:
            super()._fixture_teardown()

    def setUp(self):
        super().setUp()
        # Never bypass the service target guard in database tests.
        merge.require_local_database(apply=True)
        self.org = Organization.objects.create(pk=1, name="Synthetic PCA QA", slug="pca-qa")
        self.other_org = Organization.objects.create(pk=2, name="Other QA", slug="other-pca-qa")
        self.admin = get_user_model().objects.create_user(
            username="pca-admin", email="pca-admin@example.test",
        )
        self.writer = get_user_model().objects.create_user(
            username="pca-writer", email="pca-writer@example.test",
        )
        self.member = OrganizationMembership.objects.create(organization=self.org, user=self.admin, role="admin")
        OrganizationMembership.objects.create(organization=self.org, user=self.writer, role="lab_technician")
        self.species = Species.objects.create(pk=78, scientific_name="Synthetic Phacellophora QA")
        self.cho_strain = Strain.objects.create(pk=140, organization=self.org, species=self.species, code="PCA-CHO-1")
        self.jka_strain = Strain.objects.create(pk=54, organization=self.org, species=self.species, code="PCA-JKA-1")
        self.zone1 = ThermalZone.objects.create(pk=1, organization=self.org, name="Historical zone")
        self.zone2 = ThermalZone.objects.create(pk=2, organization=self.org, name="Current zone")
        self.canonical = Box.objects.create(
            pk=2135, organization=self.org, strain=self.cho_strain, global_code=merge.OLD_CODE,
            local_code="Old CHO label", box_number="005", thermal_zone=self.zone2,
            volume_liters="0.00", entered_on=date(2025, 12, 29), notes="Physical box metadata",
        )
        self.donor = Box.objects.create(
            pk=2141, organization=self.org, strain=self.jka_strain, global_code=merge.OFFICIAL_CODE,
            local_code="PCA-JKA-1.05", box_number="005", status="inactive",
            stop_reason="Historical stop", stop_reason_missing_from_history=True,
            deactivated_on=date(2026, 10, 1), notes="Keep donor metadata",
        )
        self.other_box = Box.objects.create(
            organization=self.other_org, strain=self.cho_strain, global_code="PCA-CHO-1.099", box_number="099",
        )
        def instant(year, month, day):
            return datetime(year, month, day, tzinfo=timezone.utc)
        BoxLocation.objects.bulk_create([
            BoxLocation(pk=5979, box=self.canonical, thermal_zone=self.zone1,
                        starts_at=instant(2025, 12, 29), ends_at=instant(2026, 1, 12), notes="Keep first"),
            BoxLocation(pk=5980, box=self.canonical, thermal_zone=self.zone2,
                        starts_at=instant(2026, 1, 12), notes="Keep current"),
            BoxLocation(pk=5945, box=self.donor, thermal_zone=self.zone2,
                        starts_at=instant(2024, 12, 30), end_date_unknown=True, notes="End unknown"),
        ])
        specs = ([(2141, 2025, week) for week in range(1, 53)]
                 + [(2141, 2026, week) for week in range(1, 41)]
                 + [(2135, 2026, week) for week in range(1, 19)]
                 + [(2135, 2026, 41), (2135, 2026, 42)])
        rows = []
        for index, (box_id, year, week) in enumerate(specs):
            measured_on = date.fromisocalendar(year, week, 3)
            pk = {41: 104426, 42: 104540}[week] if box_id == 2135 and week > 18 else 100000 + index
            actor = self.writer if index % 2 else self.admin
            rows.append(BiologicalMeasurement(
                pk=pk, box_id=box_id, measured_on=measured_on,
                week_start=BiologicalMeasurement.week_start_for(measured_on),
                polyp_count=week % 7, ephyrae_count=week % 3,
                strobila_count=0 if box_id == 2135 else None, salinity_psu="0.00" if index % 2 else None,
                notes="" if index % 2 else None, culture_status="good" if index % 2 else None,
                needs_attention=False if index % 2 else None,
                user=actor, original_observer=actor, observer_status="known",
                imported_by=actor, origin="file_import", import_format="csv",
                polyp_state_sequence=900 + index if index % 3 else None,
            ))
        BiologicalMeasurement.objects.bulk_create(rows)
        Box.objects.filter(pk=2135).update(polyp_state_revision=80)
        Box.objects.filter(pk=2141).update(polyp_state_revision=150)
        self.old_audit = AuditLog.objects.create(
            organization=self.org, user=self.writer, action="entry", object_type="box",
            object_id=merge.OLD_CODE, metadata={"measurement_id": 104426, "original": "keep"},
        )
        MeasurementImportReceipt.objects.create(
            organization=self.org, actor_id=self.writer.pk, source_sha256="a" * 64,
            source_bytes=b"synthetic,zero,0\n", preparation_fingerprint="b" * 64,
            plan_fingerprint="c" * 64, preparation={"synthetic": True},
            accepted_plan={"box_id": 2141}, result={"measurement_ids": [100000]},
        )

    def plan(self):
        return merge.build_plan(1, actor=self.admin)

    def apply(self, plan=None, **overrides):
        options = {"actor": self.admin, "expected_plan_hash": merge.fingerprint(self.plan() if plan is None else plan),
                   "backup_ref": "synthetic-qa-backup", "review_ref": "synthetic-independent-review"}
        options.update(overrides)
        return merge.apply_plan(1, **options)

    def historical_manifest(self, *, boxes=None):
        source_boxes = (self.canonical, self.donor) if boxes is None else boxes
        return make_manifest([
            box_spec(box.global_code, strain_code=box.strain.code,
                     species=self.species.scientific_name, local=box.local_code, number=box.box_number)
            for box in source_boxes
        ], [item(merge.OLD_CODE, 43, 0, 0)])

    def database_snapshot(self):
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def assert_blocked(self):
        plan = self.plan()
        self.assertFalse(plan["ready"], plan)
        before = self.database_snapshot()
        with self.assertRaises(ValueError):
            self.apply(plan)
        self.assertEqual(before, self.database_snapshot())


@skipUnless(connection.vendor == "postgresql", "PCA identity merge requires independent PostgreSQL QA, never SQLite.")
class PcaIdentityUnificationTests(PcaFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def test_read_only_deterministic_full_plan_and_command_default(self):
        before = self.database_snapshot()
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        self.assertEqual(plan, self.plan())
        self.assertEqual(plan["counts"], {"duplicates": 18, "moved": 92, "untouched": 2})
        self.assertEqual(len(plan["baseline"]["measurements"]), 112)
        self.assertEqual(len(plan["changes"]), 112)
        self.assertEqual(len(plan["baseline"]["locations"]), 3)
        output = StringIO()
        call_command("unify_pca_identity", organization_id=1, actor_id=self.admin.pk, stdout=output)
        self.assertEqual(json.loads(output.getvalue()), {"plan_hash": merge.fingerprint(plan), "plan": plan})
        self.assertEqual(before, self.database_snapshot())

    def test_existing_temperature_blocks_identity_merge_without_bypassing_pg_guards(self):
        from apps.measurements.models import HistoricalTemperatureEvidence, HistoricalTemperatureReading
        from apps.measurements.test_historical_temperature_plan import evidence

        biology = BiologicalMeasurement.objects.get(box=self.canonical, week_start=date(2025, 12, 29))
        with transaction.atomic():
            reading = HistoricalTemperatureReading.objects.create(
                biological_measurement=biology, iso_year=2026, iso_week=1, temperature_c="0",
            )
            HistoricalTemperatureEvidence.objects.create(reading=reading, **evidence(
                source_box=merge.OLD_CODE, source_value="0",
                polyp_value=str(biology.polyp_count), ephyrae_value=str(biology.ephyrae_count),
            ))
        plan = self.plan()
        self.assertFalse(plan["ready"])
        self.assertTrue(any("historicaltemperaturereading" in reason for reason in plan["blockers"]))
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Unresolved conflicts"):
            self.apply(plan)
        self.assertEqual(before, self.database_snapshot())
        reading.refresh_from_db()
        self.assertEqual(reading.association_status, "confirmed")

    def test_any_record_attached_to_the_reviewed_measurements_blocks_the_plan(self):
        # Stands in for a future one-to-one reading (for example historical temperature)
        # that would silently follow a moved row or a duplicate.
        attached = BiologicalMeasurement.objects.create(
            box=self.other_box, measured_on=date(2026, 3, 4), polyp_count=1, ephyrae_count=0, user=self.admin,
        )
        BiologicalMeasurement.objects.filter(pk=attached.pk).update(duplicate_of_id=100003)
        field = BiologicalMeasurement._meta.get_field("duplicate_of")
        with patch.object(merge, "measurement_dependencies", return_value=[(BiologicalMeasurement, field)]):
            plan = self.plan()
            self.assertFalse(plan["ready"])
            self.assertTrue(any("measurement dependency: measurements.biologicalmeasurement.duplicate_of_id" in b
                                for b in plan["blockers"]), plan["blockers"])
            with self.assertRaises(ValueError):
                self.apply(plan)
        self.assertEqual(Box.objects.get(pk=2141).merged_into_id, None)

    def test_exact_deltas_audits_source_order_locations_and_no_fake_events(self):
        plan = self.plan()
        other_before = merge.snapshot(self.other_box)
        logs_before = plan["baseline"]["audit_logs"]
        receipts_before = plan["baseline"]["import_receipts"]
        receipt = self.apply(plan)
        self.assertEqual(receipt.object_type, "culture_identity_merge")
        self.assertEqual(receipt.metadata["reviewed_plan"], plan)
        for change in plan["changes"]:
            model = Box if change["model"] == "box" else BiologicalMeasurement
            self.assertEqual(merge.snapshot(model.objects.get(pk=change["id"])), change["after"])
        by_pk = {m["id"]: m for m in plan["baseline"]["measurements"]}
        for measurement in BiologicalMeasurement.objects.all():
            previous = by_pk[measurement.pk]
            current = merge.snapshot(measurement)
            delta = {key for key in current if current[key] != previous[key]}
            if previous["box_id"] == 2141:
                self.assertEqual(delta, {"box_id", "environment_source_box_id"})
                self.assertEqual(measurement.environment_source_box_id, 2141)
            elif measurement.pk in (104426, 104540):
                self.assertEqual(delta, set())
            else:
                self.assertEqual(delta, {"duplicate_of_id"})
                target = measurement.duplicate_of
                self.assertEqual((target.box_id, target.week_start, target.duplicate_of_id),
                                 (2135, measurement.week_start, None))
        self.assertEqual(BiologicalMeasurement.objects.canonical().count(), 94)
        self.assertEqual(BiologicalMeasurement.objects.count(), 112)
        self.assertEqual(set(BiologicalMeasurement.objects.values_list("strobila_count", flat=True)), {0, None})
        self.assertEqual(set(BiologicalMeasurement.objects.values_list("user_id", flat=True)),
                         {self.admin.pk, self.writer.pk})
        self.assertFalse(BiologicalMeasurement.objects.filter(box_id=2141).exists())
        self.canonical.refresh_from_db()
        self.donor.refresh_from_db()
        self.assertEqual((self.canonical.global_code, self.canonical.strain_id, self.canonical.local_code),
                         ("PCA-JKA-1.005", 54, "PCA-JKA-1.05"))
        self.assertEqual(self.canonical.polyp_state_revision, 1011)
        self.assertEqual((self.donor.merged_into_id, self.donor.polyp_state_revision), (2135, 151))
        self.assertEqual(self.donor.global_code, merge.RETIRED_CODE)
        self.assertEqual(self.donor.previous_global_code, "PCA-JKA-1.005")
        self.assertEqual(self.canonical.previous_global_code, "PCA-CHO-1.005")
        self.assertEqual(merge._rows(BoxLocation.objects.all()), plan["baseline"]["locations"])
        self.assertEqual(merge._rows(Strain.objects.all()), plan["baseline"]["strains"])
        self.assertEqual(merge._rows(ThermalZone.objects.all()), plan["baseline"]["zones"])
        self.assertEqual(merge._rows(AuditLog.objects.filter(pk__in=[a["id"] for a in logs_before])), logs_before)
        self.assertEqual(merge._rows(MeasurementImportReceipt.objects.all()), receipts_before)
        self.other_box.refresh_from_db()
        self.assertEqual(merge.snapshot(self.other_box), other_before)
        self.assertEqual(AuditLog.objects.count(), 114)  # old + 110 measurements + 2 boxes + receipt
        self.assertEqual(BoxMovement.objects.count(), 0)
        self.assertEqual(Alert.objects.count(), 0)
        for namespace in ("PCA-CHO-1", "PCA-JKA-1"):
            self.assertGreaterEqual(BoxCodeNamespace.objects.get(namespace=namespace).high_water, 5)

    def test_individual_audits_use_existing_measurement_and_box_discriminators(self):
        plan = self.plan()
        self.apply(plan)
        changes = AuditLog.objects.filter(metadata__operation=merge.OPERATION).exclude(object_type=merge.RECEIPT_TYPE)
        measurements = list(changes.filter(metadata__model="measurement"))
        self.assertEqual(len(measurements), 110)
        expected_ids = {change["id"] for change in plan["changes"] if change["model"] == "measurement"}
        self.assertEqual({log.metadata["measurement_id"] for log in measurements}, expected_ids)
        self.assertEqual(set(resolve_audit_measurements(measurements, organization_id=1)), expected_ids)
        self.assertEqual(resolve_audit_measurements(measurements, organization_id=2), {})
        annotated = impactful_audit_logs(organization_id=1).filter(pk__in=[log.pk for log in measurements])
        self.assertEqual(set(annotated.values_list("business_family", flat=True)), {"measurements"})
        for log in measurements:
            self.assertEqual(classify_audit_log(log), "measurements")
            self.assertEqual((log.object_type, log.object_id, log.metadata["box_id"]),
                             ("box", merge.OFFICIAL_CODE, 2135))
            self.assertEqual(log.metadata["source_box_id"], log.metadata["before"]["box_id"])
            self.assertEqual(log.metadata["modifications"], {})  # No scientific count changed.
            self.assertEqual(log.metadata["valeurs"]["polypes"], log.metadata["before"]["polyp_count"])
            self.assertEqual(log.metadata["valeurs"]["strobiles"], log.metadata["before"]["strobila_count"])
        boxes = list(changes.filter(metadata__model="box"))
        self.assertEqual(len(boxes), 2)
        self.assertEqual({log.metadata["source_box_id"] for log in boxes}, {2135, 2141})
        self.assertEqual({log.metadata["box_id"] for log in boxes}, {2135, 2141})
        for log in boxes:
            self.assertEqual(classify_audit_log(log), "boxes")
            self.assertEqual((log.object_type, log.object_id, log.metadata["canonical_box_id"]),
                             ("box", merge.OFFICIAL_CODE, 2135))
        targets = resolve_audit_box_references([*measurements, *boxes], organization_id=1)
        self.assertEqual({code: box.pk for code, box in targets.items()}, {merge.OFFICIAL_CODE: 2135})

    def test_audit_endpoints_classify_resolve_and_link_moved_and_duplicate_measurements(self):
        originals = {}
        for pk, code in ((100000, merge.OFFICIAL_CODE), (100092, merge.OLD_CODE)):
            originals[pk] = AuditLog.objects.create(
                organization=self.org, user=self.writer, action="entry", object_type="box", object_id=code,
                metadata={"measurement_id": pk}, description="Original synthetic measurement audit",
            )
        old_snapshots = {pk: merge.snapshot(log) for pk, log in originals.items()}
        self.apply()
        updates = list(AuditLog.objects.filter(metadata__operation=merge.OPERATION, metadata__model="measurement"))
        by_measurement = {log.metadata["measurement_id"]: log for log in updates}
        self.client.force_login(self.admin)
        headers = {"HTTP_X_ORGANIZATION_ID": "1"}
        for route, parameters in (
            ("api_account_audit_log", {"family": "measurements"}), ("api_profile_actions", {}),
        ):
            entries = {}
            offset = 0
            while True:
                response = self.client.get(reverse(route), {**parameters, "limit": 100, "offset": offset}, **headers)
                self.assertEqual(response.status_code, 200)
                payload = response.json()
                entries.update({row["id"]: row for row in payload["results"]})
                if not payload["has_more"]:
                    break
                self.assertGreater(payload["next_offset"], offset)
                offset = payload["next_offset"]
            for log in updates:
                entry = entries[log.pk]
                self.assertEqual(entry["family"], "measurements")
                self.assertEqual(entry["context"]["measurement"]["id"], log.metadata["measurement_id"])
                self.assertEqual(entry["box_reference"]["id"], 2135)
                self.assertEqual(entry["business_details"]["type"], "measurement")
        for measurement_id, original in originals.items():
            root = by_measurement[measurement_id]
            response = self.client.get(reverse("api_account_audit_log_linked", args=[root.pk]), **headers)
            self.assertEqual(response.status_code, 200)
            chain = response.json()["results"]
            self.assertEqual({row["id"] for row in chain}, {original.pk, root.pk})
            self.assertTrue(all(row["context"]["measurement"]["id"] == measurement_id for row in chain))
            self.assertTrue(all(row["related_action_count"] == 1 for row in chain))
            original.refresh_from_db()
            self.assertEqual(merge.snapshot(original), old_snapshots[measurement_id])
        OrganizationMembership.objects.create(organization=self.other_org, user=self.admin, role="admin")
        response = self.client.get(reverse("api_account_audit_log_linked", args=[updates[0].pk]),
                                   HTTP_X_ORGANIZATION_ID="2")
        self.assertEqual(response.status_code, 404)

    def test_historical_import_locks_scoped_boxes_by_pk_before_organization(self):
        earlier = Box.objects.create(
            pk=2000, organization=self.org, strain=self.cho_strain, global_code="PCA-CHO-1.009",
            local_code="PCA-CHO-1.09", box_number="009", thermal_zone=self.zone2,
        )
        manifest = self.historical_manifest(boxes=(self.donor, earlier, self.canonical))
        reviewed = importer.build_plan(manifest, self.org)
        locks = []
        def record(execute, sql, params, many, context):
            if "FOR UPDATE" in sql or "FOR NO KEY UPDATE" in sql:
                locks.append(sql)
            return execute(sql, params, many, context)
        with connection.execute_wrapper(record):
            applied, receipt = importer.apply_import(manifest, self.org, actor=self.admin,
                                                      expected_plan_hash=reviewed.plan_hash)
        self.assertEqual(applied.plan_hash, reviewed.plan_hash)
        self.assertIsNotNone(receipt)
        box_index = next(index for index, sql in enumerate(locks) if '"cultures_box"' in sql)
        org_index = next(index for index, sql in enumerate(locks) if '"organizations_organization"' in sql)
        member_index = next(index for index, sql in enumerate(locks) if '"accounts_organizationmembership"' in sql)
        self.assertLess(box_index, org_index)
        self.assertLess(org_index, member_index)
        self.assertIn('ORDER BY "cultures_box"."id" ASC', locks[box_index])
        self.assertIn("FOR NO KEY UPDATE", locks[org_index])
        self.assertEqual(BiologicalMeasurement.objects.get(box_id=2135, week_start=date.fromisocalendar(2026, 43, 1)).polyp_count, 0)

    def test_historical_import_can_materialize_missing_codes_and_keeps_foreign_collisions_blocked(self):
        spec = box_spec("PCA-CHO-1.010", strain_code=self.cho_strain.code,
                        species=self.species.scientific_name, number="010")
        manifest = make_manifest([spec], [item(spec["code"], 43, 0, 0)])
        reviewed = importer.build_plan(manifest, self.org)
        _, receipt = importer.apply_import(manifest, self.org, actor=self.admin, expected_plan_hash=reviewed.plan_hash)
        self.assertIsNotNone(receipt)
        created = Box.objects.get(global_code=spec["code"])
        self.assertEqual((created.organization_id, created.status), (1, "pending_review"))
        foreign_spec = box_spec(self.other_box.global_code, strain_code=self.cho_strain.code,
                                species=self.species.scientific_name, number=self.other_box.box_number)
        foreign = make_manifest([foreign_spec], [item(foreign_spec["code"], 43, 0, 0)])
        reviewed = importer.build_plan(foreign, self.org)
        before = self.database_snapshot()
        with self.assertRaises(importer.HistoricalImportBlocked):
            importer.apply_import(foreign, self.org, actor=self.admin, expected_plan_hash=reviewed.plan_hash)
        self.assertEqual(before, self.database_snapshot())

    def test_command_apply_returns_own_receipt_and_requires_all_references(self):
        plan = self.plan()
        options = {"organization_id": 1, "actor_id": self.admin.pk, "apply": True,
                   "expected_plan_hash": merge.fingerprint(plan),
                   "backup_ref": "synthetic-command-backup", "review_ref": "synthetic-command-review"}
        output = StringIO()
        call_command("unify_pca_identity", stdout=output, **options)
        result = json.loads(output.getvalue())
        receipt = AuditLog.objects.get(pk=result["receipt_id"])
        self.assertEqual(receipt.object_type, merge.RECEIPT_TYPE)
        self.assertEqual(receipt.metadata["reviewed_plan"], plan)
        self.assertEqual(result["receipt"], merge.snapshot(receipt))
        before = self.database_snapshot()
        with self.assertRaises(CommandError):
            call_command("unify_pca_identity", **{**options, "review_ref": None})
        self.assertEqual(before, self.database_snapshot())

    def test_empty_official_local_code_retains_physical_local_code(self):
        Box.objects.filter(pk=2141).update(local_code="")
        self.apply()
        self.assertEqual(Box.objects.get(pk=2135).local_code, "Old CHO label")

    def test_original_hash_replays_receipt_even_after_later_canonical_edit(self):
        plan = self.plan()
        receipt = self.apply(plan)
        Box.objects.filter(pk=2135).update(notes="Later legitimate edit")
        before = self.database_snapshot()
        self.assertEqual(self.apply(plan).pk, receipt.pk)
        self.assertEqual(before, self.database_snapshot())
        with self.assertRaisesMessage(ValueError, "different or ambiguous"):
            self.apply(plan, expected_plan_hash="d" * 64)
        OrganizationMembership.objects.filter(pk=self.member.pk).update(role="viewer")
        with self.assertRaises(PermissionDenied):
            self.apply(plan)

    def test_receipt_tampering_is_rejected_not_repaired(self):
        plan = self.plan()
        receipt = self.apply(plan)
        metadata = dict(receipt.metadata, review_ref="tampered")
        AuditLog.objects.filter(pk=receipt.pk).update(metadata=metadata)
        with self.assertRaisesMessage(ValueError, "receipt integrity"):
            self.apply(plan)

    def test_stale_snapshots_cover_measurements_boxes_strains_zones_locations_audits_and_receipts(self):
        targets = [(BiologicalMeasurement, 100000, "notes", "concurrent"),
                   (Box, 2135, "notes", "concurrent"), (Strain, 54, "notes", "concurrent"),
                   (ThermalZone, 1, "notes", "concurrent"), (BoxLocation, 5945, "notes", "concurrent"),
                   (AuditLog, self.old_audit.pk, "description", "concurrent")]
        for model, pk, field, value in targets:
            with self.subTest(model=model):
                plan = self.plan()
                old = getattr(model.objects.get(pk=pk), field)
                model.objects.filter(pk=pk).update(**{field: value})
                before = self.database_snapshot()
                with self.assertRaisesMessage(ValueError, "Stale plan"):
                    self.apply(plan)
                self.assertEqual(before, self.database_snapshot())
                model.objects.filter(pk=pk).update(**{field: old})
        plan = self.plan()
        MeasurementImportReceipt.objects.create(
            organization=self.org, actor_id=self.admin.pk, source_sha256="e" * 64, source_bytes=b"new",
            preparation_fingerprint="f" * 64, plan_fingerprint="a" * 64,
            preparation={}, accepted_plan={"rows": [{"box": {"id": 2141}}]}, result={},
        )
        with self.assertRaisesMessage(ValueError, "Stale plan"):
            self.apply(plan)

    def test_scoped_evidence_includes_nested_plans_but_ignores_unrelated_institution_activity(self):
        related = [self.old_audit.pk]
        for object_type, object_id, metadata, original_values in (
            ("box", "2141", {}, {}),
            ("biological_measurement", "100000", {}, {}),
            ("historical_correction", "separate-operation", {"reviewed_plan": {"rows": [{"box": {"id": 2135}}]}}, {}),
            ("import", "nested-measurement", {"result": {"measurement_ids": ["100001"]}}, {}),
            ("correction", "nested-code", {}, {"before": {"global_code": "PCA-JKA-1.005"}}),
        ):
            related.append(AuditLog.objects.create(
                organization=self.org, object_type=object_type, object_id=object_id,
                metadata=metadata, original_values=original_values, action="update",
            ).pk)
        receipt = MeasurementImportReceipt.objects.create(
            organization=self.org, actor_id=self.admin.pk, source_sha256="1" * 64, source_bytes=b"nested PCA",
            preparation_fingerprint="f" * 64, plan_fingerprint="a" * 64,
            preparation={}, accepted_plan={"reviewed": {"rows": [{"box": {"id": "2141"}}]}}, result={},
        )
        plan = self.plan()
        self.assertEqual({row["id"] for row in plan["baseline"]["audit_logs"]}, set(related))
        self.assertIn(str(receipt.pk), {row["id"] for row in plan["baseline"]["import_receipts"]})
        unrelated = AuditLog.objects.create(
            organization=self.org, object_type="account", object_id="unrelated-account", action="update",
            metadata={"user_id": 2135, "unrelated": "not PCA"},
        )
        AuditLog.objects.create(
            organization=self.other_org, object_type="box", object_id=merge.OLD_CODE, action="update",
        )
        MeasurementImportReceipt.objects.create(
            organization=self.org, actor_id=self.admin.pk, source_sha256="2" * 64, source_bytes=b"unrelated",
            preparation_fingerprint="f" * 64, plan_fingerprint="a" * 64,
            preparation={}, accepted_plan={"rows": [{"box": {"id": self.other_box.pk}}]}, result={},
        )
        AuditLog.objects.filter(pk=unrelated.pk).update(description="Unrelated activity continues")
        self.assertEqual(self.plan(), plan)
        all_audits_before = merge._rows(AuditLog.objects.all())
        all_receipts_before = merge._rows(MeasurementImportReceipt.objects.all())
        self.apply(plan)
        self.assertEqual(merge._rows(AuditLog.objects.filter(pk__in=[row["id"] for row in all_audits_before])),
                         all_audits_before)
        self.assertEqual(merge._rows(MeasurementImportReceipt.objects.all()), all_receipts_before)

    def test_namespace_high_water_and_new_counter_invalidate_reviewed_plan(self):
        plan = self.plan()
        counter = BoxCodeNamespace.objects.get(namespace="PCA-JKA-1")
        self.assertIn(merge.snapshot(counter), plan["baseline"]["namespaces"])
        BoxCodeNamespace.objects.filter(pk=counter.pk).update(high_water=counter.high_water + 1)
        before = self.database_snapshot()
        with self.assertRaisesMessage(ValueError, "Stale plan"):
            self.apply(plan)
        self.assertEqual(self.database_snapshot(), before)
        BoxCodeNamespace.objects.filter(pk=counter.pk).delete()
        plan = self.plan()
        self.assertNotIn("PCA-JKA-1", {row["namespace"] for row in plan["baseline"]["namespaces"]})
        BoxCodeNamespace.objects.create(namespace="PCA-JKA-1", high_water=5)
        with self.assertRaisesMessage(ValueError, "Stale plan"):
            self.apply(plan)

    def test_missing_counter_is_created_only_after_the_original_plan_is_checked(self):
        BoxCodeNamespace.objects.filter(namespace="PCA-JKA-1").delete()
        plan = self.plan()
        BoxCodeNamespace.objects.create(namespace="unrelated-namespace", high_water=400)
        self.assertEqual(self.plan(), plan)
        make_plan = merge._make_plan
        def check_missing_before_fingerprint(organization):
            self.assertFalse(BoxCodeNamespace.objects.filter(namespace="PCA-JKA-1").exists())
            return make_plan(organization)
        with patch.object(merge, "_make_plan", side_effect=check_missing_before_fingerprint):
            receipt = self.apply(plan)
        self.assertEqual(receipt.metadata["reviewed_plan"], plan)
        self.assertEqual(BoxCodeNamespace.objects.get(namespace="PCA-JKA-1").high_water, 5)

    def test_exact_strain_codes_shared_species_78_and_active_current_zone_are_required(self):
        for strain in (self.cho_strain, self.jka_strain):
            Strain.objects.filter(pk=strain.pk).update(code="Unexpected-PCA-code")
            self.assert_blocked()
            Strain.objects.filter(pk=strain.pk).update(code=strain.code)
        wrong_species = Species.objects.create(scientific_name="Different synthetic species")
        Strain.objects.filter(pk=54).update(species=wrong_species)
        self.assert_blocked()
        Strain.objects.filter(pk=140).update(species=wrong_species)
        self.assert_blocked()  # Equal species alone is not the exact reviewed species.
        Strain.objects.filter(pk__in=(54, 140)).update(species=self.species)
        ThermalZone.objects.filter(pk=2).update(is_active=False)
        self.assert_blocked()
        ThermalZone.objects.filter(pk=2).update(is_active=True)
        Box.objects.filter(pk=2135).update(thermal_zone=self.zone1)
        self.assert_blocked()

    def test_location_dates_are_compared_in_paris_and_original_instants_are_preserved(self):
        for period in BoxLocation.objects.all():
            BoxLocation.objects.filter(pk=period.pk).update(
                starts_at=period.starts_at - timedelta(hours=1),
                ends_at=period.ends_at - timedelta(hours=1) if period.ends_at else None,
            )
        plan = self.plan()
        self.assertTrue(plan["ready"], plan["blockers"])
        before = merge._rows(BoxLocation.objects.all())
        self.apply(plan)
        self.assertEqual(merge._rows(BoxLocation.objects.all()), before)

    def test_each_overlap_requires_equal_counts_and_explicit_zero_null_loader_provenance(self):
        for week in range(1, 19):
            cho = BiologicalMeasurement.objects.get(box_id=2135, week_start=date.fromisocalendar(2026, week, 1))
            BiologicalMeasurement.objects.filter(pk=cho.pk).update(polyp_count=cho.polyp_count + 1)
            self.assert_blocked()
            BiologicalMeasurement.objects.filter(pk=cho.pk).update(polyp_count=cho.polyp_count)
        cho = BiologicalMeasurement.objects.get(box_id=2135, week_start=date.fromisocalendar(2026, 18, 1))
        jka = BiologicalMeasurement.objects.get(box_id=2141, week_start=cho.week_start)
        for row, field, value in ((cho, "ephyrae_count", cho.ephyrae_count + 1),
                                  (cho, "strobila_count", None), (jka, "strobila_count", 0)):
            BiologicalMeasurement.objects.filter(pk=row.pk).update(**{field: value})
            self.assert_blocked()
            BiologicalMeasurement.objects.filter(pk=row.pk).update(**{field: getattr(row, field)})
        self.assertTrue(self.plan()["ready"])

    def test_both_preserved_measurements_require_nonnull_but_not_hardcoded_authors(self):
        authors = set(BiologicalMeasurement.objects.filter(pk__in=(104426, 104540)).values_list("user_id", flat=True))
        self.assertEqual(authors, {self.admin.pk, self.writer.pk})
        self.assertTrue(self.plan()["ready"])
        for pk in (104426, 104540):
            row = BiologicalMeasurement.objects.get(pk=pk)
            BiologicalMeasurement.objects.filter(pk=pk).update(user=None)
            self.assert_blocked()
            BiologicalMeasurement.objects.filter(pk=pk).update(user_id=row.user_id)

    def test_all_dynamically_discovered_unapproved_dependencies_block(self):
        original_inventory = merge._dependency_inventory([])
        discovered = {(model._meta.label_lower, field.attname)
                      for model, field in [*merge.box_dependencies(), *merge.measurement_dependencies()]}
        self.assertEqual(discovered, {(entry["model"], entry["field"]) for entry in original_inventory})
        for entry in original_inventory:
            if entry["allowed"]:
                continue
            with self.subTest(model=entry["model"], field=entry["field"]):
                simulated = [dict(row) for row in original_inventory]
                selected = next(row for row in simulated if (row["model"], row["field"]) == (entry["model"], entry["field"]))
                selected.update(count=1, rows=[{"synthetic_unexpected_reference": True}])
                with patch.object(merge, "_dependency_inventory", return_value=simulated):
                    self.assert_blocked()

    def test_real_observation_alert_tag_and_movement_references_block(self):
        factories = [
            lambda: Observation.objects.create(box=self.donor, notes="Unexpected observation"),
            lambda: Alert.objects.create(box=self.canonical, organization=self.org, message="Old alert"),
            lambda: IdentificationTag.objects.create(box=self.canonical, code="synthetic-pca-tag"),
            lambda: BoxMovement.objects.create(box=self.canonical, to_thermal_zone=self.zone2, user=self.admin),
        ]
        for factory in factories:
            with self.subTest(factory=factory):
                row = factory()
                self.assert_blocked()
                row.delete()

    def test_foreign_organization_inactive_user_and_nonadmin_are_forbidden(self):
        with self.assertRaises(ValueError):
            merge.build_plan(2, actor=self.admin)
        for field, value in (("role", "viewer"), ("is_active", False)):
            old = getattr(self.member, field)
            OrganizationMembership.objects.filter(pk=self.member.pk).update(**{field: value})
            with self.assertRaises(PermissionDenied):
                self.plan()
            with self.assertRaises(PermissionDenied):
                self.apply({}, expected_plan_hash="a" * 64)
            OrganizationMembership.objects.filter(pk=self.member.pk).update(**{field: old})
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False, is_superuser=True)
        with self.assertRaises(PermissionDenied):
            self.plan()

    def test_wrong_tenancy_and_exact_preserved_ids_block(self):
        Box.objects.filter(pk=2141).update(organization=self.other_org)
        self.assert_blocked()
        Box.objects.filter(pk=2141).update(organization=self.org)
        BiologicalMeasurement.objects.filter(pk=104426).update(week_start=date.fromisocalendar(2026, 43, 1))
        self.assert_blocked()

    def test_origin_and_unexpected_existing_evidence_require_review(self):
        origin = Origin.objects.create(description="Synthetic unexpected origin")
        Box.objects.filter(pk=2135).update(origin=origin)
        self.assert_blocked()
        Box.objects.filter(pk=2135).update(origin=None)
        BiologicalMeasurement.objects.filter(pk=100000).update(environment_source_box_id=2141)
        self.assert_blocked()

    def test_late_audit_failure_rolls_back_all_changes_and_namespace_registration(self):
        BoxCodeNamespace.objects.filter(namespace="PCA-JKA-1").delete()
        plan = self.plan()
        before = self.database_snapshot()
        create = AuditLog.objects.create
        def fail_receipt(**kwargs):
            if kwargs.get("object_type") == merge.RECEIPT_TYPE:
                raise RuntimeError("Injected receipt audit failure")
            return create(**kwargs)
        with patch.object(AuditLog.objects, "create", side_effect=fail_receipt):
            with self.assertRaisesMessage(RuntimeError, "Injected receipt"):
                self.apply(plan)
        self.assertEqual(before, self.database_snapshot())
        self.assertTrue(self.plan()["ready"])

    def test_apply_requires_review_backup_and_exact_hash(self):
        plan = self.plan()
        for overrides in ({"backup_ref": ""}, {"review_ref": " "}, {"expected_plan_hash": "invalid"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                self.apply(plan, **overrides)


@skipUnless(connection.vendor == "postgresql", "PCA concurrency requires PostgreSQL row locks.")
class PcaIdentityConcurrencyTests(PcaFixtureMixin, TransactionTestCase):
    reset_sequences = True

    def worker(self, callback, *, attempted=None, table=None, finished=None, organization_attempted=None):
        close_old_connections()
        def observe(execute, sql, params, many, context):
            if attempted is not None and "FOR" in sql and table in sql:
                attempted.set()
            if organization_attempted is not None and "FOR" in sql and '"organizations_organization"' in sql:
                organization_attempted.set()
            return execute(sql, params, many, context)
        try:
            with connection.execute_wrapper(observe):
                return callback()
        finally:
            connections.close_all()
            if finished is not None:
                finished.set()

    def test_ordinary_write_waits_for_merge_then_preserves_both_writes(self):
        plan = self.plan()
        entered, release, attempted, finished = (threading.Event() for _ in range(4))
        create = AuditLog.objects.create
        def pause(**kwargs):
            result = create(**kwargs)
            if kwargs.get("object_type") == merge.RECEIPT_TYPE:
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Merge audit was not released")
            return result
        def ordinary():
            return measurement_services.create_biological_measurement(
                box_id=2135, organization_ids=[1], user=self.writer,
                data={"measured_on": date.fromisocalendar(2026, 43, 3), "polyp_count": 0, "ephyrae_count": 0},
            ).pk
        with patch.object(AuditLog.objects, "create", side_effect=pause), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, lambda: self.apply(plan).pk)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.worker, ordinary, attempted=attempted,
                                     table="cultures_box", finished=finished)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(finished.wait(0.2))
            finally:
                release.set()
            self.assertIsInstance(first.result(15), int)
            pk = second.result(15)
        self.assertEqual(BiologicalMeasurement.objects.get(pk=pk).polyp_count, 0)
        self.assertEqual(BiologicalMeasurement.objects.count(), 113)
        self.assertEqual(Box.objects.get(pk=2135).polyp_state_revision, 1012)

    def test_ordinary_correction_commits_first_and_merge_rejects_stale_plan(self):
        plan = self.plan()
        entered, release, attempted = (threading.Event() for _ in range(3))
        def correct_first():
            with transaction.atomic():
                measurement_services.correct_biological_measurement(
                    box_id=2135, measurement_id=104426, organization_ids=[1], user=self.admin,
                    data={"polyp_count": 0, "notes": "Concurrent ordinary correction"},
                )
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Ordinary correction was not released")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, correct_first)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.worker, lambda: self.apply(plan), attempted=attempted, table="cultures_box")
                self.assertTrue(attempted.wait(10))
            finally:
                release.set()
            first.result(15)
            with self.assertRaisesMessage(ValueError, "Stale plan"):
                second.result(15)
        self.assertEqual(Box.objects.get(pk=2135).global_code, merge.OLD_CODE)
        self.assertEqual(BiologicalMeasurement.objects.filter(box_id=2141).count(), 92)

    def test_rights_change_holds_organization_then_merge_rechecks_under_lock(self):
        plan = self.plan()
        entered, release, attempted = (threading.Event() for _ in range(3))
        def revoke():
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=1)
                membership = OrganizationMembership.objects.select_for_update().get(pk=self.member.pk)
                membership.role = "viewer"
                membership.save(update_fields=["role"])
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Rights change was not released")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, revoke)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.worker, lambda: self.apply(plan), attempted=attempted,
                                     table="organizations_organization")
                self.assertTrue(attempted.wait(10))
            finally:
                release.set()
            first.result(15)
            with self.assertRaises(PermissionDenied):
                second.result(15)
        self.assertEqual(Box.objects.get(pk=2141).merged_into_id, None)
        self.assertEqual(AuditLog.objects.filter(object_type=merge.RECEIPT_TYPE).count(), 0)

    def test_historical_import_waits_for_merge_boxes_without_holding_organization(self):
        plan = self.plan()
        manifest = self.historical_manifest()
        historical_hash = importer.build_plan(manifest, self.org).plan_hash
        entered, release, attempted, organization_attempted = (threading.Event() for _ in range(4))
        authorize = merge._authorize
        def pause_before_organization(*args, **kwargs):
            if kwargs.get("lock"):
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Merge Box locks were not released")
            return authorize(*args, **kwargs)
        def historical():
            return importer.apply_import(manifest, self.org, actor=self.admin, expected_plan_hash=historical_hash)
        with patch.object(merge, "_authorize", side_effect=pause_before_organization), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, lambda: self.apply(plan).pk)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.worker, historical, attempted=attempted, table='"cultures_box"',
                                     organization_attempted=organization_attempted)
                self.assertTrue(attempted.wait(10))
                self.assertFalse(organization_attempted.is_set(), "Importer took Organization before waiting for Box")
            finally:
                release.set()
            self.assertIsInstance(first.result(15), int)
            with self.assertRaises(importer.HistoricalImportError):
                second.result(15)
        self.assertFalse(Box.objects.filter(global_code=merge.OLD_CODE).exists())
        self.assertEqual(BiologicalMeasurement.objects.canonical().count(), 94)
        self.assertEqual(AuditLog.objects.filter(object_type=importer.IMPORT_OBJECT_TYPE).count(), 0)

    def test_historical_import_commits_first_then_merge_rejects_stale_plan(self):
        plan = self.plan()
        manifest = self.historical_manifest()
        historical_hash = importer.build_plan(manifest, self.org).plan_hash
        entered, release, attempted = (threading.Event() for _ in range(3))
        audit = importer._audit
        def pause_receipt(*args, **kwargs):
            result = audit(*args, **kwargs)
            if result.object_type == importer.IMPORT_OBJECT_TYPE:
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Historical import receipt was not released")
            return result
        def historical():
            _, receipt = importer.apply_import(manifest, self.org, actor=self.admin, expected_plan_hash=historical_hash)
            return receipt.pk
        with patch.object(importer, "_audit", side_effect=pause_receipt), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, historical)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.worker, lambda: self.apply(plan), attempted=attempted, table='"cultures_box"')
                self.assertTrue(attempted.wait(10))
            finally:
                release.set()
            self.assertIsInstance(first.result(15), int)
            with self.assertRaisesMessage(ValueError, "Stale plan"):
                second.result(15)
        self.assertEqual(BiologicalMeasurement.objects.count(), 113)
        self.assertEqual(Box.objects.get(pk=2135).global_code, merge.OLD_CODE)
        self.assertEqual(BiologicalMeasurement.objects.filter(box_id=2141).count(), 92)
        self.assertEqual(AuditLog.objects.filter(object_type=merge.RECEIPT_TYPE).count(), 0)

    def test_historical_import_waits_for_ordinary_write_then_preserves_both_writes(self):
        manifest = self.historical_manifest()
        historical_hash = importer.build_plan(manifest, self.org).plan_hash
        entered, release, attempted = (threading.Event() for _ in range(3))
        def ordinary():
            with transaction.atomic():
                measurement_services.correct_biological_measurement(
                    box_id=2135, measurement_id=104426, organization_ids=[1], user=self.admin,
                    data={"polyp_count": 0, "notes": "Ordinary correction before historical import"},
                )
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Ordinary correction was not released")
        def historical():
            _, receipt = importer.apply_import(manifest, self.org, actor=self.admin, expected_plan_hash=historical_hash)
            return receipt.pk
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, ordinary)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.worker, historical, attempted=attempted, table='"cultures_box"')
                self.assertTrue(attempted.wait(10))
            finally:
                release.set()
            first.result(15)
            self.assertIsInstance(second.result(15), int)
        self.assertEqual(BiologicalMeasurement.objects.get(pk=104426).notes,
                         "Ordinary correction before historical import")
        self.assertEqual(BiologicalMeasurement.objects.get(
            box_id=2135, week_start=date.fromisocalendar(2026, 43, 1)).polyp_count, 0)

    def test_historical_import_rechecks_rights_after_waiting_for_organization(self):
        manifest = self.historical_manifest()
        historical_hash = importer.build_plan(manifest, self.org).plan_hash
        entered, release, attempted = (threading.Event() for _ in range(3))
        def revoke():
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=1)
                OrganizationMembership.objects.select_for_update().get(pk=self.member.pk)
                OrganizationMembership.objects.filter(pk=self.member.pk).update(role="viewer")
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Membership revocation was not released")
        def historical():
            return importer.apply_import(manifest, self.org, actor=self.admin, expected_plan_hash=historical_hash)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, revoke)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.worker, historical, attempted=attempted, table='"organizations_organization"')
                self.assertTrue(attempted.wait(10))
            finally:
                release.set()
            first.result(15)
            with self.assertRaises(PermissionDenied):
                second.result(15)
        self.assertEqual(BiologicalMeasurement.objects.count(), 112)
        self.assertEqual(AuditLog.objects.filter(object_type=importer.IMPORT_OBJECT_TYPE).count(), 0)

    def test_historical_import_aborts_if_a_missing_code_appears_while_waiting(self):
        new_code = "PCA-CHO-1.010"
        manifest = self.historical_manifest()
        manifest["boxes"].append(box_spec(new_code, strain_code=self.cho_strain.code,
                                         species=self.species.scientific_name, number="010"))
        historical_hash = importer.build_plan(manifest, self.org).plan_hash
        entered, release, attempted = (threading.Event() for _ in range(3))
        def create_before_unlock():
            with transaction.atomic():
                Organization.objects.select_for_update().get(pk=1)
                created = Box.objects.create(organization=self.org, strain=self.cho_strain,
                                             global_code=new_code, local_code=new_code, box_number="010")
                entered.set()
                if not release.wait(10):
                    raise TimeoutError("Concurrent Box creation was not released")
                return created.pk
        def historical():
            return importer.apply_import(manifest, self.org, actor=self.admin, expected_plan_hash=historical_hash)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.worker, create_before_unlock)
            try:
                self.assertTrue(entered.wait(10))
                second = pool.submit(self.worker, historical, attempted=attempted, table='"organizations_organization"')
                self.assertTrue(attempted.wait(10))
            finally:
                release.set()
            pk = first.result(15)
            with self.assertRaisesMessage(importer.HistoricalImportError, "Box identities changed"):
                second.result(15)
        self.assertEqual(Box.objects.get(global_code=new_code).pk, pk)
        self.assertEqual(BiologicalMeasurement.objects.count(), 112)
        self.assertEqual(AuditLog.objects.filter(object_type=importer.IMPORT_OBJECT_TYPE).count(), 0)
