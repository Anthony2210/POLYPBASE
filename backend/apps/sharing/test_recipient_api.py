"""Recipient side: invitation identity, exact Box scope, read-only access."""

from datetime import timedelta
import json
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.measurements.models import BiologicalMeasurement

from . import services
from .models import BoxReaderShare
from .sharing_testing import (
    PASSWORD,
    SHARING_SETTINGS,
    accept,
    build_world,
    client_for,
    create_accepted_share,
    create_share,
)

ACCEPT = "/api/shared-boxes/invitations/accept/"
INSPECT = "/api/shared-boxes/invitations/inspect/"
READER = "/api/shared-boxes/"


def later(**delta):
    return patch.object(services, "sharing_now", return_value=timezone.now() + timedelta(**delta))


@override_settings(**SHARING_SETTINGS)
class InvitationAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.w = build_world()

    def setUp(self):
        self.share, self.token = create_share(self.w)

    def stored(self):
        return BoxReaderShare.objects.get(pk=self.share["id"])

    def test_intended_recipient_accepts_and_is_bound(self):
        password_before = type(self.w.recipient).objects.get(pk=self.w.recipient.pk).password
        memberships_before = list(OrganizationMembership.objects.order_by("pk").values())
        response = accept(self.w.recipient, self.token)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()["share"]
        share = self.stored()
        self.assertEqual(body["id"], str(share.public_id))
        self.assertEqual(body["state"], "active")
        self.assertTrue(body["read_only"])
        self.assertEqual(body["source_institution"], {"name": "Synthetic source lab"})
        self.assertEqual(share.recipient_user_id, self.w.recipient.pk)
        self.assertIsNotNone(share.accepted_at)
        self.assertIsNone(share.invitation_token_hash)
        # No membership, no password change.
        self.assertEqual(list(OrganizationMembership.objects.order_by("pk").values()), memberships_before)
        account = type(self.w.recipient).objects.get(pk=self.w.recipient.pk)
        self.assertEqual(account.password, password_before)
        self.assertTrue(account.check_password(PASSWORD))
        audit = AuditLog.objects.filter(object_type="box_reader_share").order_by("-pk").first()
        self.assertEqual((audit.user_id, audit.organization_id), (self.w.recipient.pk, self.w.source.pk))
        self.assertEqual(audit.description, "Box reader share invitation accepted")
        self.assertNotIn(self.token, json.dumps(audit.metadata))

    def test_anonymous_actor_gets_nothing(self):
        anonymous = client_for()
        for method, path, body in (
            ("post", ACCEPT, {"token": self.token}),
            ("post", INSPECT, {"token": self.token}),
            ("get", READER, None),
            ("get", f"{READER}{self.stored().public_id}/", None),
        ):
            with self.subTest(path=path):
                self.assertEqual(getattr(anonymous, method)(path, body, format="json").status_code, 403)
        self.assertIsNone(self.stored().recipient_user_id)

    def test_another_signed_in_account_cannot_accept_or_inspect(self):
        for user in (self.w.loner, self.w.third_admin, self.w.admin, self.w.superuser):
            with self.subTest(user=user.username):
                for path in (ACCEPT, INSPECT):
                    response = client_for(user).post(path, {"token": self.token}, format="json")
                    self.assertEqual((response.status_code, response.json()["code"]), (403, "invitation_recipient_mismatch"))
                    self.assertNotIn("recipient@example.test", response.content.decode())
        share = self.stored()
        self.assertIsNone(share.recipient_user_id)
        # The mismatch consumed nothing: the intended recipient still accepts.
        self.assertEqual(accept(self.w.recipient, self.token).status_code, 200)

    def test_account_email_without_the_mailed_secret_proves_nothing(self):
        guesses = ["", "x", "A" * 43, self.token[:-1], self.token + "A", self.stored().invitation_token_hash,
                   str(self.stored().public_id)]
        for guess in guesses:
            with self.subTest(guess=guess[:8]):
                response = accept(self.w.recipient, guess)
                self.assertIn(response.status_code, (400, 404))
                self.assertIn(response.json()["code"], ("invitation_unavailable", "invalid_request"))
        self.assertIsNone(self.stored().recipient_user_id)
        self.assertEqual(client_for(self.w.recipient).get(READER).json()["shares"], [])

    def test_possessing_the_link_grants_no_read_and_get_consumes_nothing(self):
        share = self.stored()
        recipient = client_for(self.w.recipient)
        # GET never accepts: link previews and scanners cannot activate a grant.
        for path in (ACCEPT, INSPECT, f"{ACCEPT}?token={self.token}"):
            self.assertEqual(recipient.get(path).status_code, 405)
        # Before acceptance even the intended account reads nothing.
        response = recipient.get(f"{READER}{share.public_id}/")
        self.assertEqual((response.status_code, response.json()["code"]), (404, "share_not_found"))
        self.assertEqual(recipient.get(f"{READER}{share.public_id}/", {"token": self.token}).status_code, 404)
        self.assertIsNone(self.stored().accepted_at)
        self.assertIsNotNone(self.stored().invitation_token_hash)

    def test_inspect_describes_without_consuming(self):
        response = client_for(self.w.recipient).post(INSPECT, {"token": self.token}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        invitation = response.json()["invitation"]
        self.assertEqual(set(invitation), {"mode", "read_only", "invitation_expires_at", "access_expires_at", "source_institution"})
        self.assertEqual(invitation["source_institution"], {"name": "Synthetic source lab"})
        self.assertNotIn("SHR-AAA", response.content.decode())
        self.assertIsNone(self.stored().accepted_at)

    def test_acceptance_requires_csrf(self):
        client = client_for(self.w.recipient, enforce_csrf=True)
        self.assertEqual(client.post(ACCEPT, {"token": self.token}, format="json").status_code, 403)
        self.assertIsNone(self.stored().accepted_at)

    def test_secret_is_single_use(self):
        self.assertEqual(accept(self.w.recipient, self.token).status_code, 200)
        replay = accept(self.w.recipient, self.token)
        self.assertEqual((replay.status_code, replay.json()["code"]), (404, "invitation_unavailable"))
        self.assertEqual(accept(self.w.loner, self.token).json()["code"], "invitation_unavailable")
        self.assertEqual(
            AuditLog.objects.filter(description="Box reader share invitation accepted").count(), 1,
        )

    def test_expired_invitation_and_expired_share_cannot_be_accepted(self):
        with later(days=3):
            response = accept(self.w.recipient, self.token)
            self.assertEqual((response.status_code, response.json()["code"]), (410, "invitation_expired"))
        BoxReaderShare.objects.filter(pk=self.share["id"]).update(
            invitation_expires_at=timezone.now() + timedelta(days=40),
        )
        with later(days=31):
            self.assertEqual(accept(self.w.recipient, self.token).json()["code"], "share_expired")
        self.assertIsNone(self.stored().recipient_user_id)

    def test_inactive_source_institution_blocks_acceptance(self):
        type(self.w.source).objects.filter(pk=self.w.source.pk).update(is_active=False)
        self.assertEqual(accept(self.w.recipient, self.token).json()["code"], "invitation_unavailable")

    def test_failed_acceptance_audit_does_not_bind_the_account(self):
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                accept(self.w.recipient, self.token)
        share = self.stored()
        self.assertIsNone(share.recipient_user_id)
        self.assertIsNotNone(share.invitation_token_hash)

    def test_invited_mailbox_without_an_account_cannot_be_claimed_by_others(self):
        _share, token = create_share(self.w, recipient_email="nobody@example.test")
        for user in (self.w.recipient, self.w.loner):
            self.assertEqual(accept(user, token).json()["code"], "invitation_recipient_mismatch")


@override_settings(**SHARING_SETTINGS)
class ReaderScopeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.w = build_world()

    def setUp(self):
        # The reader has no membership anywhere.
        self.share, self.public_id = create_accepted_share(self.w, recipient_email="loner@example.test", user=self.w.loner)
        self.reader = client_for(self.w.loner)
        self.url = f"{READER}{self.public_id}/"

    def test_account_without_membership_reads_exactly_its_box(self):
        response = self.reader.get(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["share"]["id"], self.public_id)
        self.assertEqual(body["share"]["mode"], "live")
        self.assertTrue(body["share"]["read_only"])
        self.assertEqual(body["share"]["source_institution"], {"name": "Synthetic source lab"})
        self.assertEqual(body["share"]["policy_revision"], 1)
        self.assertEqual(body["content"]["kind"], "live_projection")
        self.assertEqual(body["content"]["consistency"], "non_atomic")
        self.assertEqual(len(body["snapshot"]["observations"]), 1)
        text = response.content.decode()
        for leaked in ('"value":999', '"value":998', '"value":555', "SHR-AAA-1.777", "THD-AAA",
                       "private synthetic note"):
            self.assertNotIn(leaked, text)
        self.assertFalse(OrganizationMembership.objects.filter(user=self.w.loner).exists())

    def test_client_supplied_identifiers_cannot_change_the_scope(self):
        w = self.w
        expected = self.reader.get(self.url).json()["snapshot"]
        attempts = [
            ({"box_id": w.sibling.pk}, {}),
            ({"box": w.sibling.pk, "organization": w.third.pk, "organization_id": w.third.pk}, {}),
            ({"selectors": "source.box_code", "disclosed": "identity.strain_code", "mode": "frozen"}, {}),
            ({"portable_id": str(w.third.portable_id), "public_id": str(uuid.uuid4())}, {}),
            ({}, {"HTTP_X_ORGANIZATION_ID": str(w.source.pk)}),
            ({}, {"HTTP_X_ORGANIZATION_ID": str(w.third.pk)}),
            ({}, {"HTTP_X_ORGANIZATION_ID": "not-a-number"}),
        ]
        for params, headers in attempts:
            with self.subTest(params=params, headers=headers):
                response = self.reader.get(self.url, params, **headers)
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.json()["snapshot"], expected)

    def test_other_references_are_not_found(self):
        w = self.w
        _other, other_public_id = create_accepted_share(w, box_id=w.sibling.pk)
        references = [
            other_public_id,  # another account's grant, same source institution
            str(uuid.uuid4()),
            str(w.source.portable_id),
        ]
        for reference in references:
            with self.subTest(reference=reference):
                response = self.reader.get(f"{READER}{reference}/")
                self.assertEqual((response.status_code, response.json()["code"]), (404, "share_not_found"))
        for path in (f"{READER}{w.box.pk}/", f"{READER}{self.share['id']}/", f"{READER}SHR-AAA-1.001/"):
            self.assertEqual(self.reader.get(path).status_code, 404)
        # The source Admin is not the recipient either.
        self.assertEqual(client_for(w.admin, w.source).get(self.url).status_code, 404)
        self.assertEqual(client_for(w.superuser).get(self.url).status_code, 404)

    def test_grant_opens_no_institutional_api(self):
        w = self.w
        box, sibling = w.box.pk, w.sibling.pk
        measurement = w.zero.pk
        for headers in ({}, {"HTTP_X_ORGANIZATION_ID": str(w.source.pk)}):
            client = client_for(w.loner)
            requests = [
                ("get", "/api/boxes/", None),
                ("get", f"/api/boxes/{box}/", None),
                ("get", f"/api/boxes/{sibling}/", None),
                ("get", f"/api/boxes/{box}/history/", None),
                ("get", f"/api/boxes/{box}/lineage/", None),
                ("get", f"/api/boxes/{box}/measurements/", None),
                ("get", f"/api/boxes/{box}/temperatures/", None),
                ("get", f"/api/exports/boxes/{box}/trend/", None),
                ("get", "/api/exports/measurements.csv", None),
                ("get", "/api/exports/eligible-boxes/", None),
                ("get", "/api/admin/box-shares/", None),
                ("get", "/api/admin/box-inventory/", None),
                ("post", f"/api/boxes/{box}/measurements/", {"measured_on": "2026-05-04", "polyp_count": 1, "ephyrae_count": 1}),
                ("patch", f"/api/boxes/{box}/measurements/{measurement}/", {"polyp_count": 7}),
                ("delete", f"/api/boxes/{box}/measurements/{measurement}/", None),
                ("patch", f"/api/boxes/{box}/", {"notes": "tampered"}),
                ("post", f"/api/boxes/{box}/deactivate/", {}),
                ("post", f"/api/boxes/{box}/activate/", {}),
                ("post", f"/api/boxes/{box}/move/", {"thermal_zone_id": 1}),
                ("post", f"/api/boxes/{box}/subcultures/", {}),
                ("post", f"/api/boxes/{box}/qualify/", {}),
                ("post", "/api/admin/box-shares/", {"box_id": box}),
                ("post", f"/api/admin/box-shares/{self.share['id']}/revoke/", {}),
            ]
            for method, path, body in requests:
                with self.subTest(path=path, method=method, headers=bool(headers)):
                    response = getattr(client, method)(path, body, format="json", **headers)
                    # Existing institutional behaviour for an account without the
                    # source membership: refusal, or an empty own-scope answer.
                    self.assertIn(
                        response.status_code, (200, 400, 403, 404, 405) if method == "get" else (400, 403, 404, 405),
                        f"{method} {path}: {response.status_code}",
                    )
                    text = response.content.decode()
                    for leaked in ("Synthetic source", "polyp_count", "global_code", str(self.public_id)):
                        self.assertNotIn(leaked, text, f"{method} {path}")
                    self.assertNotIn("SHR-AAA", text)
                    self.assertNotIn("private synthetic note", text)
        self.assertEqual(BiologicalMeasurement.objects.get(pk=measurement).polyp_count, 0)
        self.assertEqual(BiologicalMeasurement.objects.count(), 3)
        self.assertEqual(Box.objects.get(pk=box).status, Box.Status.ACTIVE)
        self.assertIsNone(BoxReaderShare.objects.get(pk=self.share["id"]).revoked_at)
        # The profile does not gain the source institution.
        profile = client_for(w.loner).get("/api/auth/session/")
        self.assertNotIn("Synthetic source lab", profile.content.decode())

    def test_reader_endpoints_expose_no_mutation(self):
        for method in ("post", "put", "patch", "delete"):
            with self.subTest(method=method):
                self.assertEqual(getattr(self.reader, method)(self.url, {}, format="json").status_code, 405)
                self.assertEqual(getattr(self.reader, method)(READER, {}, format="json").status_code, 405)

    def test_recipient_with_its_own_institution_gains_nothing_there(self):
        w = self.w
        _share, public_id = create_accepted_share(w)
        recipient = client_for(w.recipient, w.recipient_org)
        self.assertEqual(recipient.get(f"{READER}{public_id}/").status_code, 200)
        self.assertIn(recipient.get(f"/api/boxes/{w.box.pk}/").status_code, (403, 404))
        self.assertEqual(recipient.get("/api/boxes/").json().get("count", 0), 0)
        forged = client_for(w.recipient, w.source).get("/api/boxes/")
        self.assertEqual(forged.status_code, 403)

    def test_list_shows_only_own_grants(self):
        w = self.w
        create_accepted_share(w, box_id=w.sibling.pk)
        shares = self.reader.get(READER).json()["shares"]
        self.assertEqual([item["id"] for item in shares], [self.public_id])
        self.assertEqual(shares[0]["source_institution"], {"name": "Synthetic source lab"})
        self.assertNotIn("box", shares[0])
        self.assertEqual(client_for(w.viewer, w.source).get(READER).json()["shares"], [])

    def test_revocation_ends_access_for_an_open_session(self):
        self.assertEqual(self.reader.get(self.url).status_code, 200)
        client_for(self.w.admin, self.w.source).post(f"/api/admin/box-shares/{self.share['id']}/revoke/")
        response = self.reader.get(self.url)
        self.assertEqual((response.status_code, response.json()["code"]), (410, "share_revoked"))
        self.assertNotIn("snapshot", response.json())
        self.assertEqual(self.reader.get(READER).json()["shares"][0]["state"], "revoked")

    def test_expiry_ends_access_until_an_admin_extends_it(self):
        with later(days=31):
            response = self.reader.get(self.url)
            self.assertEqual((response.status_code, response.json()["code"]), (410, "share_expired"))
            self.assertEqual(self.reader.get(READER).json()["shares"][0]["state"], "expired")

    def test_inactive_source_institution_makes_the_share_unavailable(self):
        type(self.w.source).objects.filter(pk=self.w.source.pk).update(is_active=False)
        response = self.reader.get(self.url)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "share_source_unavailable"))
        self.assertEqual(self.reader.get(READER).json()["shares"][0]["state"], "unavailable")

    def test_source_admin_losing_authority_does_not_change_the_grant(self):
        OrganizationMembership.objects.filter(user=self.w.admin).update(is_active=False)
        self.assertEqual(self.reader.get(self.url).status_code, 200)

    def test_deactivated_or_changed_recipient_account_reads_nothing(self):
        User = type(self.w.loner)
        User.objects.filter(pk=self.w.loner.pk).update(email="moved@example.test")
        self.assertEqual(self.reader.get(self.url).status_code, 404)
        self.assertEqual(self.reader.get(READER).json()["shares"], [])
        # Another account taking the invited address does not inherit the grant.
        taker = User.objects.create_user(username="share-taker", email="loner@example.test")
        self.assertEqual(client_for(taker).get(self.url).status_code, 404)
        taker.delete()
        User.objects.filter(pk=self.w.loner.pk).update(email="loner@example.test", is_active=False)
        self.assertEqual(self.reader.get(self.url).status_code, 403)

    def test_revocation_committed_during_a_read_prevents_delivery(self):
        original = services._live_content

        def revoke_then_project(share, now):
            content = original(share, now)
            BoxReaderShare.objects.filter(pk=share.pk).update(revoked_at=timezone.now())
            return content

        with patch.object(services, "_live_content", side_effect=revoke_then_project):
            response = self.reader.get(self.url)
        self.assertEqual((response.status_code, response.json()["code"]), (410, "share_revoked"))
        self.assertFalse(AuditLog.objects.filter(action="view", object_type="box_reader_share").exists())

    def test_policy_change_during_a_read_prevents_delivery_under_the_old_revision(self):
        original = services._live_content

        def reduce_then_project(share, now):
            content = original(share, now)
            BoxReaderShare.objects.filter(pk=share.pk).update(
                selectors=["source.institution_name"], policy_revision=2,
            )
            return content

        with patch.object(services, "_live_content", side_effect=reduce_then_project):
            response = self.reader.get(self.url)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "share_policy_changed"))
        self.assertNotIn("observations", response.content.decode())
        retry = self.reader.get(self.url).json()
        self.assertEqual(retry["share"]["policy_revision"], 2)
        self.assertNotIn("observations", retry["snapshot"])

    def test_delivery_is_audited_server_side_without_content(self):
        before = AuditLog.objects.filter(action="view").count()
        body = self.reader.get(self.url).json()
        audit = AuditLog.objects.filter(action="view", object_type="box_reader_share").get()
        self.assertEqual(AuditLog.objects.filter(action="view").count(), before + 1)
        self.assertEqual((audit.organization_id, audit.user_id), (self.w.source.pk, self.w.loner.pk))
        self.assertEqual(audit.object_id, self.public_id)
        self.assertEqual(audit.metadata["policy_revision"], 1)
        self.assertEqual(audit.metadata["content_kind"], "live_projection")
        self.assertEqual(len(audit.metadata["content_sha256"]), 64)
        self.assertNotIn("observations", json.dumps(audit.metadata))
        self.assertNotIn("snapshot", audit.metadata)
        self.assertTrue(body["snapshot"])
        # Refused reads record no delivery.
        client_for(self.w.third_admin).get(self.url)
        self.assertEqual(AuditLog.objects.filter(action="view", object_type="box_reader_share").count(), 1)

    def test_delivery_is_refused_when_it_cannot_be_audited(self):
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.reader.get(self.url)

    def test_reader_responses_are_never_cacheable(self):
        responses = [
            self.reader.get(self.url),
            self.reader.get(READER),
            self.reader.get(f"{READER}{uuid.uuid4()}/"),
            client_for().get(self.url),
        ]
        for response in responses:
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertIn("Cookie", response["Vary"])

    def test_switching_account_in_the_same_browser_loses_access(self):
        client = client_for(self.w.loner)
        self.assertEqual(client.get(self.url).status_code, 200)
        client.force_login(self.w.third_admin)
        self.assertEqual(client.get(self.url).status_code, 404)
        client.logout()
        self.assertEqual(client.get(self.url).status_code, 403)


@override_settings(**SHARING_SETTINGS)
class StoredStateConstraintTests(TestCase):
    """Database-level guarantees that hold even for a technical write."""

    @classmethod
    def setUpTestData(cls):
        cls.w = build_world()

    def assert_rejected(self, **changes):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError), transaction.atomic():
            BoxReaderShare.objects.filter(pk=self.share["id"]).update(**changes)

    def test_revoked_or_accepted_share_cannot_hold_an_acceptable_secret(self):
        self.share, token = create_share(self.w)
        accept(self.w.recipient, token)
        self.assert_rejected(invitation_token_hash="a" * 64)
        client_for(self.w.admin, self.w.source).post(f"/api/admin/box-shares/{self.share['id']}/revoke/")
        self.assert_rejected(invitation_token_hash="b" * 64)
        self.assert_rejected(invitation_token_hash="c" * 64, accepted_at=None)

    def test_account_cannot_be_bound_without_acceptance_evidence(self):
        self.share, _token = create_share(self.w)
        self.assert_rejected(recipient_user=self.w.loner)

    def test_second_open_share_for_the_same_box_and_mailbox_is_rejected(self):
        self.share, _token = create_share(self.w)
        other, _token = create_share(self.w, box_id=self.w.sibling.pk)
        self.share = other
        self.assert_rejected(box=self.w.box)

    def test_source_and_box_cannot_be_deleted_under_a_share(self):
        from django.db.models import ProtectedError

        self.share, _token = create_share(self.w)
        with self.assertRaises(ProtectedError):
            BoxReaderShare.objects.get(pk=self.share["id"]).box.delete()
