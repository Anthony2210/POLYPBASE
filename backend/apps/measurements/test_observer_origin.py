"""Observer attribution is distinct from the latest writer and audit actor."""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.cultures.serializers import BiologicalMeasurementSerializer
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import services
from .models import BiologicalMeasurement
from .serializers import BiologicalMeasurementCreateSerializer


PROVENANCE_FIELDS = (
    "observer_status", "original_observer_id", "origin", "import_format", "imported_by_id",
)


class ObserverOriginTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Observer laboratory")
        self.foreign_org = Organization.objects.create(name="Foreign observer laboratory")
        users = get_user_model()
        self.observer = users.objects.create_user(
            username="original-observer", first_name="Alice", last_name="Observer",
            email="alice@example.org",
        )
        self.corrector = users.objects.create_user(
            username="correcting-actor", first_name="Bob", last_name="Corrector",
            email="bob@example.org",
        )
        self.foreign_user = users.objects.create_user(username="foreign-observer", email="foreign@example.org")
        for user in (self.observer, self.corrector):
            OrganizationMembership.objects.create(user=user, organization=self.org, role="admin")
        OrganizationMembership.objects.create(user=self.foreign_user, organization=self.foreign_org, role="admin")
        species = Species.objects.create(scientific_name="Aurelia observer", genus_species_code="AOB")
        strain = Strain.objects.create(organization=self.org, species=species, code="AOB-QA-1")
        foreign_strain = Strain.objects.create(organization=self.foreign_org, species=species, code="AOB-QA-2")
        self.box = Box.objects.create(
            organization=self.org, strain=strain, global_code="AOB-QA-1.001", box_number="001",
        )
        self.foreign_box = Box.objects.create(
            organization=self.foreign_org, strain=foreign_strain, global_code="AOB-QA-2.001", box_number="001",
        )
        self.day = date(2026, 9, 14)
        self.list_url = reverse("api_box_measurements", args=[self.box.pk])
        self.client.force_login(self.observer)

    def post(self, data=None, *, url=None):
        return self.client.post(
            url or self.list_url,
            data={"measured_on": self.day.isoformat()} if data is None else data,
            content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )

    def correct(self, row, data, *, box=None):
        return self.client.patch(
            reverse("api_box_measurement_detail", args=[(box or self.box).pk, row.pk]),
            data=data, content_type="application/json", HTTP_X_ORGANIZATION_ID=str(self.org.pk),
        )

    def revision(self):
        self.box.refresh_from_db()
        return self.box.polyp_state_revision

    def provenance(self, row):
        return tuple(getattr(row, field) for field in PROVENANCE_FIELDS)

    def snapshot(self):
        return (
            list(BiologicalMeasurement.objects.order_by("pk").values()),
            list(AuditLog.objects.order_by("pk").values()),
            list(Box.objects.order_by("pk").values()),
        )

    def assert_response_provenance(self, data, row):
        for field in ("observer_status", "origin", "import_format"):
            self.assertEqual(data[field], getattr(row, field))
        for prefix in ("original_observer", "imported_by"):
            account = getattr(row, prefix)
            identity = None if account is None else {
                "first_name": account.first_name, "last_name": account.last_name, "email": account.email,
            }
            self.assertEqual(data[f"{prefix}_identity"], identity)
            display = None if account is None else " ".join(
                part for part in (account.first_name, account.last_name.upper()) if part
            ) or account.email or None
            self.assertEqual(data[f"{prefix}_display"], display)
            self.assertNotIn(prefix, data)
            self.assertNotIn(f"{prefix}_id", data)

    def test_http_direct_entry_and_other_actor_correction_preserve_provenance_and_audits(self):
        revision = self.revision()
        response = self.post({
            "measured_on": self.day.isoformat(), "polyp_count": 0, "ephyrae_count": 0,
            "strobila_count": 0, "salinity_psu": "0.00", "notes": "Scientific zero",
        })
        self.assertEqual(response.status_code, 201, response.content)
        row = BiologicalMeasurement.objects.get()
        self.assertEqual(self.provenance(row), ("known", self.observer.pk, "direct_entry", "", None))
        self.assertEqual(row.user_id, self.observer.pk)
        self.assertEqual((row.polyp_count, row.ephyrae_count, row.strobila_count), (0, 0, 0))
        self.assertEqual(row.salinity_psu, Decimal("0.00"))
        self.assertEqual(row.week_start, self.day)
        self.assertEqual(self.revision(), revision + 1)
        self.assertEqual(row.polyp_state_sequence, revision + 1)
        self.assert_response_provenance(response.json(), row)
        self.assertEqual(response.json()["polyp_count"], 0)
        values = {
            "date": self.day.isoformat(), "polypes": 0, "ephyrules": 0, "strobiles": 0,
            "salinite_psu": "0.00", "statut_culture": "not_specified", "a_verifier": False,
            "note": "Scientific zero",
        }
        entry = AuditLog.objects.get(action="entry")
        self.assertEqual(entry.user_id, self.observer.pk)
        self.assertEqual(entry.organization_id, self.org.pk)
        self.assertEqual((entry.object_type, entry.object_id), ("box", self.box.global_code))
        self.assertEqual(entry.description, f"Biological measurement for {self.day}")
        self.assertEqual(entry.metadata, {"measurement_id": row.pk, "valeurs": values})
        entry_snapshot = AuditLog.objects.filter(pk=entry.pk).values().get()
        identity = (row.pk, row.created_at, row.polyp_state_sequence)
        provenance = self.provenance(row)
        next_day = self.day + timedelta(days=7)
        self.client.force_login(self.corrector)
        response = self.correct(row, {"measured_on": next_day.isoformat(), "notes": "Corrected date"})
        self.assertEqual(response.status_code, 200, response.content)
        row.refresh_from_db()
        self.assertEqual((row.pk, row.created_at, row.polyp_state_sequence), identity)
        self.assertEqual(self.provenance(row), provenance)
        self.assertEqual(row.user_id, self.corrector.pk)
        self.assertEqual((row.measured_on, row.week_start), (next_day, next_day))
        self.assertEqual((row.polyp_count, row.ephyrae_count, row.strobila_count), (0, 0, 0))
        self.assertEqual(self.revision(), revision + 2)
        self.assert_response_provenance(response.json(), row)
        after = {**values, "date": next_day.isoformat(), "note": "Corrected date"}
        update = AuditLog.objects.get(action="update")
        self.assertEqual(update.user_id, self.corrector.pk)
        self.assertEqual(update.metadata, {
            "measurement_id": row.pk, "before": values, "after": after, "valeurs": after,
            "modifications": {
                "date": {"avant": self.day.isoformat(), "apres": next_day.isoformat()},
                "note": {"avant": "Scientific zero", "apres": "Corrected date"},
            },
        })
        self.assertEqual(AuditLog.objects.filter(pk=entry.pk).values().get(), entry_snapshot)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_write_serializer_excludes_and_silently_ignores_all_provenance_fields(self):
        spoof = self.spoofed_provenance()
        serializer = BiologicalMeasurementCreateSerializer(data={"measured_on": self.day.isoformat(), **spoof})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data, {"measured_on": self.day})
        self.assertTrue(set(spoof).isdisjoint(serializer.fields))
        row = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day)
        serializer = BiologicalMeasurementCreateSerializer(row, data=spoof, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data, {})

    def spoofed_provenance(self):
        return {
            "observer_status": "not_recorded", "original_observer": self.foreign_user.pk,
            "original_observer_id": self.foreign_user.pk, "origin": "file_import", "import_format": "csv",
            "imported_by": self.foreign_user.pk, "imported_by_id": self.foreign_user.pk,
            "original_observer_identity": {"email": "spoof@example.org"},
            "original_observer_display": "Spoofed observer",
            "imported_by_identity": {"email": "spoof@example.org"}, "imported_by_display": "Spoofed importer",
        }

    def test_http_post_and_patch_ignore_spoofed_provenance(self):
        spoof = self.spoofed_provenance()
        response = self.post({"measured_on": self.day.isoformat(), **spoof})
        self.assertEqual(response.status_code, 201, response.content)
        row = BiologicalMeasurement.objects.get()
        expected = ("known", self.observer.pk, "direct_entry", "", None)
        self.assertEqual(self.provenance(row), expected)
        self.assert_response_provenance(response.json(), row)
        self.client.force_login(self.corrector)
        response = self.correct(row, {"polyp_count": 0, **spoof})
        self.assertEqual(response.status_code, 200, response.content)
        row.refresh_from_db()
        self.assertEqual(self.provenance(row), expected)
        self.assertEqual(row.user_id, self.corrector.pk)
        self.assert_response_provenance(response.json(), row)
        self.assertEqual(AuditLog.objects.get(action="update").metadata["modifications"], {})

    def test_new_foreign_references_rejected_by_clean_and_save_without_side_effects(self):
        for field in ("original_observer", "imported_by"):
            for method in ("clean", "save"):
                with self.subTest(field=field, method=method):
                    row = BiologicalMeasurement(
                        box=self.box, measured_on=self.day, observer_status="known", origin="file_import",
                        import_format="csv", **{field: self.foreign_user},
                    )
                    before = self.snapshot()
                    with self.assertRaises(ValidationError) as error:
                        getattr(row, method)()
                    self.assertIn(field, error.exception.message_dict)
                    self.assertEqual(self.snapshot(), before)

    def test_changed_reference_and_box_revalidate_membership(self):
        row = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.day, observer_status="known", original_observer=self.observer,
            origin="file_import", import_format="csv", imported_by=self.corrector,
        )
        before = self.snapshot()
        for field in ("original_observer", "imported_by"):
            with self.subTest(field=field):
                row.refresh_from_db()
                setattr(row, field, self.foreign_user)
                with self.assertRaises(ValidationError) as error:
                    row.save()
                self.assertIn(field, error.exception.message_dict)
                self.assertEqual(self.snapshot(), before)
        row.refresh_from_db()
        row.box = self.foreign_box
        with self.assertRaises(ValidationError):
            row.save()
        self.assertEqual(self.snapshot(), before)

    def test_unchanged_references_remain_correctable_after_membership_removal(self):
        row = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.day, observer_status="known", original_observer=self.observer,
            origin="file_import", import_format="csv", imported_by=self.observer,
        )
        provenance = self.provenance(row)
        OrganizationMembership.objects.filter(user=self.observer, organization=self.org).delete()
        self.client.force_login(self.corrector)
        response = self.correct(row, {"polyp_count": 0, "notes": "Historical correction"})
        self.assertEqual(response.status_code, 200, response.content)
        row.refresh_from_db()
        self.assertEqual(self.provenance(row), provenance)
        self.assertEqual(row.user_id, self.corrector.pk)
        self.assert_response_provenance(response.json(), row)

    def test_foreign_endpoint_scope_denies_reads_and_writes_without_mutation(self):
        foreign_row = BiologicalMeasurement.objects.create(box=self.foreign_box, measured_on=self.day)
        before = self.snapshot()
        url = reverse("api_box_measurements", args=[self.foreign_box.pk])
        response = self.post(url=url)
        self.assertEqual(response.status_code, 404, response.content)
        response = self.correct(foreign_row, {"polyp_count": 0}, box=self.foreign_box)
        self.assertEqual(response.status_code, 404, response.content)
        response = self.client.get(url, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 404, response.content)
        response = self.correct(foreign_row, {"polyp_count": 0})
        self.assertEqual(response.status_code, 404, response.content)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_and_explicit_unknown_are_distinct_in_http_reads_and_corrections(self):
        legacy = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day, user=self.observer)
        unknown = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.day + timedelta(days=7), user=self.observer,
            observer_status="not_recorded", origin="file_import", import_format="csv", imported_by=self.corrector,
        )
        self.assertEqual(self.provenance(legacy), ("legacy_unestablished", None, "legacy_unclassified", "", None))
        self.assertEqual(self.provenance(unknown), ("not_recorded", None, "file_import", "csv", self.corrector.pk))
        response = self.client.get(self.list_url, HTTP_X_ORGANIZATION_ID=str(self.org.pk))
        self.assertEqual(response.status_code, 200, response.content)
        payload = response.json()
        rows = payload["results"] if isinstance(payload, dict) else payload
        by_id = {row["id"]: row for row in rows}
        for row in (legacy, unknown):
            self.assert_response_provenance(by_id[row.pk], row)
            self.assertIsNone(by_id[row.pk]["original_observer_identity"])
            provenance = self.provenance(row)
            self.client.force_login(self.corrector)
            response = self.correct(row, {"notes": "Corrected legacy data"})
            self.assertEqual(response.status_code, 200, response.content)
            row.refresh_from_db()
            self.assertEqual(self.provenance(row), provenance)

    def test_identity_is_live_not_a_name_snapshot(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        row = BiologicalMeasurement.objects.get()
        self.observer.first_name = "Renamed"
        self.observer.last_name = "Scientist"
        self.observer.email = "renamed@example.org"
        self.observer.save(update_fields=["first_name", "last_name", "email"])
        row.refresh_from_db()
        data = BiologicalMeasurementSerializer(row).data
        self.assert_response_provenance(data, row)
        self.assertEqual(data["original_observer_display"], "Renamed SCIENTIST")
        self.assertEqual(data["original_observer_identity"]["email"], "renamed@example.org")
        self.assertFalse(any("snapshot" in field.name for field in row._meta.fields))

    def test_deleted_original_account_stays_known_with_null_identity_and_no_snapshot(self):
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        row = BiologicalMeasurement.objects.get()
        self.client.force_login(self.corrector)
        response = self.correct(row, {"notes": "Corrected before deletion"})
        self.assertEqual(response.status_code, 200, response.content)
        before_revision = self.revision()
        sequence = row.polyp_state_sequence
        self.observer.delete()
        row.refresh_from_db()
        self.assertEqual(self.provenance(row), ("known", None, "direct_entry", "", None))
        self.assertEqual(row.user_id, self.corrector.pk)
        self.assertEqual(row.polyp_state_sequence, sequence)
        self.assertEqual(self.revision(), before_revision)
        data = BiologicalMeasurementSerializer(row).data
        self.assert_response_provenance(data, row)
        self.assertIsNone(data["original_observer_display"])
        for text in ("Alice", "Observer", "alice@example.org"):
            self.assertNotIn(text, str(data))
            self.assertNotIn(text, str(BiologicalMeasurement.objects.filter(pk=row.pk).values().get()))
        response = self.correct(row, {"polyp_count": 0})
        self.assertEqual(response.status_code, 200, response.content)
        row.refresh_from_db()
        self.assertEqual(row.observer_status, "known")
        self.assertIsNone(row.original_observer_id)

    def test_deleted_importer_and_observer_are_set_null_without_reclassifying_import(self):
        row = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=self.day, observer_status="known", original_observer=self.observer,
            origin="file_import", import_format="csv", imported_by=self.observer,
        )
        self.observer.delete()
        row.refresh_from_db()
        self.assertEqual(self.provenance(row), ("known", None, "file_import", "csv", None))
        self.assert_response_provenance(BiologicalMeasurementSerializer(row).data, row)

    def test_database_constraints_reject_invalid_provenance_even_without_model_save(self):
        row = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day)
        invalid = [
            {"observer_status": "invalid"},
            {"original_observer_id": self.observer.pk},
            {"observer_status": "not_recorded", "original_observer_id": self.observer.pk},
            {"origin": "invalid"},
            {"import_format": "csv"},
            {"imported_by_id": self.observer.pk},
            {"origin": "file_import"},
            {"origin": "file_import", "import_format": "xlsx"},
            {"origin": "direct_entry"},
            {"origin": "direct_entry", "observer_status": "not_recorded"},
            {"origin": "direct_entry", "observer_status": "known", "import_format": "csv"},
            {"origin": "direct_entry", "observer_status": "known", "imported_by_id": self.observer.pk},
        ]
        before = self.snapshot()
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    BiologicalMeasurement.objects.filter(pk=row.pk).update(**values)
                self.assertEqual(self.snapshot(), before)

    def test_database_allows_all_observer_states_and_known_null_after_deletion(self):
        row = BiologicalMeasurement.objects.create(box=self.box, measured_on=self.day)
        for status in ("legacy_unestablished", "not_recorded", "known"):
            for origin, format_ in (("legacy_unclassified", ""), ("file_import", "csv"), ("direct_entry", "")):
                if origin == "direct_entry" and status != "known":
                    continue
                with self.subTest(status=status, origin=origin):
                    BiologicalMeasurement.objects.filter(pk=row.pk).update(
                        observer_status=status, original_observer=None, origin=origin,
                        import_format=format_, imported_by=None,
                    )
                    row.refresh_from_db()
                    self.assertEqual(self.provenance(row), (status, None, origin, format_, None))

    def test_http_audit_failure_rolls_back_creation_and_correction_including_provenance(self):
        original = services._record_measurement_audit

        def fail_after_insert(**kwargs):
            original(**kwargs)
            raise RuntimeError("Failure after observer audit insertion")

        before = self.snapshot()
        with patch.object(services, "_record_measurement_audit", side_effect=fail_after_insert):
            with self.assertRaisesMessage(RuntimeError, "Failure after observer audit insertion"):
                self.post()
        self.assertEqual(self.snapshot(), before)
        response = self.post()
        self.assertEqual(response.status_code, 201, response.content)
        row = BiologicalMeasurement.objects.get()
        before = self.snapshot()
        self.client.force_login(self.corrector)
        with patch.object(services, "_record_measurement_audit", side_effect=fail_after_insert):
            with self.assertRaisesMessage(RuntimeError, "Failure after observer audit insertion"):
                self.correct(row, {
                    "measured_on": (self.day + timedelta(days=7)).isoformat(), "polyp_count": 0,
                    "notes": "Must roll back", **self.spoofed_provenance(),
                })
        self.assertEqual(self.snapshot(), before)


class ObserverOriginMigrationTests(TransactionTestCase):
    def test_upgrade_0007_to_0008_preserves_all_original_fields_and_related_history(self):
        executor = MigrationExecutor(connection)
        latest = executor.loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(latest))
        before = [node for node in latest if node[0] != "measurements"] + [
            ("measurements", "0007_nullable_strobila_count"),
        ]
        executor.migrate(before)
        old_apps = executor.loader.project_state(before).apps
        org = old_apps.get_model("organizations", "Organization").objects.create(name="Observer migration laboratory")
        User = old_apps.get_model(*settings.AUTH_USER_MODEL.split("."))
        writer = User.objects.create(username="historical-latest-writer", email="historical@example.org")
        species = old_apps.get_model("taxonomy", "Species").objects.create(scientific_name="Aurelia observer migration")
        strain = old_apps.get_model("taxonomy", "Strain").objects.create(
            organization=org, species=species, code="ORIGIN-UPGRADE",
        )
        BoxModel = old_apps.get_model("cultures", "Box")
        parent = BoxModel.objects.create(
            organization=org, strain=strain, global_code="ORIGIN-UPGRADE.001", box_number="001",
            polyp_state_revision=43,
        )
        child = BoxModel.objects.create(
            organization=org, strain=strain, global_code="ORIGIN-UPGRADE.002", box_number="002",
            polyp_state_revision=1,
        )
        Measurement = old_apps.get_model("measurements", "BiologicalMeasurement")
        for index, (user, strobila, sequence) in enumerate(((writer, 0, 40), (None, None, None), (writer, 8, 41))):
            day = date(2026, 8, 3) + timedelta(days=7 * index)
            row = Measurement.objects.create(
                box=parent, measured_on=day, week_start=day, polyp_count=0 if index == 0 else 100,
                ephyrae_count=index, strobila_count=strobila, salinity_psu=Decimal("0.00") if index == 0 else None,
                culture_status="medium", needs_attention=True, notes=f"Historical notes {index}",
                user=user, polyp_state_sequence=sequence,
            )
            Measurement.objects.filter(pk=row.pk).update(created_at=timezone.now() - timedelta(days=90 + index))
        Event = old_apps.get_model("cultures", "SubcultureEvent")
        event = Event.objects.create(
            parent_box=parent, event_date=date(2026, 8, 20), user=writer, occurred_at=timezone.now(),
            parent_state_sequence=42, parent_polyp_count_before=100, allocated_polyp_count=30,
            parent_polyp_count_after=70, parent_state_snapshot={"polyp_count": 100, "source": "synthetic"},
            author_name="Existing subculture snapshot", reason="Synthetic migration evidence", notes="Keep event",
        )
        old_apps.get_model("cultures", "SubcultureAllocation").objects.create(
            event=event, child_box=child, position=0, allocated_polyps=30, child_state_sequence=1,
            child_global_code=child.global_code,
        )
        old_apps.get_model("cultures", "BoxLineage").objects.create(
            parent_box=parent, child_box=child, subculture_event=event, notes="Keep lineage",
        )
        old_apps.get_model("audit", "AuditLog").objects.create(
            organization=org, user=writer, action="update", object_type="box", object_id=parent.global_code,
            description="Synthetic historical correction", metadata={"measurement_id": row.pk, "valeurs": {"polypes": 0}},
            original_values={"notes": "Unchanged audit evidence"}, edited_at=timezone.now(), edited_by=writer,
        )
        labels = [
            ("measurements", "BiologicalMeasurement"), ("cultures", "Box"),
            ("cultures", "SubcultureEvent"), ("cultures", "SubcultureAllocation"),
            ("cultures", "BoxLineage"), ("audit", "AuditLog"),
        ]
        snapshots = {
            label: list(old_apps.get_model(*label).objects.order_by("pk").values()) for label in labels
        }
        targets = [node for node in latest if node[0] != "measurements"] + [("measurements", "0008_observer_origin")]
        with patch.object(BiologicalMeasurement, "save", side_effect=AssertionError("No current-model backfill")):
            executor = MigrationExecutor(connection)
            executor.migrate(targets)
        new_apps = executor.loader.project_state(targets).apps
        for label, rows in snapshots.items():
            with self.subTest(model=label):
                fields = list(rows[0])
                self.assertEqual(list(new_apps.get_model(*label).objects.order_by("pk").values(*fields)), rows)
        Measurement = new_apps.get_model("measurements", "BiologicalMeasurement")
        self.assertEqual(Measurement.objects.count(), 3)
        for row in Measurement.objects.order_by("pk"):
            self.assertEqual(
                tuple(getattr(row, field) for field in PROVENANCE_FIELDS),
                ("legacy_unestablished", None, "legacy_unclassified", "", None),
            )
        self.assertEqual(new_apps.get_model("cultures", "Box").objects.get(pk=parent.pk).polyp_state_revision, 43)
