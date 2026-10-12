"""Synthetic, isolated tests of the read-only measurement import preview."""

import csv
import hashlib
import json
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import Alert, AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .canonical import PAYLOAD_FIELDS, SourceCoordinate
from .csv_import import CsvLimits, read_csv
from .import_planner import build_measurement_import_plan
from .models import BiologicalMeasurement


class MeasurementImportPlannerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="Synthetic planner laboratory")
        cls.foreign_org = Organization.objects.create(name="Foreign planner laboratory")
        users = get_user_model()
        cls.admin = users.objects.create_user(username="planner-admin", email="planner-admin@example.org")
        cls.technician = users.objects.create_user(username="planner-technician", email="planner-tech@example.org")
        cls.viewer = users.objects.create_user(username="planner-viewer", email="planner-viewer@example.org")
        cls.outsider = users.objects.create_user(username="planner-outsider", email="planner-outsider@example.org")
        cls.superuser = users.objects.create_user(
            username="planner-superuser", email="planner-superuser@example.org", is_superuser=True, is_staff=True,
        )
        for user, role in (
            (cls.admin, OrganizationMembership.Role.ADMIN),
            (cls.technician, OrganizationMembership.Role.LAB_TECHNICIAN),
            (cls.viewer, OrganizationMembership.Role.VIEWER),
        ):
            OrganizationMembership.objects.create(user=user, organization=cls.org, role=role)
        OrganizationMembership.objects.create(
            user=cls.outsider, organization=cls.foreign_org,
            role=OrganizationMembership.Role.ADMIN,
        )
        species = Species.objects.create(scientific_name="Aurelia planner", genus_species_code="APL")
        cls.strain = Strain.objects.create(organization=cls.org, species=species, code="APL-QA-1")
        foreign_strain = Strain.objects.create(
            organization=cls.foreign_org, species=species, code="APL-OTHER-1",
        )
        cls.box = Box.objects.create(
            organization=cls.org, strain=cls.strain, global_code="APL-QA-1.001",
            local_code="local-planner-box", box_number="001", status=Box.Status.ACTIVE,
        )
        cls.foreign_box = Box.objects.create(
            organization=cls.foreign_org, strain=foreign_strain,
            global_code="APL-OTHER-1.001", box_number="001", status=Box.Status.INACTIVE,
        )
        cls.day = date(2026, 9, 14)

    def prepare(self, rows=None, *, fields=None, source_id="synthetic-planner.csv", limits=None):
        fields = fields or ("box_reference", "measured_on", "polyp_count", "ephyrae_count")
        if rows is None:
            rows = [self.source()]
        output = StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(fields)
        for row in rows:
            writer.writerow([row.get(field, "") for field in fields])
        return read_csv(
            output.getvalue().encode("utf-8"), source_id=source_id,
            mapping={field: field for field in fields}, confirmed=True,
            **({"limits": limits} if limits is not None else {}),
        )

    def source(self, **changes):
        values = {
            "box_reference": self.box.global_code, "measured_on": self.day.isoformat(),
            "polyp_count": "0", "ephyrae_count": "0",
        }
        values.update(changes)
        return values

    def measurement(self, **changes):
        values = dict(box=self.box, measured_on=self.day, polyp_count=0, ephyrae_count=0)
        values.update(changes)
        return BiologicalMeasurement.objects.create(**values)

    def reject_non_select(self, execute, sql, params, many, context):
        self.assertEqual(sql.lstrip().split(None, 1)[0].upper(), "SELECT", sql)
        return execute(sql, params, many, context)

    def plan(self, preparation=None, *, actor=None, organization=None):
        # Guard every preview, including serialization, against attempted SQL writes.
        with connection.execute_wrapper(self.reject_non_select):
            result = build_measurement_import_plan(
                self.prepare() if preparation is None else preparation,
                organization=self.org if organization is None else organization,
                actor=self.admin if actor is None else actor,
            )
            self.assertEqual(
                result.fingerprint,
                hashlib.sha256(result.canonical_json().encode("utf-8")).hexdigest(),
            )
            return result

    def codes(self, row):
        return {issue.code for issue in row.issues}

    def assert_outcome(self, preparation, outcome, *, ready):
        plan = self.plan(preparation)
        self.assertEqual([row.outcome for row in plan.rows], [outcome])
        self.assertEqual(plan.ready, ready)
        self.assertEqual(plan.summary.ready, ready)
        return plan.rows[0]

    def test_exact_create_preserves_zero_canonical_row_and_provenance(self):
        fields = ("box_reference", "measured_on", *PAYLOAD_FIELDS)
        preparation = self.prepare([self.source(
            strobila_count="0", salinity_psu="0", culture_status="good",
            needs_attention="false", notes=' first, line\nsecond "quote" ',
        )], fields=fields)
        plan = self.plan(preparation)
        row = plan.rows[0]
        self.assertEqual(row.outcome, "CREATE")
        self.assertTrue(plan.ready)
        self.assertIs(row.source, preparation.records[0])
        self.assertIs(row.source.row, preparation.records[0].row)
        self.assertEqual(row.source.provenance.coordinate, SourceCoordinate("synthetic-planner.csv", 2, 2, 3))
        self.assertEqual(row.source.provenance.original_values[-1], ' first, line\nsecond "quote" ')
        values = {value.field: value for value in row.source.row.values}
        self.assertEqual([values[field].value for field in PAYLOAD_FIELDS[:3]], [0, 0, 0])
        self.assertEqual(values["salinity_psu"].value, "0.00")
        self.assertIsNone(row.existing)
        self.assertEqual(row.box.id, self.box.pk)
        self.assertEqual(row.box.organization_id, self.org.pk)
        self.assertEqual(row.issues, ())
        self.assertEqual(plan.summary.create_candidates, 1)
        self.assertEqual(plan.preparation_fingerprint, preparation.fingerprint)

    def test_every_explicit_scientific_payload_field_is_compared(self):
        stored = dict(
            polyp_count=7, ephyrae_count=2, strobila_count=3,
            salinity_psu=Decimal("31.25"), culture_status="good",
            needs_attention=True, notes="Original observation",
        )
        self.assertEqual(set(stored), set(PAYLOAD_FIELDS))
        measurement = self.measurement(**stored)
        source = self.source(**{
            **{field: str(value) for field, value in stored.items()},
            "needs_attention": "true",
        })
        fields = ("box_reference", "measured_on", *PAYLOAD_FIELDS)
        row = self.assert_outcome(self.prepare([source], fields=fields), "ALREADY_MATCHES", ready=True)
        self.assertEqual(row.existing.id, measurement.pk)
        self.assertEqual(dict(row.existing.values), stored | {"salinity_psu": "31.25"})
        different = dict(
            polyp_count="0", ephyrae_count="0", strobila_count="0",
            salinity_psu="0", culture_status="bad", needs_attention="false", notes="Changed",
        )
        for field in PAYLOAD_FIELDS:
            with self.subTest(field=field):
                row = self.assert_outcome(
                    self.prepare([source | {field: different[field]}], fields=fields), "CONFLICT", ready=False,
                )
                self.assertEqual([(issue.code, issue.field, issue.column) for issue in row.issues], [
                    ("supplied_value_conflict", field, fields.index(field) + 1),
                ])

    def test_nullable_explicit_unknown_matches_null_but_not_scientific_zero(self):
        measurement = self.measurement(strobila_count=None, salinity_psu=None)
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "strobila_count", "salinity_psu")
        preparation = self.prepare(fields=fields)
        row = self.assert_outcome(preparation, "ALREADY_MATCHES", ready=True)
        self.assertIsNone(dict(row.existing.values)["strobila_count"])
        for field, zero in (("strobila_count", 0), ("salinity_psu", Decimal("0.00"))):
            with self.subTest(field=field):
                setattr(measurement, field, zero)
                measurement.save(update_fields=[field])
                row = self.assert_outcome(preparation, "CONFLICT", ready=False)
                self.assertIn(("supplied_value_conflict", field), [(issue.code, issue.field) for issue in row.issues])
                setattr(measurement, field, None)
                measurement.save(update_fields=[field])
        row = self.assert_outcome(self.prepare([self.source(strobila_count="0", salinity_psu="0")], fields=fields), "CONFLICT", ready=False)
        self.assertEqual({issue.field for issue in row.issues}, {"strobila_count", "salinity_psu"})

    def test_absent_optional_fields_do_not_compare_or_apply_defaults(self):
        self.measurement(strobila_count=9, salinity_psu=Decimal("32.50"), culture_status="bad", needs_attention=True, notes="Keep history")
        row = self.assert_outcome(self.prepare(), "ALREADY_MATCHES", ready=True)
        values = {value.field: value for value in row.source.row.values}
        for field in PAYLOAD_FIELDS[2:]:
            self.assertEqual(values[field].state, "absent")

    def test_nonnullable_optional_blanks_do_not_clear_existing_values(self):
        self.measurement(culture_status="bad", needs_attention=True, notes="Keep history")
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "culture_status", "needs_attention", "notes")
        row = self.assert_outcome(self.prepare(fields=fields), "ALREADY_MATCHES", ready=True)
        values = {value.field: value for value in row.source.row.values}
        for field in fields[4:]:
            self.assertEqual(values[field].state, "blank")

    def test_signed_salinity_zero_matches_numeric_zero_without_losing_source_sign(self):
        self.measurement(salinity_psu=Decimal("0.00"))
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "salinity_psu")
        for value in ("-0", "-0.0", "-0.00", "0"):
            with self.subTest(value=value):
                row = self.assert_outcome(
                    self.prepare([self.source(salinity_psu=value)], fields=fields),
                    "ALREADY_MATCHES", ready=True,
                )
                supplied = next(field for field in row.source.row.values if field.field == "salinity_psu")
                self.assertEqual(supplied.value, "-0.00" if value.startswith("-") else "0.00")
        plan = self.plan(self.prepare([
            self.source(salinity_psu="0"), self.source(salinity_psu="-0.00"),
        ], fields=fields))
        self.assertEqual(plan.duplicate_groups[0].code, "duplicate_source_slot")
        self.assertFalse(plan.ready)

    def test_zero_existing_measurement_is_not_absence_of_a_measurement(self):
        self.assert_outcome(self.prepare(), "CREATE", ready=True)
        self.measurement(strobila_count=0, salinity_psu=Decimal("0.00"))
        row = self.assert_outcome(self.prepare(), "ALREADY_MATCHES", ready=True)
        self.assertEqual(dict(row.existing.values)["polyp_count"], 0)
        self.assertEqual(dict(row.existing.values)["ephyrae_count"], 0)

    def test_exact_date_in_same_week_still_conflicts(self):
        measurement = self.measurement(measured_on=self.day + timedelta(days=2))
        row = self.assert_outcome(self.prepare(), "CONFLICT", ready=False)
        self.assertEqual(self.codes(row), {"exact_date_conflict"})
        self.assertEqual(row.existing.measured_on, measurement.measured_on.isoformat())
        self.assertEqual(row.source.row.time.measured_on, self.day.isoformat())

    def test_week_only_matches_existing_without_inventing_precise_source_date(self):
        measurement = self.measurement(measured_on=self.day + timedelta(days=2))
        iso = self.day.isocalendar()
        fields = ("box_reference", "iso_year", "iso_week", "polyp_count", "ephyrae_count")
        preparation = self.prepare([self.source(iso_year=str(iso.year), iso_week=str(iso.week))], fields=fields)
        row = self.assert_outcome(preparation, "ALREADY_MATCHES", ready=True)
        self.assertEqual(row.week_start, self.day.isoformat())
        self.assertIsNone(row.source.row.time.measured_on)
        self.assertEqual(row.source.row.time.precision, "iso_week")
        self.assertEqual(row.existing.measured_on, measurement.measured_on.isoformat())

    def test_week_only_create_is_diagnostic_and_not_ready(self):
        iso = self.day.isocalendar()
        fields = ("box_reference", "iso_year", "iso_week", "polyp_count", "ephyrae_count")
        preparation = self.prepare([self.source(iso_year=str(iso.year), iso_week=str(iso.week))], fields=fields)
        plan = self.plan(preparation)
        self.assertEqual(plan.rows[0].outcome, "CREATE")
        self.assertEqual(self.codes(plan.rows[0]), {"week_precision_persistence_required"})
        self.assertIsNone(plan.rows[0].source.row.time.measured_on)
        self.assertFalse(plan.ready)
        self.assertEqual((plan.summary.create_candidates, plan.summary.invalid_or_blocked, plan.summary.blocked_rows), (1, 1, 1))

    def test_iso_year_boundary_resolves_existing_week(self):
        self.measurement(measured_on=date(2021, 1, 1))
        fields = ("box_reference", "iso_year", "iso_week", "polyp_count", "ephyrae_count")
        row = self.assert_outcome(self.prepare([self.source(iso_year="2020", iso_week="53")], fields=fields), "ALREADY_MATCHES", ready=True)
        self.assertEqual(row.week_start, "2020-12-28")
        self.assertEqual(row.existing.measured_on, "2021-01-01")

    def test_named_observer_blocks_create_and_matching_rows(self):
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "observer_reference")
        preparation = self.prepare([self.source(observer_reference=self.admin.username)], fields=fields)
        row = self.assert_outcome(preparation, "CREATE", ready=False)
        self.assertEqual(self.codes(row), {"observer_resolution_unsupported"})
        self.measurement(user=self.admin)
        row = self.assert_outcome(preparation, "BLOCKED", ready=False)
        self.assertEqual([(issue.code, issue.field, issue.column) for issue in row.issues], [
            ("observer_resolution_unsupported", "observer_reference", 5),
        ])

    def test_importing_actor_is_separate_from_historical_observer(self):
        measurement = self.measurement(user=self.viewer)
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "observer_reference")
        for preparation in (self.prepare(), self.prepare(fields=fields)):
            with self.subTest(observer_state=preparation.records[0].row.values[-1].state):
                plan = self.plan(preparation, actor=self.technician)
                self.assertTrue(plan.ready)
                self.assertEqual(plan.rows[0].outcome, "ALREADY_MATCHES")
                self.assertEqual(plan.importing_actor_id, self.technician.pk)
                self.assertEqual(plan.rows[0].existing.observer_id, self.viewer.pk)
                self.assertEqual(plan.rows[0].existing.created_at, measurement.created_at.isoformat())
        measurement.user = None
        measurement.save(update_fields=["user"])
        self.assertIsNone(self.plan().rows[0].existing.observer_id)

    def test_active_admin_and_technician_can_plan(self):
        for actor in (self.admin, self.technician):
            with self.subTest(actor=actor.username):
                self.assertTrue(self.plan(actor=actor).ready)

    def test_viewer_outsider_superuser_and_anonymous_cannot_plan(self):
        for actor in (self.viewer, self.outsider, self.superuser, AnonymousUser(), get_user_model()(username="unsaved")):
            with self.subTest(actor=actor), self.assertRaises(PermissionDenied):
                self.plan(actor=actor)
        with connection.execute_wrapper(self.reject_non_select), self.assertRaises(PermissionDenied):
            build_measurement_import_plan(self.prepare(), organization=self.org, actor=None)

    def test_superuser_requires_its_own_active_write_membership(self):
        membership = OrganizationMembership.objects.create(
            user=self.superuser, organization=self.org, role=OrganizationMembership.Role.VIEWER,
        )
        with self.assertRaises(PermissionDenied):
            self.plan(actor=self.superuser)
        membership.role = OrganizationMembership.Role.ADMIN
        membership.save(update_fields=["role"])
        self.assertTrue(self.plan(actor=self.superuser).ready)
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            self.plan(actor=self.superuser)

    def test_membership_deactivation_or_downgrade_is_rechecked(self):
        membership = OrganizationMembership.objects.get(user=self.admin, organization=self.org)
        self.assertTrue(self.plan().ready)
        for changes in ({"is_active": False}, {"is_active": True, "role": "viewer"}):
            with self.subTest(changes=changes):
                OrganizationMembership.objects.filter(pk=membership.pk).update(**changes)
                with self.assertRaises(PermissionDenied):
                    self.plan()

    def test_actor_and_institution_active_state_are_reloaded(self):
        self.assertTrue(self.admin.is_active)
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.plan()
        get_user_model().objects.filter(pk=self.admin.pk).update(is_active=True)
        self.assertTrue(self.org.is_active)
        Organization.objects.filter(pk=self.org.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.plan()

    def test_exact_global_code_only_no_local_numeric_case_or_fuzzy_lookup(self):
        for reference in (self.box.local_code, self.box.box_number, str(self.box.pk), self.box.global_code.lower(), "APL-QA-1", "missing-box"):
            with self.subTest(reference=reference):
                row = self.assert_outcome(self.prepare([self.source(box_reference=reference)]), "UNKNOWN_BOX", ready=False)
                self.assertIsNone(row.box)
                self.assertIsNone(row.existing)
                self.assertEqual(self.codes(row), {"unknown_box"})
        self.assert_outcome(self.prepare(), "CREATE", ready=True)

    def test_foreign_box_never_exposes_baseline_even_for_multi_institution_admin(self):
        OrganizationMembership.objects.create(user=self.admin, organization=self.foreign_org, role="admin")
        self.measurement(box=self.foreign_box, polyp_count=987, user=self.outsider, notes="Foreign confidential observation")
        preparation = self.prepare([self.source(box_reference=self.foreign_box.global_code)])
        plan = self.plan(preparation)
        row = plan.rows[0]
        self.assertEqual(row.outcome, "UNKNOWN_BOX")
        self.assertEqual(self.codes(row), {"unknown_box"})
        self.assertIsNone(row.box)
        self.assertIsNone(row.existing)
        self.assertNotIn("Foreign confidential observation", plan.canonical_json())
        self.assertNotIn("inactive_box", plan.canonical_json())
        other_plan = self.plan(preparation, organization=self.foreign_org)
        self.assertEqual(other_plan.rows[0].outcome, "CONFLICT")
        self.assertEqual(other_plan.rows[0].box.organization_id, self.foreign_org.pk)

    def test_all_identical_repeated_slots_conflict_including_existing_match(self):
        preparation = self.prepare([self.source()] * 3)
        for existing in (False, True):
            with self.subTest(existing=existing):
                if existing:
                    self.measurement()
                plan = self.plan(preparation)
                self.assertEqual([row.outcome for row in plan.rows], ["CONFLICT"] * 3)
                self.assertFalse(plan.ready)
                self.assertEqual(plan.summary.conflicts, 3)
                for row in plan.rows:
                    self.assertEqual(self.codes(row), {"duplicate_source_slot"})
                    self.assertEqual(row.duplicate_group_id, 1)
                    self.assertEqual(plan.duplicate_groups[0].sources, tuple(
                        source.provenance.coordinate for source in preparation.records
                    ))

    def test_large_duplicate_group_stores_coordinates_once(self):
        preparation = self.prepare([self.source()] * 1000)
        with self.assertNumQueries(5):
            plan = self.plan(preparation)
        self.assertEqual(len(plan.duplicate_groups), 1)
        self.assertEqual(len(plan.duplicate_groups[0].sources), 1000)
        self.assertTrue(all(row.duplicate_group_id == 1 for row in plan.rows))
        self.assertEqual(plan.summary.conflicts, 1000)
        serialized = json.loads(plan.canonical_json())
        self.assertNotIn("related_sources", serialized["rows"][0])
        self.assertEqual(len(serialized["duplicate_groups"][0]["sources"]), 1000)

    def test_all_contradictory_slots_conflict_for_different_values_or_time(self):
        for changed in (self.source(polyp_count="1"), self.source(measured_on=(self.day + timedelta(days=1)).isoformat())):
            with self.subTest(changed=changed):
                plan = self.plan(self.prepare([self.source(), changed, self.source()]))
                self.assertEqual([row.outcome for row in plan.rows], ["CONFLICT"] * 3)
                for row in plan.rows:
                    self.assertEqual(self.codes(row), {"contradictory_source_slot"})
                self.assertFalse(plan.ready)

    def test_observer_disagreement_is_unsupported_provenance_not_scientific_contradiction(self):
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "observer_reference")
        for other in ("Other observer", ""):
            with self.subTest(other=other):
                plan = self.plan(self.prepare([
                    self.source(observer_reference="First observer"), self.source(observer_reference=other),
                ], fields=fields))
                self.assertEqual(plan.duplicate_groups[0].code, "duplicate_source_slot")
                self.assertIn("observer_resolution_unsupported", self.codes(plan.rows[0]))
                self.assertFalse(plan.ready)

    def test_complementary_supplied_rows_are_duplicates_not_scientific_contradictions(self):
        fields = ("box_reference", "measured_on", "iso_year", "iso_week", *PAYLOAD_FIELDS)
        preparation = self.prepare([
            self.source(strobila_count="0", notes=""),
            self.source(measured_on="", iso_year="2026", iso_week="38", strobila_count="0", notes="note"),
        ], fields=fields)
        plan = self.plan(preparation)
        self.assertEqual([row.outcome for row in plan.rows], ["CONFLICT", "CONFLICT"])
        self.assertEqual(plan.duplicate_groups[0].code, "duplicate_source_slot")
        self.assertFalse(plan.ready)

    def test_unknown_result_is_unchanged_when_foreign_box_appears(self):
        reference = "APL-OTHER-1.999"
        preparation = self.prepare([self.source(box_reference=reference)])
        unknown = self.plan(preparation)
        foreign = Box.objects.create(
            organization=self.foreign_org, strain=self.foreign_box.strain,
            global_code=reference, box_number="999",
        )
        self.measurement(box=foreign, polyp_count=987, notes="Never disclose this")
        self.assertEqual(unknown, self.plan(preparation))
        self.assertEqual(unknown.canonical_json(), self.plan(preparation).canonical_json())

    def test_repeated_foreign_slots_remain_unknown_not_conflicts(self):
        preparation = self.prepare([self.source(box_reference=self.foreign_box.global_code)] * 2)
        plan = self.plan(preparation)
        self.assertEqual([row.outcome for row in plan.rows], ["UNKNOWN_BOX"] * 2)
        for row in plan.rows:
            self.assertIsNone(row.box)
            self.assertIsNone(row.existing)
            self.assertEqual(self.codes(row), {"unknown_box", "duplicate_source_slot"})

    def test_same_box_different_weeks_are_independent_slots(self):
        preparation = self.prepare([self.source(), self.source(measured_on=(self.day + timedelta(days=7)).isoformat())])
        plan = self.plan(preparation)
        self.assertEqual([row.outcome for row in plan.rows], ["CREATE", "CREATE"])
        self.assertTrue(plan.ready)
        self.assertNotEqual(plan.rows[0].week_start, plan.rows[1].week_start)

    def test_inactive_create_is_blocked_but_existing_match_is_allowed(self):
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.INACTIVE)
        row = self.assert_outcome(self.prepare(), "CREATE", ready=False)
        self.assertEqual(self.codes(row), {"inactive_box"})
        self.measurement()
        row = self.assert_outcome(self.prepare(), "ALREADY_MATCHES", ready=True)
        self.assertEqual(row.box.status, Box.Status.INACTIVE)
        self.assertEqual(row.issues, ())

    def test_complete_partial_invalid_and_blank_rows_never_allow_partial_readiness(self):
        preparation = self.prepare([
            self.source(), self.source(polyp_count=""),
            self.source(polyp_count="", ephyrae_count=""),
        ])
        plan = self.plan(preparation)
        self.assertTrue(preparation.complete)
        self.assertEqual([row.outcome for row in plan.rows], ["CREATE", "INVALID", "IGNORED"])
        self.assertFalse(plan.ready)
        self.assertEqual(plan.rows[1].issues, preparation.records[1].issues)
        for planned, source in zip(plan.rows, preparation.records):
            self.assertIs(planned.source, source)
        self.assertEqual((plan.summary.total_source_rows, plan.summary.ignored_rows, plan.summary.invalid_or_blocked), (3, 1, 1))

    def test_physical_and_prefilled_blank_rows_are_ignored_without_default_measurements(self):
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count")
        data = ",".join(fields) + "\n\n" + self.box.global_code + ",2026-09-14,,\n"
        preparation = read_csv(data.encode(), source_id="blank.csv", mapping={field: field for field in fields}, confirmed=True)
        plan = self.plan(preparation)
        self.assertTrue(plan.ready)
        self.assertEqual([row.outcome for row in plan.rows], ["IGNORED", "IGNORED"])
        self.assertEqual(plan.summary.create_candidates, 0)
        self.assertEqual(plan.summary.blocked_rows, 0)
        self.assertTrue(all(row.box is None and row.existing is None for row in plan.rows))

    def test_malformed_file_retains_candidates_but_blocks_entire_plan(self):
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count")
        for body in ('"unterminated', 'bad,2026-09-14,0,"0"x\n'):
            with self.subTest(body=body):
                data = ",".join(fields) + "\n" + self.box.global_code + ",2026-09-14,0,0\n" + body
                preparation = read_csv(data.encode(), source_id="malformed.csv", mapping={field: field for field in fields}, confirmed=True)
                plan = self.plan(preparation)
                self.assertFalse(plan.source_complete)
                self.assertFalse(plan.ready)
                self.assertEqual([row.outcome for row in plan.rows], ["CREATE", "INVALID"])
                self.assertEqual({issue.code for issue in plan.issues}, {"malformed_csv", "incomplete_preparation"})
                self.assertEqual(self.codes(plan.rows[1]), {"malformed_csv"})

    def test_row_limit_is_incomplete_even_when_preceding_candidate_is_valid(self):
        preparation = self.prepare([
            self.source(), self.source(measured_on=(self.day + timedelta(days=7)).isoformat()),
        ], limits=CsvLimits(max_rows=1))
        plan = self.plan(preparation)
        self.assertEqual([row.outcome for row in plan.rows], ["CREATE", "INVALID"])
        self.assertFalse(plan.ready)
        self.assertEqual({issue.code for issue in plan.issues}, {"row_limit", "incomplete_preparation"})

    def test_file_level_failure_without_rows_is_not_ready(self):
        preparation = read_csv(b"", source_id="empty.csv", mapping={}, confirmed=True)
        plan = self.plan(preparation)
        self.assertEqual(plan.rows, ())
        self.assertFalse(plan.ready)
        self.assertEqual(plan.summary.total_source_rows, 0)
        self.assertEqual({issue.code for issue in plan.issues}, {"missing_header", "incomplete_preparation"})

    def test_unsupported_schema_or_version_blocks_valid_rows_without_resolution(self):
        preparation = self.prepare()
        for changes in ({"schema_version": "2.0"}, {"schema": "other.measurement"}):
            with self.subTest(changes=changes), CaptureQueriesContext(connection) as queries:
                plan = self.plan(replace(preparation, **changes))
                self.assertEqual(plan.rows[0].outcome, "BLOCKED")
                self.assertIsNone(plan.rows[0].box)
                self.assertIsNone(plan.rows[0].existing)
                self.assertEqual(self.codes(plan.rows[0]), {"unsupported_preparation_version"})
                self.assertIn("unsupported_preparation_version", {issue.code for issue in plan.issues})
                self.assertFalse(plan.ready)
            self.assertFalse(any(Box._meta.db_table in query["sql"] or BiologicalMeasurement._meta.db_table in query["sql"] for query in queries))

    def test_baselines_are_frozen_value_snapshots_not_live_models(self):
        measurement = self.measurement(user=self.viewer)
        plan = self.plan()
        row = plan.rows[0]
        original_json, original_fingerprint = plan.canonical_json(), plan.fingerprint
        for target, field, value in (
            (row.box, "status", Box.Status.INACTIVE),
            (row.existing, "observer_id", self.admin.pk),
            (row, "outcome", "CREATE"),
            (plan, "organization_id", self.foreign_org.pk),
        ):
            with self.subTest(field=field), self.assertRaises(FrozenInstanceError):
                setattr(target, field, value)
        self.assertIsInstance(row.existing.values, tuple)
        self.assertEqual(row.existing.polyp_state_sequence, measurement.polyp_state_sequence)
        Box.objects.filter(pk=self.box.pk).update(status=Box.Status.INACTIVE)
        measurement.polyp_count = 8
        measurement.save(update_fields=["polyp_count"])
        self.assertEqual(plan.canonical_json(), original_json)
        self.assertEqual(plan.fingerprint, original_fingerprint)
        self.assertNotEqual(self.plan().fingerprint, original_fingerprint)

    def test_canonical_json_and_fingerprint_are_stable_and_context_sensitive(self):
        preparation = self.prepare()
        first, second = self.plan(preparation), self.plan(self.prepare())
        self.assertEqual(first, second)
        self.assertEqual(first.canonical_json(), second.canonical_json())
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.fingerprint, hashlib.sha256(first.canonical_json().encode("utf-8")).hexdigest())
        decoded = json.loads(first.canonical_json())
        self.assertEqual(decoded["summary"], asdict(first.summary))
        self.assertEqual(first.canonical_json(), json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
        self.assertNotEqual(first.fingerprint, self.plan(preparation, actor=self.technician).fingerprint)
        self.assertNotEqual(first.fingerprint, self.plan(self.prepare(source_id="other-source.csv")).fingerprint)
        self.assertNotEqual(first.fingerprint, self.plan(self.prepare([self.source(polyp_count="1")])).fingerprint)
        Box.objects.filter(pk=self.box.pk).update(polyp_state_revision=10)
        self.assertNotEqual(first.fingerprint, self.plan(preparation).fingerprint)

    def test_planning_all_outcomes_preserves_models_revisions_and_audit(self):
        self.measurement(user=self.viewer)
        self.measurement(box=self.foreign_box, polyp_count=42)
        AuditLog.objects.create(organization=self.org, user=self.admin, action=AuditLog.Action.IMPORT, metadata={"synthetic": True})
        Alert.objects.create(organization=self.org, box=self.box, alert_type=Alert.AlertType.BIOLOGICAL, message="Historical synthetic alert")
        fields = ("box_reference", "measured_on", "polyp_count", "ephyrae_count", "observer_reference")
        preparation = self.prepare([
            self.source(),
            self.source(measured_on=(self.day + timedelta(days=7)).isoformat()),
            self.source(measured_on=(self.day + timedelta(days=14)).isoformat(), observer_reference="Named observer"),
            self.source(box_reference=self.foreign_box.global_code),
            self.source(polyp_count=""),
            self.source(polyp_count="", ephyrae_count=""),
        ], fields=fields)
        # Snapshot all application tables, including full Box revisions and audit history.
        models = [model for model in apps.get_models() if model.__module__.startswith("apps.")]
        def snapshot():
            return {model._meta.label: list(model.objects.order_by("pk").values()) for model in models}
        before = snapshot()
        plan = self.plan(preparation)
        self.assertEqual([row.outcome for row in plan.rows], ["ALREADY_MATCHES", "CREATE", "CREATE", "UNKNOWN_BOX", "INVALID", "IGNORED"])
        self.assertFalse(plan.ready)
        self.assertEqual(asdict(plan.summary), dict(
            total_source_rows=6, ignored_rows=1, create_candidates=2, already_matching=1,
            conflicts=0, unknown_boxes=1, invalid_or_blocked=2, blocked_rows=3, ready=False,
        ))
        self.plan(self.prepare([self.source(polyp_count="1")]))
        self.plan(self.prepare([self.source(observer_reference="Named observer")], fields=fields))
        self.plan(self.prepare([self.source()] * 2))
        with self.assertRaises(PermissionDenied):
            self.plan(actor=self.viewer)
        self.assertEqual(snapshot(), before)

    def test_query_count_is_bounded_across_200_row_chunk_boundary(self):
        boxes = [Box(
            organization=self.org, strain=self.strain, global_code=f"APL-QA-1.{index:03d}",
            box_number=f"{index:03d}", status=Box.Status.ACTIVE,
        ) for index in range(2, 403)]
        Box.objects.bulk_create(boxes)
        # bulk_create intentionally bypasses fixture save hooks; planning must not
        # repair allocator state or materialize scientific defaults as a side effect.
        all_boxes = [self.box, *boxes]
        BiologicalMeasurement.objects.bulk_create([BiologicalMeasurement(
            box=box, measured_on=self.day, week_start=self.day, polyp_count=0, ephyrae_count=0,
        ) for box in all_boxes])
        for count in (1, 199, 200, 201, 400, 401):
            preparation = self.prepare([self.source(box_reference=box.global_code) for box in all_boxes[:count]])
            with self.subTest(count=count), CaptureQueriesContext(connection) as queries:
                plan = self.plan(preparation)
                self.assertTrue(plan.ready)
                self.assertEqual(len(plan.rows), count)
                self.assertTrue(all(row.outcome == "ALREADY_MATCHES" for row in plan.rows))
                self.assertEqual([row.box.id for row in plan.rows], [box.pk for box in all_boxes[:count]])
            batches = (count + 199) // 200
            self.assertLessEqual(len(queries), 3 + 2 * batches)
            self.assertEqual(sum(f'FROM "{Box._meta.db_table}"' in query["sql"] for query in queries), batches)
            self.assertEqual(sum(f'FROM "{BiologicalMeasurement._meta.db_table}"' in query["sql"] for query in queries), batches)

    def test_many_weeks_for_one_box_batch_measurement_slots_not_just_box_references(self):
        days = [self.day + timedelta(days=7 * index) for index in range(201)]
        BiologicalMeasurement.objects.bulk_create([BiologicalMeasurement(
            box=self.box, measured_on=day, week_start=day, polyp_count=0, ephyrae_count=0,
        ) for day in days])
        preparation = self.prepare([self.source(measured_on=day.isoformat()) for day in days])
        with CaptureQueriesContext(connection) as queries:
            plan = self.plan(preparation)
            self.assertTrue(plan.ready)
            self.assertEqual([row.outcome for row in plan.rows], ["ALREADY_MATCHES"] * 201)
        self.assertLessEqual(len(queries), 6)
        self.assertEqual(sum(f'FROM "{BiologicalMeasurement._meta.db_table}"' in query["sql"] for query in queries), 2)
