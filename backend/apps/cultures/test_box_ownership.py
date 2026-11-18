from datetime import timedelta
from uuid import UUID, uuid4

from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement, Observation
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, Species, Strain

from .box_ownership import boxes_with_ownership_history, enroll_origin_box, verify_ownership_chain
from .models import (
    Box, BoxLineage, BoxLocation, BoxMovement, BoxOwnershipEpisode, BoxTransferImport,
    GlobalBoxIdentity, PortableLineageEdge, PortableLineageNode, SubcultureEvent, ThermalZone,
)


class BoxOwnershipTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org_a = Organization.objects.create(name="Institution A", slug="own-a")
        cls.org_b = Organization.objects.create(name="Institution B", slug="own-b")
        cls.org_c = Organization.objects.create(name="Institution C", slug="own-c")
        cls.species = Species.objects.create(scientific_name="Aurelia aurita")
        cls.strain_identity = GlobalStrainIdentity.objects.create()
        cls.strain = Strain.objects.create(
            species=cls.species, organization=cls.org_a, code="AAU-OWN",
            global_identity=cls.strain_identity,
        )
        cls.zone_a = ThermalZone.objects.create(organization=cls.org_a, name="Tank A")
        cls.a1 = cls.box(cls.org_a, "OWN-A.001", zone=cls.zone_a)
        cls.b1 = cls.box(cls.org_b, "OWN-B.001")
        cls.c1 = cls.box(cls.org_c, "OWN-C.001")
        cls.a2 = cls.box(cls.org_a, "OWN-A.002")

    @classmethod
    def box(cls, organization, code, zone=None):
        return Box.objects.create(
            organization=organization, strain=cls.strain, global_code=code,
            box_number=code.rsplit(".", 1)[-1], thermal_zone=zone,
        )

    def succeed(self, previous, organization, box, at=None):
        """Model-level succession used only to prove the schema; no service exists yet."""
        at = at or (previous.started_at or timezone.now()) + timedelta(days=1)
        with transaction.atomic():
            previous.ended_at = at
            previous.save(update_fields=["ended_at"])
            return BoxOwnershipEpisode.objects.create(
                identity=previous.identity, sequence=previous.sequence + 1,
                organization=organization, local_box=box, started_at=at,
                arrival_transfer_id=uuid4(), arrival_item_id=uuid4(),
            )

    def refresh(self, episode):
        return BoxOwnershipEpisode.objects.get(pk=episode.pk)


class GlobalIdentityTests(BoxOwnershipTestCase):
    def test_enrollment_is_stable_idempotent_and_distinct_from_other_identifiers(self):
        first = enroll_origin_box(organization=self.org_a, box=self.a1)
        again = enroll_origin_box(organization=self.org_a, box=self.a1)
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(GlobalBoxIdentity.objects.count(), 1)
        identity = first.identity
        self.assertIsInstance(identity.global_box_id, UUID)
        self.assertNotEqual(identity.global_box_id, self.strain_identity.global_id)
        self.assertNotEqual(str(identity.global_box_id), self.a1.global_code)
        self.assertEqual((first.sequence, first.organization_id, first.local_box_id), (1, self.org_a.pk, self.a1.pk))
        self.assertIsNone(first.started_at)  # unknown acquisition, never reconstructed
        self.assertIsNone(first.ended_at)
        self.assertIsNone(first.arrival_transfer_id)

    def test_identity_is_immutable_and_protected(self):
        identity = enroll_origin_box(organization=self.org_a, box=self.a1).identity
        original = identity.global_box_id
        identity.global_box_id = uuid4()
        with self.assertRaises(ModelValidationError):
            identity.save()
        with self.assertRaises(ModelValidationError):
            GlobalBoxIdentity.objects.filter(pk=identity.pk).update(global_box_id=uuid4())
        with self.assertRaises(ModelValidationError):
            identity.delete()
        with self.assertRaises(ModelValidationError):
            GlobalBoxIdentity.objects.all().delete()
        self.assertEqual(GlobalBoxIdentity.objects.get().global_box_id, original)

    def test_each_enrolled_box_gets_its_own_identity(self):
        first = enroll_origin_box(organization=self.org_a, box=self.a1)
        second = enroll_origin_box(organization=self.org_a, box=self.a2)
        self.assertNotEqual(first.identity_id, second.identity_id)


class ChainTests(BoxOwnershipTestCase):
    def test_a_to_b_to_c_keeps_every_representation(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        b1 = self.succeed(a1, self.org_b, self.b1)
        c1 = self.succeed(b1, self.org_c, self.c1)
        episodes = list(a1.identity.ownership_episodes.order_by("sequence"))
        self.assertEqual(
            [(e.sequence, e.organization_id, e.local_box_id) for e in episodes],
            [(1, self.org_a.pk, self.a1.pk), (2, self.org_b.pk, self.b1.pk), (3, self.org_c.pk, self.c1.pk)],
        )
        self.assertEqual([e.pk for e in episodes if e.ended_at is None], [c1.pk])
        self.assertEqual(verify_ownership_chain(a1.identity), [])
        # Local identifiers and institutions are untouched.
        for box, org, code in ((self.a1, self.org_a, "OWN-A.001"), (self.b1, self.org_b, "OWN-B.001"),
                               (self.c1, self.org_c, "OWN-C.001")):
            box.refresh_from_db()
            self.assertEqual((box.organization_id, box.global_code), (org.pk, code))

    def test_return_to_a_appends_new_representation_without_overwriting_a1(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        b1 = self.succeed(a1, self.org_b, self.b1)
        a2 = self.succeed(b1, self.org_a, self.a2)
        a1 = self.refresh(a1)
        self.assertEqual((a1.local_box_id, a1.sequence), (self.a1.pk, 1))
        self.assertIsNotNone(a1.ended_at)
        self.assertEqual((a2.local_box_id, a2.sequence, a2.organization_id), (self.a2.pk, 3, self.org_a.pk))
        self.assertEqual(verify_ownership_chain(a1.identity), [])
        self.assertEqual(self.a1.ownership_episode.pk, a1.pk)

    def test_chronological_provenance_is_preserved(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        at = timezone.now()
        b1 = self.succeed(a1, self.org_b, self.b1, at=at)
        a1 = self.refresh(a1)
        self.assertEqual(a1.ended_at, b1.started_at)
        self.assertIsNotNone(b1.arrival_transfer_id)
        self.assertIsNotNone(b1.arrival_item_id)


class ExclusiveOwnershipTests(BoxOwnershipTestCase):
    def test_model_refuses_second_current_owner(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        with self.assertRaises(ModelValidationError):
            BoxOwnershipEpisode.objects.create(
                identity=a1.identity, sequence=2, organization=self.org_b, local_box=self.b1,
                started_at=timezone.now(), arrival_transfer_id=uuid4(), arrival_item_id=uuid4(),
            )
        self.assertEqual(a1.identity.ownership_episodes.count(), 1)

    def test_database_refuses_second_current_owner_even_bypassing_save(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        row = BoxOwnershipEpisode(
            identity=a1.identity, sequence=2, organization=self.org_b, local_box=self.b1,
            started_at=timezone.now(), arrival_transfer_id=uuid4(), arrival_item_id=uuid4(),
        )
        with self.assertRaisesMessage(IntegrityError, ""), transaction.atomic():
            BoxOwnershipEpisode.objects.bulk_create([row])

    def test_database_refuses_duplicate_sequence(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        at = timezone.now()
        row = BoxOwnershipEpisode(
            identity=a1.identity, sequence=1, organization=self.org_b, local_box=self.b1, ended_at=at,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            BoxOwnershipEpisode.objects.bulk_create([row])


class InvalidRelationshipTests(BoxOwnershipTestCase):
    def create(self, **kwargs):
        return BoxOwnershipEpisode.objects.create(**kwargs)

    def test_enrollment_refuses_foreign_institution_box(self):
        with self.assertRaises(ValidationError) as caught:
            enroll_origin_box(organization=self.org_a, box=self.b1)
        self.assertEqual(caught.exception.get_codes(), {"box": "box_not_in_organization"})
        self.assertFalse(BoxOwnershipEpisode.objects.exists())
        self.assertFalse(GlobalBoxIdentity.objects.exists())

    def test_representation_must_belong_to_episode_institution(self):
        identity = GlobalBoxIdentity.objects.create()
        with self.assertRaises(ModelValidationError):
            self.create(identity=identity, sequence=1, organization=self.org_a, local_box=self.b1)

    def test_box_cannot_represent_two_episodes(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        other = GlobalBoxIdentity.objects.create()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create(identity=other, sequence=1, organization=self.org_a, local_box=self.a1)
        self.assertEqual(self.refresh(a1).identity_id, a1.identity_id)

    def test_sequence_must_be_gapless(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        at = timezone.now()
        a1.ended_at = at
        a1.save(update_fields=["ended_at"])
        with self.assertRaises(ModelValidationError):
            self.create(identity=a1.identity, sequence=3, organization=self.org_b, local_box=self.b1,
                        started_at=at, arrival_transfer_id=uuid4(), arrival_item_id=uuid4())
        with self.assertRaises(ModelValidationError):
            self.create(identity=GlobalBoxIdentity.objects.create(), sequence=2,
                        organization=self.org_b, local_box=self.b1, started_at=at,
                        arrival_transfer_id=uuid4(), arrival_item_id=uuid4())

    def test_succession_must_change_institution_and_be_continuous(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        at = timezone.now()
        a1.ended_at = at
        a1.save(update_fields=["ended_at"])
        provenance = {"arrival_transfer_id": uuid4(), "arrival_item_id": uuid4()}
        with self.assertRaises(ModelValidationError):
            self.create(identity=a1.identity, sequence=2, organization=self.org_a,
                        local_box=self.a2, started_at=at, **provenance)
        with self.assertRaises(ModelValidationError):
            self.create(identity=a1.identity, sequence=2, organization=self.org_b,
                        local_box=self.b1, started_at=at + timedelta(seconds=1), **provenance)

    def test_arrival_provenance_check_constraint(self):
        identity = GlobalBoxIdentity.objects.create()
        invalid_rows = [
            # An origin cannot cite a transfer.
            BoxOwnershipEpisode(identity=identity, sequence=1, organization=self.org_a, local_box=self.a1,
                                arrival_transfer_id=uuid4(), arrival_item_id=uuid4()),
            # A continuation must cite its transfer item and start time.
            BoxOwnershipEpisode(identity=identity, sequence=2, organization=self.org_b, local_box=self.b1,
                                started_at=timezone.now()),
            BoxOwnershipEpisode(identity=identity, sequence=2, organization=self.org_b, local_box=self.b1,
                                arrival_transfer_id=uuid4(), arrival_item_id=uuid4()),
            BoxOwnershipEpisode(identity=identity, sequence=0, organization=self.org_a, local_box=self.a1),
        ]
        for row in invalid_rows:
            with self.subTest(sequence=row.sequence), self.assertRaises(IntegrityError), transaction.atomic():
                BoxOwnershipEpisode.objects.bulk_create([row])

    def test_one_transfer_item_carries_one_arrival(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        b1 = self.succeed(a1, self.org_b, self.b1)
        other = enroll_origin_box(organization=self.org_a, box=self.a2)
        at = timezone.now() + timedelta(days=3)
        other.ended_at = at
        other.save(update_fields=["ended_at"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.create(identity=other.identity, sequence=2, organization=self.org_c, local_box=self.c1,
                        started_at=at, arrival_transfer_id=b1.arrival_transfer_id,
                        arrival_item_id=b1.arrival_item_id)

    def test_closed_episode_is_immutable_and_only_end_can_be_recorded(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        a1.organization = self.org_b
        with self.assertRaises(ModelValidationError):
            a1.save()
        a1 = self.refresh(a1)
        a1.ended_at = timezone.now()
        a1.save(update_fields=["ended_at"])
        a1.ended_at = timezone.now() + timedelta(days=1)
        with self.assertRaises(ModelValidationError):
            a1.save(update_fields=["ended_at"])
        with self.assertRaises(ModelValidationError):
            BoxOwnershipEpisode.objects.filter(pk=a1.pk).update(ended_at=None)
        with self.assertRaises(ModelValidationError):
            a1.delete()
        with self.assertRaises(ModelValidationError):
            BoxOwnershipEpisode.objects.all().delete()

    def test_end_cannot_precede_start(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        b1 = self.succeed(a1, self.org_b, self.b1)
        b1.ended_at = b1.started_at - timedelta(seconds=1)
        with self.assertRaises(ModelValidationError):
            b1.save(update_fields=["ended_at"])

    def test_merged_alias_and_transfer_v1_import_are_not_enrolled(self):
        alias = self.box(self.org_a, "OWN-A.900")
        Box.objects.filter(pk=alias.pk).update(merged_into=self.a1, status=Box.Status.INACTIVE)
        with self.assertRaises(ValidationError):
            enroll_origin_box(organization=self.org_a, box=alias)
        imported = self.box(self.org_b, "OWN-B.900")
        BoxTransferImport.objects.create(
            format_version="polypbase.box_transfer.v1", source_transfer_id="legacy-1",
            source_organization_name="Institution A", source_global_code="OWN-A.001",
            destination_organization=self.org_b, created_box=imported,
        )
        with self.assertRaises(ValidationError) as caught:
            enroll_origin_box(organization=self.org_b, box=imported)
        self.assertEqual(caught.exception.get_codes(), {"box": "box_identity_provenance_unresolved"})
        self.assertFalse(GlobalBoxIdentity.objects.exists())

    def test_verify_reports_cross_row_divergence(self):
        identity = GlobalBoxIdentity.objects.create()
        self.assertEqual(verify_ownership_chain(identity), ["identity_without_episode"])
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        # A technical SQL writer bypassing Box.save is detected, never repaired.
        Box.objects.filter(pk=self.a1.pk).update(organization=self.org_b)
        self.assertIn("representation_institution_mismatch", verify_ownership_chain(a1.identity))


class LegacyBoxCompatibilityTests(BoxOwnershipTestCase):
    def test_global_code_uniqueness_is_unchanged(self):
        enroll_origin_box(organization=self.org_a, box=self.a1)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Box.objects.bulk_create([Box(
                organization=self.org_b, strain=self.strain, global_code="OWN-A.001", box_number="1",
            )])

    def test_unenrolled_box_behaves_as_before(self):
        # Existing behavior preserved, including an administrative institution edit.
        self.a2.organization = self.org_b
        self.a2.save()
        self.a2.refresh_from_db()
        self.assertEqual(self.a2.organization_id, self.org_b.pk)
        self.assertFalse(BoxOwnershipEpisode.objects.exists())

    def test_enrolled_box_cannot_be_reassigned_to_another_institution(self):
        enroll_origin_box(organization=self.org_a, box=self.a1)
        box = Box.objects.get(pk=self.a1.pk)
        box.organization = self.org_b
        with self.assertRaises(ModelValidationError):
            box.save()
        box = Box.objects.get(pk=self.a1.pk)
        box.organization_id = self.org_b.pk
        with self.assertRaises(ModelValidationError):
            box.save(update_fields=["organization"])
        self.assertEqual(Box.objects.get(pk=self.a1.pk).organization_id, self.org_a.pk)

    def test_enrolled_box_cannot_become_a_merged_alias(self):
        retired = self.box(self.org_a, "OWN-A.003")
        Box.objects.filter(pk=retired.pk).update(status=Box.Status.INACTIVE)
        enroll_origin_box(organization=self.org_a, box=retired)
        for update_fields in (None, ["merged_into"]):
            box = Box.objects.get(pk=retired.pk)
            box.merged_into = self.a1
            with self.subTest(update_fields=update_fields), self.assertRaises(ModelValidationError):
                box.save(update_fields=update_fields)
        self.assertIsNone(Box.objects.get(pk=retired.pk).merged_into_id)

    def test_unenrolled_box_alias_path_is_unchanged(self):
        retired = self.box(self.org_a, "OWN-A.004")
        Box.objects.filter(pk=retired.pk).update(status=Box.Status.INACTIVE)
        box = Box.objects.get(pk=retired.pk)
        box.merged_into = self.a1
        box.save(update_fields=["merged_into"])
        self.assertEqual(Box.objects.get(pk=retired.pk).merged_into_id, self.a1.pk)

    def test_boxes_with_ownership_history_includes_ended_episodes(self):
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        self.succeed(a1, self.org_b, self.b1)
        self.assertEqual(
            boxes_with_ownership_history([self.a1.pk, self.b1.pk, self.a2.pk]), {self.a1.pk, self.b1.pk},
        )

    def test_enrolled_box_keeps_ordinary_edits_and_lifecycle(self):
        enroll_origin_box(organization=self.org_a, box=self.a1)
        box = Box.objects.get(pk=self.a1.pk)
        box.notes = "Still editable"
        box.status = Box.Status.INACTIVE
        box.save()
        box.refresh_from_db()
        self.assertEqual((box.notes, box.status), ("Still editable", Box.Status.INACTIVE))

    def test_enrolled_box_is_protected_from_deletion(self):
        enroll_origin_box(organization=self.org_a, box=self.a1)
        with self.assertRaises(ProtectedError):
            Box.objects.filter(pk=self.a1.pk).delete()

    def test_identity_writes_touch_no_scientific_or_lifecycle_history(self):
        zero = BiologicalMeasurement.objects.create(box=self.a1, measured_on=timezone.localdate(), polyp_count=0)
        null = BiologicalMeasurement.objects.create(
            box=self.a1, measured_on=timezone.localdate() - timedelta(days=7), polyp_count=3,
            strobila_count=None,
        )
        Observation.objects.create(box=self.a1, notes="Keep")
        tracked = (BiologicalMeasurement, Observation, BoxLocation, BoxMovement, BoxLineage,
                   SubcultureEvent, AuditLog, PortableLineageNode, PortableLineageEdge)
        before = {model: list(model.objects.order_by("pk").values()) for model in tracked}
        box_before = Box.objects.filter(pk__in=[self.a1.pk, self.b1.pk, self.a2.pk]).order_by("pk").values()
        box_before = list(box_before)
        a1 = enroll_origin_box(organization=self.org_a, box=self.a1)
        b1 = self.succeed(a1, self.org_b, self.b1)
        self.succeed(b1, self.org_a, self.a2)
        after = {model: list(model.objects.order_by("pk").values()) for model in tracked}
        self.assertEqual(after, before)
        self.assertEqual(
            list(Box.objects.filter(pk__in=[self.a1.pk, self.b1.pk, self.a2.pk]).order_by("pk").values()),
            box_before,
        )
        # 0 stays a real measurement, NULL stays unknown; nothing is copied to B1.
        self.assertEqual(BiologicalMeasurement.objects.get(pk=zero.pk).polyp_count, 0)
        self.assertIsNone(BiologicalMeasurement.objects.get(pk=null.pk).strobila_count)
        self.assertFalse(BiologicalMeasurement.objects.filter(box__in=[self.b1, self.a2]).exists())
