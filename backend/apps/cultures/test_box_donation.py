"""Whole-Box donation proposals: authority, isolation, verification, lifecycle."""

from datetime import date
from unittest import mock
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError as ModelValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, Species, Strain

from . import box_donation
from .box_ownership import enroll_origin_box
from .models import (
    Box, BoxDonationProposal, BoxLineage, BoxLocation, BoxMovement, BoxOwnershipEpisode,
    BoxTransferImport, GlobalBoxIdentity, SubcultureEvent, ThermalZone, TransferEnvelope, TransferItem,
)

Role = OrganizationMembership.Role
PREFLIGHT = reverse("api_box_donation_preflight")
OUTGOING = reverse("api_box_donation_outgoing")
INCOMING = reverse("api_box_donation_incoming")


def detail_url(proposal_id):
    return reverse("api_box_donation_detail", args=[proposal_id])


def cancel_url(proposal_id):
    return reverse("api_box_donation_cancel", args=[proposal_id])


class DonationTestCase(TestCase):
    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.org_a = Organization.objects.create(name="Donor A", slug="don-a")
        cls.org_b = Organization.objects.create(name="Receiver B", slug="don-b", contact_email="private@b.test")
        cls.org_c = Organization.objects.create(name="Third C", slug="don-c")

        def user(name, *memberships, **extra):
            account = User.objects.create_user(
                username=f"don-{name}", email=f"{name}@example.test", password="pw", **extra,
            )
            for organization, role in memberships:
                OrganizationMembership.objects.create(user=account, organization=organization, role=role)
            return account

        cls.admin_a = user("admin-a", (cls.org_a, Role.ADMIN))
        cls.admin_a2 = user("admin-a2", (cls.org_a, Role.ADMIN))
        cls.tech_a = user("tech-a", (cls.org_a, Role.LAB_TECHNICIAN))
        cls.viewer_a = user("viewer-a", (cls.org_a, Role.VIEWER))
        cls.admin_b = user("admin-b", (cls.org_b, Role.ADMIN))
        cls.tech_b = user("tech-b", (cls.org_b, Role.LAB_TECHNICIAN))
        cls.admin_c = user("admin-c", (cls.org_c, Role.ADMIN))
        # Admin in both A and C: context switching must never widen scope.
        cls.dual = user("dual", (cls.org_a, Role.ADMIN), (cls.org_c, Role.ADMIN))
        cls.root = user("root", is_superuser=True, is_staff=True)

        cls.species = Species.objects.create(scientific_name="Aurelia donata")
        cls.strain = Strain.objects.create(
            species=cls.species, organization=cls.org_a, code="DON-AAA-1",
            global_identity=GlobalStrainIdentity.objects.create(),
        )
        cls.zone_a = ThermalZone.objects.create(organization=cls.org_a, name="Tank A")
        cls.box_a = cls.make_box(cls.org_a, "DON-AAA-1.001", zone=cls.zone_a)
        cls.box_a_other = cls.make_box(cls.org_a, "DON-AAA-1.002")
        cls.strain_c = Strain.objects.create(
            species=cls.species, organization=cls.org_c, code="DON-CCC-1",
            global_identity=GlobalStrainIdentity.objects.create(),
        )
        cls.box_c = Box.objects.create(
            organization=cls.org_c, strain=cls.strain_c, global_code="DON-CCC-1.001", box_number="001",
        )
        BiologicalMeasurement.objects.create(
            box=cls.box_a, measured_on=date(2026, 3, 2), polyp_count=0, ephyrae_count=0, strobila_count=None,
        )

    @classmethod
    def make_box(cls, organization, code, zone=None, **extra):
        return Box.objects.create(
            organization=organization, strain=cls.strain, global_code=code,
            box_number=code.rsplit(".", 1)[-1], thermal_zone=zone, **extra,
        )

    def client_for(self, user, organization):
        client = APIClient()
        client.force_login(user)
        if organization is not None:
            client.credentials(HTTP_X_ORGANIZATION_ID=str(organization.pk))
        return client

    def preflight(self, user=None, organization=None, box=None, destination=None):
        return self.client_for(user or self.admin_a, organization or self.org_a).post(PREFLIGHT, {
            "source_box_id": (box or self.box_a).pk,
            "destination_institution_id": str((destination or self.org_b).portable_id),
        }, format="json")

    def payload(self, box=None, destination=None, **overrides):
        box = box or self.box_a
        destination = destination or self.org_b
        reference = box_donation.source_history_reference(self.org_a if box.organization_id == self.org_a.pk else box.organization, box)
        return {
            "request_id": str(uuid4()),
            "source_box_id": box.pk,
            "destination_institution_id": str(destination.portable_id),
            "history_verified": True,
            "history_sha256": reference["sha256"],
            **overrides,
        }

    def create(self, user=None, organization=None, data=None):
        return self.client_for(user or self.admin_a, organization or self.org_a).post(
            OUTGOING, data if data is not None else self.payload(), format="json",
        )


class PreflightTests(DonationTestCase):
    def test_preflight_is_read_only_and_returns_history_reference(self):
        audits = AuditLog.objects.count()
        response = self.preflight()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(response.data["eligible"])
        self.assertEqual(response.data["destination_institution"]["name"], "Receiver B")
        self.assertNotIn("contact_email", str(response.data))
        self.assertFalse(response.data["global_box_identity"]["enrolled"])
        self.assertEqual(len(response.data["history"]["sha256"]), 64)
        self.assertEqual(response.data["history"]["snapshot_version"], "1.0")
        self.assertEqual(response.data["history"]["observation_count"], 1)
        self.assertEqual(GlobalBoxIdentity.objects.count(), 0)
        self.assertEqual(BoxDonationProposal.objects.count(), 0)
        self.assertEqual(AuditLog.objects.count(), audits)
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_preflight_requires_admin_of_explicit_context(self):
        for user in (self.tech_a, self.viewer_a, self.admin_b):
            self.assertIn(self.preflight(user=user, organization=self.org_a).status_code, (403,))
        missing = self.client_for(self.admin_a, None).post(PREFLIGHT, {}, format="json")
        self.assertEqual(missing.status_code, 403)
        # A superuser has no shortcut for an outward institutional act.
        self.assertEqual(self.preflight(user=self.root).status_code, 403)

    def test_foreign_and_merged_boxes_are_indistinguishable(self):
        foreign = self.preflight(box=self.box_c)
        self.assertEqual((foreign.status_code, foreign.data["code"]), (404, "box_not_found"))
        alias = self.make_box(self.org_a, "DON-AAA-1.050")
        Box.objects.filter(pk=alias.pk).update(merged_into=self.box_a_other, status=Box.Status.INACTIVE)
        merged = self.preflight(box=alias)
        self.assertEqual((merged.status_code, merged.data["code"]), (404, "box_not_found"))

    def test_destination_must_be_an_active_distinct_registered_institution(self):
        self.assertEqual(self.preflight(destination=self.org_a).data["code"], "destination_is_source")
        unknown = self.client_for(self.admin_a, self.org_a).post(PREFLIGHT, {
            "source_box_id": self.box_a.pk, "destination_institution_id": str(uuid4()),
        }, format="json")
        self.assertEqual((unknown.status_code, unknown.data["code"]), (404, "destination_not_found"))
        Organization.objects.filter(pk=self.org_b.pk).update(is_active=False)
        self.assertEqual(self.preflight().data["code"], "destination_not_found")

    def test_client_cannot_supply_institution_or_identity_fields(self):
        response = self.client_for(self.admin_a, self.org_a).post(PREFLIGHT, {
            "source_box_id": self.box_a.pk, "destination_institution_id": str(self.org_b.portable_id),
            "source_organization_id": self.org_c.pk,
        }, format="json")
        self.assertEqual((response.status_code, response.data["code"]), (400, "invalid_request"))
        # Raw integer PKs and emails are not destination principals.
        for value in (self.org_b.pk, "admin-b@example.test"):
            response = self.client_for(self.admin_a, self.org_a).post(PREFLIGHT, {
                "source_box_id": self.box_a.pk, "destination_institution_id": value,
            }, format="json")
            self.assertEqual(response.status_code, 400)

    def test_ineligible_boxes_are_refused(self):
        inactive = self.make_box(self.org_a, "DON-AAA-1.060", status=Box.Status.INACTIVE)
        self.assertEqual(self.preflight(box=inactive).data["code"], "box_status_not_eligible")
        pending = self.make_box(self.org_a, "DON-AAA-1.061", status=Box.Status.PENDING_REVIEW)
        self.assertEqual(self.preflight(box=pending).data["code"], "box_status_not_eligible")
        imported = self.make_box(self.org_a, "DON-AAA-1.062")
        BoxTransferImport.objects.create(
            format_version="v1", source_transfer_id="t", source_organization_name="X",
            source_global_code="X.1", destination_organization=self.org_a, created_box=imported,
        )
        self.assertEqual(self.preflight(box=imported).data["code"], "box_identity_provenance_unresolved")
        no_identity = Strain.objects.create(species=self.species, organization=self.org_a, code="DON-NOI-1")
        box = Box.objects.create(
            organization=self.org_a, strain=no_identity, global_code="DON-NOI-1.001", box_number="001",
        )
        self.assertEqual(self.preflight(box=box).data["code"], "strain_global_identity_required")

    def test_former_representation_is_not_the_current_owner(self):
        episode = enroll_origin_box(organization=self.org_a, box=self.box_a)
        at = timezone.now()
        with transaction.atomic():
            episode.ended_at = at
            episode.save(update_fields=["ended_at"])
            BoxOwnershipEpisode.objects.create(
                identity=episode.identity, sequence=2, organization=self.org_c, local_box=self.box_c,
                started_at=at, arrival_transfer_id=uuid4(), arrival_item_id=uuid4(),
            )
        self.assertEqual(self.preflight().data["code"], "box_not_current_owner")
        payload = self.payload()
        self.assertEqual(self.create(data=payload).data["code"], "box_not_current_owner")


class CreationTests(DonationTestCase):
    def test_creation_enrolls_audits_and_freezes_snapshots_without_side_effects(self):
        tracked = (BiologicalMeasurement, BoxLocation, BoxMovement, BoxLineage, SubcultureEvent,
                   TransferEnvelope, TransferItem)
        before = {model: list(model.objects.order_by("pk").values()) for model in tracked}
        boxes_before = list(Box.objects.order_by("pk").values())
        response = self.create()
        self.assertEqual(response.status_code, 201, response.data)
        proposal = BoxDonationProposal.objects.get()
        self.assertEqual(proposal.state, "pending")
        self.assertEqual(proposal.revision, 1)
        self.assertEqual(proposal.source_organization, self.org_a)
        self.assertEqual(proposal.destination_organization, self.org_b)
        self.assertEqual(proposal.history_verified_by, self.admin_a)
        self.assertEqual(proposal.created_by, self.admin_a)
        episode = BoxOwnershipEpisode.objects.get(local_box=self.box_a)
        self.assertEqual((proposal.source_episode, proposal.identity), (episode, episode.identity))
        self.assertEqual(proposal.source_ownership_sequence, 1)
        self.assertEqual(proposal.source_box_code, "DON-AAA-1.001")
        self.assertEqual(proposal.global_strain_id, self.strain.global_identity.global_id)
        self.assertEqual(response.data["box"]["global_box_id"], str(episode.identity.global_box_id))
        # No ownership transition, destination Box, envelope, measurement or movement.
        self.assertIsNone(episode.ended_at)
        self.assertEqual(Box.objects.filter(organization=self.org_b).count(), 0)
        self.assertEqual({m: list(m.objects.order_by("pk").values()) for m in tracked}, before)
        self.assertEqual(list(Box.objects.order_by("pk").values()), boxes_before)
        audits = AuditLog.objects.filter(organization=self.org_a, user=self.admin_a).order_by("pk")
        self.assertEqual(
            [(a.object_type, a.action) for a in audits],
            [("global_box_identity", "creation"), ("box_donation_proposal", "transfer")],
        )
        self.assertEqual(audits[1].object_id, str(proposal.proposal_id))
        self.assertFalse(AuditLog.objects.filter(organization=self.org_b).exists())

    def test_existing_identity_is_reused_without_new_allocation_or_enrollment_audit(self):
        episode = enroll_origin_box(organization=self.org_a, box=self.box_a)
        self.assertEqual(self.create().status_code, 201)
        self.assertEqual(GlobalBoxIdentity.objects.count(), 1)
        self.assertEqual(BoxDonationProposal.objects.get().identity, episode.identity)
        self.assertFalse(AuditLog.objects.filter(object_type="global_box_identity").exists())

    def test_history_confirmation_is_required(self):
        for value in (False, None):
            payload = self.payload(history_verified=value)
            if value is None:
                payload.pop("history_verified")
            response = self.create(data=payload)
            self.assertEqual(response.status_code, 400)
        self.assertEqual(BoxDonationProposal.objects.count(), 0)
        self.assertEqual(GlobalBoxIdentity.objects.count(), 0)

    def test_exact_json_types_are_required(self):
        for override in ({"history_verified": "true"}, {"history_verified": 1},
                         {"history_sha256": "A" * 64}, {"source_box_id": str(self.box_a.pk)},
                         {"request_id": 5}):
            response = self.create(data=self.payload(**override))
            self.assertEqual((response.status_code, response.data["code"]), (400, "invalid_request"), override)
        self.assertEqual(BoxDonationProposal.objects.count(), 0)

    def test_deleted_measurement_invalidates_the_reference(self):
        payload = self.payload()
        BiologicalMeasurement.objects.filter(box=self.box_a).get().delete()
        self.assertEqual(self.create(data=payload).data["code"], "source_history_changed")

    def test_institution_rename_does_not_invalidate_the_reference(self):
        payload = self.payload()
        Organization.objects.filter(pk=self.org_a.pk).update(name="Donor A renamed")
        self.assertEqual(self.create(data=payload).status_code, 201)

    def test_stale_history_reference_is_refused_without_enrollment(self):
        payload = self.payload()
        BiologicalMeasurement.objects.create(box=self.box_a, measured_on=date(2026, 3, 9), polyp_count=4)
        response = self.create(data=payload)
        self.assertEqual((response.status_code, response.data["code"]), (409, "source_history_changed"))
        self.assertNotIn("sha256", str(response.data))
        self.assertEqual(GlobalBoxIdentity.objects.count(), 0)
        self.assertEqual(BoxDonationProposal.objects.count(), 0)

    def test_zero_and_null_are_distinct_in_the_history_reference(self):
        payload = self.payload()
        measurement = BiologicalMeasurement.objects.get(box=self.box_a)
        measurement.strobila_count = 0
        measurement.save()
        self.assertEqual(self.create(data=payload).data["code"], "source_history_changed")

    def test_idempotent_replay_and_conflicting_reuse_of_a_request_id(self):
        payload = self.payload()
        first = self.create(data=payload)
        replay = self.create(data=payload)
        self.assertEqual((first.status_code, replay.status_code), (201, 200))
        self.assertEqual(first.data["proposal_id"], replay.data["proposal_id"])
        self.assertEqual(BoxDonationProposal.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="box_donation_proposal").count(), 1)
        conflict = self.create(data={**payload, "destination_institution_id": str(self.org_c.portable_id)})
        self.assertEqual((conflict.status_code, conflict.data["code"]), (409, "proposal_request_conflict"))
        # The key is scoped to the actor: another Admin's key is independent.
        other = self.create(user=self.admin_a2, data=payload)
        self.assertEqual((other.status_code, other.data["code"]), (409, "donation_already_pending"))

    def test_replay_after_cancellation_returns_the_recorded_proposal(self):
        payload = self.payload()
        created = self.create(data=payload)
        self.client_for(self.admin_a, self.org_a).post(
            cancel_url(created.data["proposal_id"]), {"expected_revision": 1}, format="json",
        )
        replay = self.create(data=payload)
        self.assertEqual((replay.status_code, replay.data["state"]), (200, "cancelled"))
        self.assertEqual(BoxDonationProposal.objects.count(), 1)

    def test_second_pending_proposal_for_the_same_box_is_refused(self):
        self.assertEqual(self.create().status_code, 201)
        response = self.create(data=self.payload(destination=self.org_c))
        self.assertEqual((response.status_code, response.data["code"]), (409, "donation_already_pending"))
        preflight = self.preflight(destination=self.org_c)
        self.assertEqual(preflight.data["code"], "donation_already_pending")

    def test_new_proposal_allowed_after_cancellation(self):
        first = self.create()
        self.client_for(self.admin_a, self.org_a).post(
            cancel_url(first.data["proposal_id"]), {"expected_revision": 1}, format="json",
        )
        self.assertEqual(self.create(data=self.payload(destination=self.org_c)).status_code, 201)
        self.assertEqual(BoxDonationProposal.objects.filter(state="pending").count(), 1)

    def test_removed_or_demoted_admin_cannot_create_or_replay(self):
        payload = self.payload()
        self.assertEqual(self.create(data=payload).status_code, 201)
        membership = OrganizationMembership.objects.get(user=self.admin_a, organization=self.org_a)
        membership.role = Role.LAB_TECHNICIAN
        membership.save()
        replay = self.create(data=payload)
        self.assertEqual((replay.status_code, replay.data["code"]), (403, "donation_admin_required"))
        membership.role = Role.ADMIN
        membership.is_active = False
        membership.save()
        self.assertEqual(self.create(data=payload).status_code, 403)
        self.admin_a.is_active = False
        self.admin_a.save()
        self.assertIn(self.create(data=self.payload(box=self.box_a_other)).status_code, (401, 403))

    def test_partial_audit_failure_rolls_back_enrollment_and_proposal(self):
        original = AuditLog.objects.create

        def fail_on_proposal_audit(**kwargs):
            if kwargs.get("object_type") == "box_donation_proposal":
                raise RuntimeError("audit storage unavailable")
            return original(**kwargs)

        payload = self.payload()
        with mock.patch.object(AuditLog.objects, "create", side_effect=fail_on_proposal_audit):
            with self.assertRaises(RuntimeError):
                box_donation.create_proposal(actor=self.admin_a, organization=self.org_a, data=payload)
        self.assertEqual(GlobalBoxIdentity.objects.count(), 0)
        self.assertEqual(BoxOwnershipEpisode.objects.count(), 0)
        self.assertEqual(BoxDonationProposal.objects.count(), 0)
        self.assertFalse(AuditLog.objects.filter(object_type="global_box_identity").exists())
        # The same key then succeeds once the audit works again.
        _proposal, created = box_donation.create_proposal(
            actor=self.admin_a, organization=self.org_a, data=payload,
        )
        self.assertTrue(created)

    def test_enrollment_audit_failure_rolls_back_everything(self):
        original = AuditLog.objects.create

        def fail_on_enrollment(**kwargs):
            if kwargs.get("object_type") == "global_box_identity":
                raise RuntimeError("audit storage unavailable")
            return original(**kwargs)

        with mock.patch.object(AuditLog.objects, "create", side_effect=fail_on_enrollment):
            with self.assertRaises(RuntimeError):
                box_donation.create_proposal(actor=self.admin_a, organization=self.org_a, data=self.payload())
        self.assertEqual(GlobalBoxIdentity.objects.count(), 0)
        self.assertEqual(BoxDonationProposal.objects.count(), 0)


class AdminEnrollmentTransactionTests(SimpleTestCase):
    def test_enrollment_outside_a_transaction_is_refused_before_any_query(self):
        from .box_ownership import enroll_origin_box_as_admin

        with self.assertRaisesMessage(RuntimeError, "inside the caller's transaction"):
            enroll_origin_box_as_admin(actor=None, organization=None, box=None)


class AdminEnrollmentTests(DonationTestCase):
    def test_direct_admin_enrollment_audits_once_and_is_idempotent(self):
        from .box_ownership import enroll_origin_box_as_admin

        with transaction.atomic():
            first, created = enroll_origin_box_as_admin(actor=self.admin_a, organization=self.org_a, box=self.box_a)
        with transaction.atomic():
            again, created_again = enroll_origin_box_as_admin(actor=self.admin_a, organization=self.org_a, box=self.box_a)
        self.assertEqual((created, created_again), (True, False))
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(AuditLog.objects.filter(object_type="global_box_identity").count(), 1)

    def test_direct_admin_enrollment_requires_source_admin(self):
        from rest_framework.exceptions import PermissionDenied

        from .box_ownership import enroll_origin_box_as_admin

        # The "outside a transaction" refusal cannot be observed under TestCase,
        # which always holds an outer transaction.
        for actor in (self.tech_a, self.admin_b, self.root):
            with self.assertRaises(PermissionDenied), transaction.atomic():
                enroll_origin_box_as_admin(actor=actor, organization=self.org_a, box=self.box_a)
        self.assertEqual(GlobalBoxIdentity.objects.count(), 0)

    def test_direct_admin_enrollment_audit_failure_leaves_no_identity(self):
        from .box_ownership import enroll_origin_box_as_admin

        with mock.patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError), transaction.atomic():
                enroll_origin_box_as_admin(actor=self.admin_a, organization=self.org_a, box=self.box_a)
        self.assertEqual(GlobalBoxIdentity.objects.count(), 0)
        self.assertEqual(BoxOwnershipEpisode.objects.count(), 0)


class IsolationTests(DonationTestCase):
    def setUp(self):
        self.proposal_id = self.create().data["proposal_id"]

    def test_lists_are_institution_filtered(self):
        outgoing = self.client_for(self.admin_a, self.org_a).get(OUTGOING)
        self.assertEqual([p["proposal_id"] for p in outgoing.data["results"]], [self.proposal_id])
        self.assertEqual(self.client_for(self.admin_a, self.org_a).get(INCOMING).data["results"], [])
        incoming = self.client_for(self.admin_b, self.org_b).get(INCOMING)
        self.assertEqual([p["proposal_id"] for p in incoming.data["results"]], [self.proposal_id])
        self.assertEqual(self.client_for(self.admin_b, self.org_b).get(OUTGOING).data["results"], [])
        self.assertEqual(self.client_for(self.admin_c, self.org_c).get(INCOMING).data["results"], [])
        self.assertEqual(self.client_for(self.admin_c, self.org_c).get(OUTGOING).data["results"], [])

    def test_non_admins_are_refused_everywhere(self):
        for user, organization in ((self.tech_a, self.org_a), (self.viewer_a, self.org_a), (self.tech_b, self.org_b)):
            client = self.client_for(user, organization)
            self.assertEqual(client.get(OUTGOING).status_code, 403)
            self.assertEqual(client.get(INCOMING).status_code, 403)
            self.assertEqual(client.get(detail_url(self.proposal_id)).status_code, 403)
            self.assertEqual(client.post(cancel_url(self.proposal_id), {"expected_revision": 1}, format="json").status_code, 403)
        root = self.client_for(self.root, self.org_a)
        self.assertEqual(root.get(OUTGOING).status_code, 403)

    def test_destination_detail_is_a_summary_without_history_or_accounts(self):
        response = self.client_for(self.admin_b, self.org_b).get(detail_url(self.proposal_id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["box"]["global_code"], "DON-AAA-1.001")
        text = str(response.data)
        for hidden in ("history_verification", "sha256", "created_by", "admin-a@example.test", "observations",
                       "polyp_count", "'id'"):
            self.assertNotIn(hidden, text)
        self.assertNotIn("id", response.data["box"])

    def test_source_detail_reports_verification_and_staleness(self):
        client = self.client_for(self.admin_a, self.org_a)
        response = client.get(detail_url(self.proposal_id))
        self.assertTrue(response.data["history_verification"]["current"])
        self.assertEqual(response.data["history_verification"]["verified_by"]["email"], "admin-a@example.test")
        # The source keeps operational control: measuring is not blocked.
        BiologicalMeasurement.objects.create(box=self.box_a, measured_on=date(2026, 3, 16), polyp_count=7)
        self.assertFalse(client.get(detail_url(self.proposal_id)).data["history_verification"]["current"])

    def test_third_institution_and_context_switching_see_nothing(self):
        self.assertEqual(self.client_for(self.admin_c, self.org_c).get(detail_url(self.proposal_id)).status_code, 404)
        dual_c = self.client_for(self.dual, self.org_c)
        self.assertEqual(dual_c.get(detail_url(self.proposal_id)).status_code, 404)
        self.assertEqual(dual_c.get(OUTGOING).data["results"], [])
        cancel = dual_c.post(cancel_url(self.proposal_id), {"expected_revision": 1}, format="json")
        self.assertEqual(cancel.status_code, 404)
        create = self.create(user=self.dual, organization=self.org_c, data=self.payload(box=self.box_a_other))
        self.assertEqual((create.status_code, create.data["code"]), (404, "box_not_found"))
        dual_a = self.client_for(self.dual, self.org_a)
        self.assertEqual(dual_a.get(detail_url(self.proposal_id)).status_code, 200)
        # A context the account cannot access is refused, not ignored.
        self.assertEqual(self.client_for(self.admin_c, self.org_a).get(OUTGOING).status_code, 403)

    def test_destination_cannot_cancel(self):
        response = self.client_for(self.admin_b, self.org_b).post(
            cancel_url(self.proposal_id), {"expected_revision": 1}, format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(BoxDonationProposal.objects.get().state, "pending")


class CancellationTests(DonationTestCase):
    def setUp(self):
        self.proposal_id = self.create().data["proposal_id"]
        self.client_a = self.client_for(self.admin_a, self.org_a)

    def test_cancellation_is_audited_and_changes_nothing_else(self):
        episodes = list(BoxOwnershipEpisode.objects.values())
        response = self.client_a.post(cancel_url(self.proposal_id), {"expected_revision": 1}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual((response.data["state"], response.data["revision"]), ("cancelled", 2))
        proposal = BoxDonationProposal.objects.get()
        self.assertEqual(proposal.cancelled_by, self.admin_a)
        self.assertIsNotNone(proposal.cancelled_at)
        self.assertEqual(list(BoxOwnershipEpisode.objects.values()), episodes)
        audit = AuditLog.objects.get(object_type="box_donation_proposal", action=AuditLog.Action.ARCHIVE)
        self.assertEqual((audit.organization, audit.user), (self.org_a, self.admin_a))
        self.assertIsNone(self.client_a.get(detail_url(self.proposal_id)).data["history_verification"]["current"])

    def test_stale_and_repeated_cancellations_are_refused(self):
        stale = self.client_a.post(cancel_url(self.proposal_id), {"expected_revision": 2}, format="json")
        self.assertEqual((stale.status_code, stale.data["code"]), (409, "stale_proposal_revision"))
        self.assertEqual(self.client_a.post(cancel_url(self.proposal_id), {"expected_revision": 1}, format="json").status_code, 200)
        again = self.client_a.post(cancel_url(self.proposal_id), {"expected_revision": 1}, format="json")
        self.assertEqual((again.status_code, again.data["code"]), (409, "proposal_not_pending"))
        self.assertEqual(AuditLog.objects.filter(action=AuditLog.Action.ARCHIVE).count(), 1)

    def test_another_source_admin_can_cancel(self):
        response = self.client_for(self.admin_a2, self.org_a).post(
            cancel_url(self.proposal_id), {"expected_revision": 1}, format="json",
        )
        self.assertEqual(response.status_code, 200)

    def test_cancellation_audit_failure_keeps_the_proposal_pending(self):
        with mock.patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                box_donation.cancel_proposal(
                    actor=self.admin_a, organization=self.org_a, proposal_id=self.proposal_id,
                    data={"expected_revision": 1},
                )
        self.assertEqual(BoxDonationProposal.objects.get().state, "pending")


class ModelGuardTests(DonationTestCase):
    def setUp(self):
        self.create()
        self.proposal = BoxDonationProposal.objects.get()

    def test_snapshots_are_immutable_and_rows_protected(self):
        self.proposal.source_box_code = "FORGED"
        with self.assertRaises(ModelValidationError):
            self.proposal.save()
        with self.assertRaises(ModelValidationError):
            self.proposal.save(update_fields=["destination_institution_name"])
        with self.assertRaises(ModelValidationError):
            BoxDonationProposal.objects.filter(pk=self.proposal.pk).update(state="cancelled")
        with self.assertRaises(ModelValidationError):
            BoxDonationProposal.objects.all().delete()
        with self.assertRaises(ModelValidationError):
            self.proposal.delete()

    def persisted(self):
        return BoxDonationProposal.objects.filter(pk=self.proposal.pk).values().get()

    def test_bulk_update_is_refused_by_the_protected_queryset(self):
        # Django 5.2 bulk_update() ends in self.using(db).filter(...).update(),
        # which keeps the protected QuerySet class: no separate override needed.
        before = self.persisted()
        forged = {
            "state": "cancelled", "revision": 9, "history_sha256": "f" * 64,
            "destination_institution_name": "Forged", "source_box_code": "FORGED",
            "destination_organization_id": self.org_c.pk,
        }
        for field, value in forged.items():
            proposal = BoxDonationProposal.objects.get(pk=self.proposal.pk)
            setattr(proposal, field, value)
            for manager in (
                BoxDonationProposal.objects,
                BoxDonationProposal.objects.all(),
                self.box_a.donation_proposals,
                self.org_a.outgoing_box_donation_proposals,
                self.org_b.incoming_box_donation_proposals,
            ):
                with self.subTest(field=field, manager=type(manager).__name__):
                    # bulk_update raises inside its own atomic(savepoint=False):
                    # the enclosing transaction is then unusable, so isolate it.
                    with self.assertRaisesMessage(ModelValidationError, "validated saves"), transaction.atomic():
                        manager.bulk_update([proposal], [field.removesuffix("_id")])
        self.assertEqual(self.persisted(), before)

    def test_related_manager_update_and_delete_are_refused(self):
        before = self.persisted()
        with self.assertRaises(ModelValidationError):
            self.box_a.donation_proposals.update(state="cancelled")
        with self.assertRaises(ModelValidationError):
            self.org_b.incoming_box_donation_proposals.all().delete()
        self.assertEqual(self.persisted(), before)

    def test_guards_leave_authorized_cancellation_working(self):
        response = self.client_for(self.admin_a, self.org_a).post(
            cancel_url(self.proposal.proposal_id), {"expected_revision": 1}, format="json",
        )
        self.assertEqual((response.status_code, response.data["state"]), (200, "cancelled"))

    def test_database_refuses_two_pending_proposals_for_one_identity(self):
        fields = {
            field.name: getattr(self.proposal, field.name)
            for field in BoxDonationProposal._meta.concrete_fields if field.name not in ("id", "proposal_id")
        }
        fields["request_id"] = uuid4()
        with self.assertRaises(IntegrityError), transaction.atomic():
            BoxDonationProposal(**fields).save()

    def test_database_refuses_same_source_and_destination(self):
        fields = {
            field.name: getattr(self.proposal, field.name)
            for field in BoxDonationProposal._meta.concrete_fields if field.name not in ("id", "proposal_id")
        }
        fields.update(request_id=uuid4(), state="pending", destination_organization=self.org_a)
        self.proposal.state = "cancelled"
        self.proposal.revision = 2
        self.proposal.cancelled_by = self.admin_a
        self.proposal.cancelled_at = timezone.now()
        self.proposal.save(update_fields=["state", "revision", "cancelled_by", "cancelled_at"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            BoxDonationProposal(**fields).save()

    def test_box_with_proposal_keeps_merge_protection(self):
        from .box_ownership import boxes_with_ownership_history

        self.assertEqual(boxes_with_ownership_history([self.box_a.pk]), {self.box_a.pk})
