"""Current display identity is additive and never rewrites historical provenance."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .biological_timeline import biological_timeline
from .models import Box, BoxLineage, BoxMovement, BoxTransfer, SubcultureEvent, ThermalZone
from .polyp_state import resolve_current_polyp_state
from .serializers import (
    BiologicalMeasurementSerializer,
    BoxMovementSerializer,
    BoxTransferCreateSerializer,
    SubcultureEventSerializer,
)
from .services import create_subculture


class CulturesUserIdentityTests(TestCase):
    def setUp(self):
        self.organization = Organization.objects.create(name="Identity laboratory")
        self.foreign_organization = Organization.objects.create(name="Foreign laboratory")
        self.user = get_user_model().objects.create_user(
            username="raw-author-snapshot", first_name="Camille", last_name="Martin",
            email="camille@example.org",
        )
        self.viewer = get_user_model().objects.create_user(
                    username="identity-viewer", email="identity-viewer@example.org",
                )
        for user, role in ((self.user, OrganizationMembership.Role.LAB_TECHNICIAN),
                           (self.viewer, OrganizationMembership.Role.VIEWER)):
            OrganizationMembership.objects.create(user=user, organization=self.organization, role=role)
        OrganizationMembership.objects.create(
            user=self.viewer, organization=self.foreign_organization,
            role=OrganizationMembership.Role.VIEWER,
        )
        self.zone = ThermalZone.objects.create(organization=self.organization, name="Identity zone")
        species = Species.objects.create(scientific_name="Aurelia identity", genus_species_code="AID")
        strain = Strain.objects.create(species=species, organization=self.organization, code="AID-ID-1")
        self.box = Box.objects.create(
            organization=self.organization, strain=strain, thermal_zone=self.zone,
            global_code="AID-ID-1.001", box_number="001",
        )
        self.measurement = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=timezone.localdate(), polyp_count=10,
            ephyrae_count=0, strobila_count=0, user=self.user,
        )
        self.movement = BoxMovement.objects.create(box=self.box, to_thermal_zone=self.zone, user=self.user)
        self.box.refresh_from_db()
        self.event, self.children = create_subculture(
            parent_box=self.box, organization=self.organization, user=self.user,
            expected_current_state_revision=resolve_current_polyp_state(self.box)["revision"],
            children=[{"thermal_zone": self.zone, "allocated_polyps": 0}],
        )
        self.identity = {"first_name": "Camille", "last_name": "Martin", "email": "camille@example.org"}
        self.client.force_login(self.viewer)

    def get(self, name, box):
        response = self.client.get(reverse(name, args=[box.pk]),
            HTTP_X_ORGANIZATION_ID=str(self.organization.pk))
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def assert_serialized_identity(self, identity, raw_user):
        for serializer, obj in (
            (BiologicalMeasurementSerializer, BiologicalMeasurement.objects.get(pk=self.measurement.pk)),
            (BoxMovementSerializer, BoxMovement.objects.get(pk=self.movement.pk)),
            (SubcultureEventSerializer, SubcultureEvent.objects.get(pk=self.event.pk)),
        ):
            with self.subTest(serializer=serializer.__name__):
                data = serializer(obj).data
                self.assertEqual(data["user_identity"], identity)
                self.assertEqual(data["user"], raw_user)

    def assert_history_identity(self, identity, raw_user, snapshot_username):
        parent = self.get("api_box_detail", self.box)
        self.assertEqual(parent["latest_measurement"]["user_identity"], identity)
        self.assertEqual(parent["movements"][0]["user_identity"], identity)
        self.assertEqual({entry["kind"] for entry in parent["biological_timeline"]}, {"measurement", "subculture"})
        for entry in parent["biological_timeline"]:
            self.assertEqual(entry["user_identity"], identity)
            self.assertEqual(entry["author"]["username"],
                raw_user if entry["kind"] == "measurement" else snapshot_username)
            if entry["kind"] == "measurement":
                self.assertEqual(entry["measurement"]["user_identity"], identity)
                self.assertEqual(entry["measurement"]["user"], raw_user)
        child = self.get("api_box_detail", self.children[0])
        initialization = child["biological_timeline"][0]
        self.assertEqual(initialization["kind"], "subculture_initialization")
        self.assertEqual(initialization["polyp_count_after"], 0)
        self.assertEqual(initialization["user_identity"], identity)
        self.assertEqual(initialization["author"]["username"], snapshot_username)
        graph = self.get("api_box_lineage", self.box)
        for event in (parent["lineage"]["children"][0]["event"],
                      child["lineage"]["parents"][0]["event"], graph["edges"][0]["event"]):
            self.assertEqual(event["user_identity"], identity)
            self.assertEqual(event["user"], raw_user)

    def test_live_identity_is_additive_on_all_culture_history_surfaces(self):
        self.assert_serialized_identity(self.identity, self.user.username)
        self.assert_history_identity(self.identity, self.user.username, self.event.author_name)
        self.assertEqual(self.event.author_name, "raw-author-snapshot")

    def test_current_identity_changes_without_rewriting_snapshots(self):
        snapshot = self.event.parent_state_snapshot
        audit = AuditLog.objects.get(action=AuditLog.Action.SUBCULTURE)
        audit_metadata = audit.metadata
        self.user.first_name = "Claude"
        self.user.last_name = "Durand"
        self.user.email = "claude@example.org"
        self.user.username = "renamed-raw-user"
        self.user.save()
        identity = {"first_name": "Claude", "last_name": "Durand", "email": "claude@example.org"}
        self.assert_serialized_identity(identity, self.user.username)
        self.assert_history_identity(identity, self.user.username, "raw-author-snapshot")
        self.event.refresh_from_db()
        audit.refresh_from_db()
        self.assertEqual(self.event.author_name, "raw-author-snapshot")
        self.assertEqual(self.event.parent_state_snapshot, snapshot)
        self.assertEqual(audit.metadata, audit_metadata)

    def test_deleted_author_is_null_identity_and_keeps_raw_snapshot(self):
        snapshot = self.event.parent_state_snapshot
        audit = AuditLog.objects.get(action=AuditLog.Action.SUBCULTURE)
        audit_metadata = audit.metadata
        self.user.delete()
        self.assert_serialized_identity(None, None)
        self.assert_history_identity(None, None, "raw-author-snapshot")
        self.event.refresh_from_db()
        audit.refresh_from_db()
        self.assertIsNone(self.event.user_id)
        self.assertEqual(self.event.author_name, "raw-author-snapshot")
        self.assertEqual(self.event.parent_state_snapshot, snapshot)
        self.assertEqual(audit.metadata, audit_metadata)

    def test_legacy_event_uses_live_identity_or_null_without_parsing_snapshot(self):
        for user in (self.user, None):
            with self.subTest(user=user):
                event = SubcultureEvent.objects.create(
                    parent_box=self.box, user=user, author_name="unparsed historical username",
                )
                identity = self.identity if user else None
                data = SubcultureEventSerializer(event).data
                self.assertEqual(data["user_identity"], identity)
                self.assertEqual(data["user"], self.user.username if user else None)
                entry = next(row for row in biological_timeline(self.box, context={})
                    if row["kind"] == "subculture" and row["id"] == event.pk)
                self.assertEqual(entry["user_identity"], identity)
                self.assertEqual(entry["author"]["username"], "unparsed historical username")
                event.refresh_from_db()
                self.assertEqual(event.author_name, "unparsed historical username")
                self.assertIsNone(event.occurred_at)

    def test_live_user_with_empty_names_has_structured_identity_not_snapshot(self):
        self.user.first_name = self.user.last_name = ""
        self.user.save()
        identity = {"first_name": "", "last_name": "", "email": self.user.email}
        self.assert_serialized_identity(identity, self.user.username)
        self.assert_history_identity(identity, self.user.username, self.event.author_name)

    def test_measurement_identity_is_read_only(self):
        serializer = BiologicalMeasurementSerializer(self.measurement, data={
            "user_identity": {"first_name": "Forged", "last_name": "Author", "email": "forged@example.org"},
        }, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertNotIn("user_identity", serializer.validated_data)
        self.assertEqual(serializer.data["user_identity"], self.identity)

    def test_transfer_label_uses_name_then_email_never_username(self):
        transfer = BoxTransfer.objects.create(
            box=self.box, from_organization=self.organization,
            to_organization=self.foreign_organization, polyp_count=1, user=self.user,
        )
        for first_name, last_name, email, label in (
            ("Camille", "Martin", "camille@example.org", "Camille MARTIN"),
            ("Camille", "", "camille@example.org", "Camille"),
            ("", "Martin", "camille@example.org", "MARTIN"),
            ("", "", "camille@example.org", "camille@example.org"),

        ):
            with self.subTest(first_name=first_name, last_name=last_name, email=email):
                self.user.first_name, self.user.last_name, self.user.email = first_name, last_name, email
                self.user.save()
                transfer.refresh_from_db()
                self.assertEqual(BoxTransferCreateSerializer(transfer).data["prepared_by"], label)
                self.assertEqual(transfer.user_id, self.user.pk)
        self.user.email = ""
        transfer.user = self.user
        self.assertIsNone(BoxTransferCreateSerializer(transfer).data["prepared_by"])
        self.user.delete()
        transfer.refresh_from_db()
        self.assertIsNone(BoxTransferCreateSerializer(transfer).data["prepared_by"])

    def test_active_organization_hides_foreign_identity_and_lineage(self):
        foreign_user = get_user_model().objects.create_user(
            username="foreign-private-user", first_name="Private", last_name="Foreign",
            email="private-foreign@example.org",
        )
        strain = Strain.objects.create(
            species=self.box.strain.species, organization=self.foreign_organization, code="AID-FOREIGN-1",
        )
        foreign_box = Box.objects.create(organization=self.foreign_organization, strain=strain,
            global_code="AID-FOREIGN-1.001", box_number="001")
        BiologicalMeasurement.objects.create(box=foreign_box, measured_on=timezone.localdate(),
            polyp_count=0, ephyrae_count=0, strobila_count=0, user=foreign_user)
        foreign_event = SubcultureEvent.objects.create(parent_box=foreign_box, user=foreign_user)
        BoxLineage.objects.create(parent_box=self.box, child_box=foreign_box, subculture_event=foreign_event)
        parent = self.get("api_box_detail", self.box)
        graph = self.get("api_box_lineage", self.box)
        self.assertEqual(len(parent["lineage"]["children"]), 1)
        self.assertEqual(len(graph["edges"]), 1)
        for data in (parent, graph):
            self.assertNotIn(foreign_user.email, str(data))
            self.assertNotIn(foreign_user.username, str(data))
            self.assertNotIn(foreign_box.global_code, str(data))
        for endpoint in ("api_box_detail", "api_box_lineage"):
            response = self.client.get(reverse(endpoint, args=[foreign_box.pk]),
                HTTP_X_ORGANIZATION_ID=str(self.organization.pk))
            self.assertEqual(response.status_code, 404)
            self.assertNotContains(response, foreign_user.email, status_code=404)
