from datetime import date

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.measurements.scientific_snapshot import SELECTORS, canonical_json, validate_snapshot
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, Species, Strain

from .models import Box, BoxLineage, PortableLineageNode, ThermalZone
from .scientific_projection import project_box_snapshot

ALL = sorted(SELECTORS)
OBSERVATION_VALUES = [
    "observation.time", "observation.polyp_count", "observation.ephyrae_count", "observation.strobila_count",
]


class ScientificProjectionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.source = Organization.objects.create(name="Synthetic source lab", slug="synthetic-source")
        cls.foreign = Organization.objects.create(name="Synthetic foreign lab", slug="synthetic-foreign")
        cls.viewer = User.objects.create_user(
            username="projection-viewer", email="viewer@example.test", first_name="Vera", last_name="Viewer",
        )
        cls.outsider = User.objects.create_user(username="projection-outsider", email="outsider@example.test")
        OrganizationMembership.objects.create(
            user=cls.viewer, organization=cls.source, role=OrganizationMembership.Role.VIEWER,
        )
        OrganizationMembership.objects.create(
            user=cls.outsider, organization=cls.foreign, role=OrganizationMembership.Role.ADMIN,
        )
        cls.species = Species.objects.create(scientific_name="Aurelia synthetica")
        cls.strain = Strain.objects.create(species=cls.species, organization=cls.source, code="SYN-AAA-1")
        cls.zone = ThermalZone.objects.create(organization=cls.source, name="Synthetic cabinet")
        cls.box = Box.objects.create(
            organization=cls.source, strain=cls.strain, global_code="SYN-AAA-1.001",
            box_number="1", thermal_zone=cls.zone,
        )
        cls.sibling = Box.objects.create(
            organization=cls.source, strain=cls.strain, global_code="SYN-AAA-1.777", box_number="777",
        )
        cls.foreign_strain = Strain.objects.create(species=cls.species, organization=cls.foreign, code="FOR-AAA-1")
        cls.foreign_box = Box.objects.create(
            organization=cls.foreign, strain=cls.foreign_strain, global_code="FOR-AAA-1.001", box_number="1",
        )
        BoxLineage.objects.create(parent_box=cls.box, child_box=cls.sibling)
        cls.zero = BiologicalMeasurement.objects.create(
            box=cls.box, measured_on=date(2026, 4, 15), polyp_count=0, ephyrae_count=0, strobila_count=None,
            notes="private synthetic note", user=cls.viewer,
            environment_source_box=cls.sibling,
        )
        cls.earlier = BiologicalMeasurement.objects.create(
            box=cls.box, measured_on=date(2021, 1, 3), polyp_count=12, ephyrae_count=3, strobila_count=0,
        )
        BiologicalMeasurement.objects.create(
            box=cls.sibling, measured_on=date(2026, 4, 15), polyp_count=999, ephyrae_count=998,
        )
        BiologicalMeasurement.objects.create(
            box=cls.foreign_box, measured_on=date(2026, 4, 15), polyp_count=555, ephyrae_count=554,
        )

    def project(self, selectors=ALL, **kwargs):
        return project_box_snapshot(
            actor=kwargs.pop("actor", self.viewer),
            source_organization=kwargs.pop("source_organization", self.source),
            box_id=kwargs.pop("box_id", self.box.pk),
            selectors=selectors,
            **kwargs,
        )

    def state(self):
        return {
            "measurements": list(BiologicalMeasurement.objects.order_by("pk").values()),
            "boxes": list(Box.objects.order_by("pk").values()),
            "audit": AuditLog.objects.count(),
            "lineage_nodes": PortableLineageNode.objects.count(),
            "global_identities": GlobalStrainIdentity.objects.count(),
            "strains": list(Strain.objects.order_by("pk").values()),
        }

    def test_projects_selected_scientific_content(self):
        result = self.project()
        self.assertTrue(result.ok, result.diagnostics)
        snapshot = result.snapshot
        self.assertEqual(validate_snapshot(snapshot), ())
        self.assertEqual(snapshot["source"], {
            "institution_portable_id": {"state": "value", "value": str(self.source.portable_id)},
            "institution_name": {"state": "value", "value": "Synthetic source lab"},
            "box_code": {"state": "value", "value": "SYN-AAA-1.001"},
        })
        self.assertEqual(snapshot["identity"]["species_scientific_name"]["value"], "Aurelia synthetica")
        self.assertEqual(snapshot["identity"]["strain_code"]["value"], "SYN-AAA-1")
        first, second = snapshot["observations"]
        # Ordered by ISO week; 2021-01-03 belongs to ISO week 2020-W53.
        self.assertEqual(first["time"], {
            "precision": "legacy_unverified", "iso_year": 2020, "iso_week": 53, "measured_on": "2021-01-03",
        })
        self.assertEqual(second["values"], {
            "polyp_count": {"state": "value", "value": 0},
            "ephyrae_count": {"state": "value", "value": 0},
            "strobila_count": {"state": "null"},
        })
        self.assertEqual(first["values"]["strobila_count"], {"state": "value", "value": 0})
        self.assertEqual(second["provenance"], {
            "origin": {"state": "value", "value": "legacy_unclassified"},
            "import_format": {"state": "null"},
            "observer_status": {"state": "value", "value": "legacy_unestablished"},
        })

    def test_exact_date_precision_is_preserved_not_inferred(self):
        BiologicalMeasurement.objects.filter(pk=self.zero.pk).update(temporal_precision="exact_date")
        times = [item["time"]["precision"] for item in self.project(["observation.time"]).snapshot["observations"]]
        self.assertEqual(times, ["legacy_unverified", "exact_date"])

    def test_no_user_identity_notes_other_box_or_lineage_is_disclosed(self):
        BiologicalMeasurement.objects.filter(pk=self.zero.pk).update(
            observer_status="known", original_observer=self.viewer, origin="direct_entry",
        )
        text = canonical_json(self.project().snapshot)
        for hidden in (
            "projection-viewer", "viewer@example.test", "Vera", "VIEWER", "private synthetic note",
            self.sibling.global_code, '"value":999', '"value":998', '"value":555', self.foreign_box.global_code,
            "lineage", "parent", "child", "environment", "user", "notes", '"id"',
        ):
            self.assertNotIn(hidden, text)
        # The attribution status is disclosed; the observer is not.
        self.assertIn('"observer_status":{"state":"value","value":"known"}', text)

    def test_unselected_information_is_absent(self):
        snapshot = self.project(["observation.time", "observation.ephyrae_count"]).snapshot
        self.assertEqual(set(snapshot), {"schema", "schema_version", "disclosed", "observations"})
        for item in snapshot["observations"]:
            self.assertEqual(set(item), {"time", "values"})
            self.assertEqual(set(item["values"]), {"ephyrae_count"})
        text = canonical_json(snapshot)
        self.assertNotIn(self.box.global_code, text)
        self.assertNotIn(self.source.name, text)
        self.assertNotIn('"value":12', text)
        self.assertNotIn("polyp_count", text)

        identity_only = self.project(["identity.species_scientific_name"]).snapshot
        self.assertEqual(set(identity_only), {"schema", "schema_version", "disclosed", "identity"})

    def test_unknown_selectors_are_rejected_before_any_scientific_read(self):
        with CaptureQueriesContext(connection) as queries:
            result = self.project(["observation.time", "box__notes", "observation.temperature"])
        self.assertIsNone(result.snapshot)
        self.assertEqual({item.code for item in result.diagnostics}, {"unknown_selector"})
        sql = " ".join(query["sql"] for query in queries.captured_queries)
        self.assertNotIn("measurements_biologicalmeasurement", sql)
        self.assertNotIn("cultures_box", sql)

    def test_workflow_requirements_are_server_side(self):
        result = self.project(["observation.time"], required_selectors={"source.box_code"})
        self.assertEqual([item.code for item in result.diagnostics], ["required_selector_missing"])
        self.assertTrue(self.project(["source.box_code"], required_selectors={"source.box_code"}).ok)

    def test_box_without_observation_differs_from_undisclosed_observations(self):
        empty = Box.objects.create(
            organization=self.source, strain=self.strain, global_code="SYN-AAA-1.002", box_number="2",
        )
        self.assertEqual(self.project(OBSERVATION_VALUES, box_id=empty.pk).snapshot["observations"], [])
        self.assertNotIn("observations", self.project(["source.box_code"], box_id=empty.pk).snapshot)

    def test_duplicate_evidence_is_not_a_second_observation(self):
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 4, 16), polyp_count=7, ephyrae_count=7, duplicate_of=self.zero,
        )
        snapshot = self.project(OBSERVATION_VALUES).snapshot
        self.assertEqual(len(snapshot["observations"]), 2)
        self.assertNotIn('"value":7', canonical_json(snapshot))

    def test_global_identity_is_reported_only_when_established(self):
        before = GlobalStrainIdentity.objects.count()
        snapshot = self.project(["identity.global_strain_id"]).snapshot
        self.assertEqual(snapshot["identity"], {"global_strain_id": {"state": "null"}})
        self.assertEqual(GlobalStrainIdentity.objects.count(), before)
        identity = GlobalStrainIdentity.objects.create()
        Strain.objects.filter(pk=self.strain.pk).update(global_identity=identity)
        snapshot = self.project(["identity.global_strain_id"]).snapshot
        self.assertEqual(snapshot["identity"]["global_strain_id"], {"state": "value", "value": str(identity.global_id)})

    def test_foreign_merged_and_missing_boxes_are_indistinguishable(self):
        retired = Box.objects.create(
            organization=self.source, strain=self.strain, global_code="SYN-AAA-1.003", box_number="3",
            status=Box.Status.INACTIVE, thermal_zone=None,
        )
        Box.objects.filter(pk=retired.pk).update(merged_into=self.box, previous_global_code="SYN-AAA-1.003")
        missing = Box.objects.order_by("-pk").first().pk + 1000
        results = [
            self.project(box_id=box_id) for box_id in (self.foreign_box.pk, retired.pk, missing)
        ]
        for result in results:
            self.assertIsNone(result.snapshot)
            self.assertEqual([(item.code, item.path) for item in result.diagnostics], [("box_not_found", "box_id")])
        for box_id in ("1", True, 0, None, str(self.box.pk)):
            self.assertEqual(
                [item.code for item in self.project(box_id=box_id).diagnostics], ["box_reference_invalid"],
            )

    def test_foreign_strain_reference_is_not_disclosed(self):
        Box.objects.filter(pk=self.box.pk).update(strain=self.foreign_strain)
        result = self.project(["identity.strain_code"])
        self.assertIsNone(result.snapshot)
        self.assertEqual([item.code for item in result.diagnostics], ["strain_not_eligible"])

    def test_unauthorized_actors_are_refused(self):
        inactive = get_user_model().objects.create_user(
            username="inactive-member", email="inactive@example.test", is_active=False,
        )
        OrganizationMembership.objects.create(
            user=inactive, organization=self.source, role=OrganizationMembership.Role.ADMIN,
        )
        revoked = get_user_model().objects.create_user(username="revoked-member", email="revoked@example.test")
        OrganizationMembership.objects.create(
            user=revoked, organization=self.source, role=OrganizationMembership.Role.ADMIN, is_active=False,
        )
        for actor in (None, AnonymousUser(), self.outsider, inactive, revoked):
            with self.subTest(actor=actor):
                with self.assertRaises(PermissionDenied):
                    self.project(actor=actor)
        # An outsider cannot reach the source Box through their own institution.
        result = self.project(actor=self.outsider, source_organization=self.foreign)
        self.assertEqual([item.code for item in result.diagnostics], ["box_not_found"])
        with self.assertRaises(PermissionDenied):
            self.project(source_organization=Organization(name="unsaved"))

    def test_inactive_source_institution_is_refused(self):
        Organization.objects.filter(pk=self.source.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.project()

    def test_inconsistent_stored_week_key_is_reported_not_repaired(self):
        BiologicalMeasurement.objects.filter(pk=self.zero.pk).update(week_start=date(2026, 4, 6))
        before = self.state()
        result = self.project(["observation.time"])
        self.assertIsNone(result.snapshot)
        self.assertEqual([(item.code, item.path) for item in result.diagnostics], [
            # Positions follow ISO-week order: the corrupted row is second.
            ("stored_time_inconsistent", "observations[1]"),
        ])
        self.assertEqual(self.state(), before)

    def test_projection_is_deterministic_and_does_not_mutate(self):
        before = self.state()
        first = canonical_json(self.project().snapshot)
        second = canonical_json(self.project(list(reversed(ALL))).snapshot)
        self.assertEqual(first, second)
        self.assertEqual(self.state(), before)
        self.assertFalse(PortableLineageNode.objects.exists())

    def test_query_count_does_not_grow_with_observations(self):
        with CaptureQueriesContext(connection) as small:
            self.project()
        for week in range(1, 15):
            BiologicalMeasurement.objects.create(
                box=self.box, measured_on=date(2024, 1, 1).fromisocalendar(2024, week, 3),
                polyp_count=week, ephyrae_count=0,
            )
        with CaptureQueriesContext(connection) as large:
            result = self.project()
        self.assertEqual(len(result.snapshot["observations"]), 16)
        self.assertEqual(len(small.captured_queries), len(large.captured_queries))
        self.assertLessEqual(len(large.captured_queries), 4)

    def test_identity_relations_are_read_only_when_identity_is_disclosed(self):
        identity_tables = ("taxonomy_strain", "taxonomy_species", "taxonomy_globalstrainidentity")
        with CaptureQueriesContext(connection) as queries:
            result = self.project(OBSERVATION_VALUES)
        self.assertTrue(result.ok, result.diagnostics)
        statements = [query["sql"] for query in queries.captured_queries]
        sql = " ".join(statements)
        for table in identity_tables:
            self.assertNotIn(table, sql)
        # The mandatory scope read is still constrained on the Box row itself.
        box_sql = next(statement for statement in statements if 'FROM "cultures_box"' in statement)
        for constraint in ('"cultures_box"."organization_id"', '"cultures_box"."merged_into_id" IS NULL'):
            self.assertIn(constraint, box_sql)
        for column in ("notes", "global_code", "stop_reason", "volume_liters"):
            self.assertNotIn(f'"cultures_box"."{column}"', box_sql)
        # Measurement columns are limited to the disclosed ones.
        measurement_sql = next(s for s in statements if 'FROM "measurements_biologicalmeasurement"' in s)
        for column in ("notes", "user_id", "original_observer_id", "imported_by_id", "salinity_psu"):
            self.assertNotIn(f'"measurements_biologicalmeasurement"."{column}"', measurement_sql)

        # Authorization is unchanged without identity selectors.
        with self.assertRaises(PermissionDenied):
            self.project(OBSERVATION_VALUES, actor=self.outsider)
        foreign = self.project(OBSERVATION_VALUES, box_id=self.foreign_box.pk)
        self.assertEqual([item.code for item in foreign.diagnostics], ["box_not_found"])

        with CaptureQueriesContext(connection) as queries:
            result = self.project(["identity.strain_code"])
        self.assertTrue(result.ok, result.diagnostics)
        sql = " ".join(query["sql"] for query in queries.captured_queries)
        self.assertIn("taxonomy_strain", sql)
        self.assertIn("taxonomy_species", sql)
        # Strain eligibility is still checked whenever identity is disclosed.
        eligibility = [q["sql"] for q in queries.captured_queries if 'FROM "taxonomy_strain" WHERE' in q["sql"]]
        self.assertEqual(len(eligibility), 1)
        self.assertIn(f'"taxonomy_strain"."organization_id" = {self.source.pk}', eligibility[0])
