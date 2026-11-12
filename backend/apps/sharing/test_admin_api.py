"""Source Admin management of Box reader shares: authority, scope, audit."""

from datetime import timedelta
import json
import smtplib
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.measurements.models import BiologicalMeasurement

from . import services
from .models import BoxReaderShare, BoxReaderShareCapture
from .sharing_testing import (
    BASE_SELECTORS,
    OBSERVATION_SELECTORS,
    SHARING_SETTINGS,
    accept,
    build_world,
    client_for,
    create_payload,
    create_share,
    in_days,
    last_token,
)

URL = "/api/admin/box-shares/"


@override_settings(**SHARING_SETTINGS)
class SourceAdminAuthorityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.w = build_world()

    def post(self, user, organization, **overrides):
        return client_for(user, organization).post(URL, create_payload(self.w, **overrides), format="json")

    def assert_nothing_created(self):
        self.assertEqual(BoxReaderShare.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(AuditLog.objects.filter(object_type="box_reader_share").exists())

    def test_active_source_admin_creates_a_share(self):
        response = self.post(self.w.admin, self.w.source)
        self.assertEqual(response.status_code, 201, response.content)
        data = response.json()["share"]
        share = BoxReaderShare.objects.get()
        self.assertEqual((share.organization_id, share.box_id), (self.w.source.pk, self.w.box.pk))
        self.assertEqual(share.recipient_email, "recipient@example.test")
        self.assertIsNone(share.recipient_user_id)
        self.assertEqual(data["state"], "pending")
        self.assertEqual(data["policy_revision"], 1)
        self.assertEqual(data["selectors"], sorted(OBSERVATION_SELECTORS))
        self.assertEqual(data["box"], {"id": self.w.box.pk, "global_code": "SHR-AAA-1.001"})
        self.assertEqual(mail.outbox[-1].to, ["recipient@example.test"])
        self.assertIn("Synthetic source lab", mail.outbox[-1].body)

    def test_roles_without_admin_authority_are_refused(self):
        w = self.w
        cases = {
            "technician": (w.technician, w.source),
            "viewer": (w.viewer, w.source),
            # A superuser can select any institution but is not its Admin.
            "superuser": (w.superuser, w.source),
        }
        for label, (user, organization) in cases.items():
            with self.subTest(label):
                response = self.post(user, organization)
                self.assertEqual(response.status_code, 403, response.content)
                self.assertEqual(response.json()["code"], "sharing_admin_required")
        self.assert_nothing_created()

    def test_admin_of_another_institution_cannot_use_the_source_context(self):
        w = self.w
        # Forged header: the source institution is not accessible to this Admin.
        self.assertEqual(self.post(w.third_admin, w.source).status_code, 403)
        # Own context, foreign Box: indistinguishable from a missing Box.
        own_context = self.post(w.third_admin, w.third)
        missing = self.post(w.third_admin, w.third, box_id=Box.objects.order_by("-pk").first().pk + 1000)
        for response in (own_context, missing):
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json(), {"detail": "This Box does not exist.", "code": "box_not_found"})
        self.assert_nothing_created()

    def test_organization_context_must_be_explicit(self):
        response = client_for(self.w.admin).post(URL, create_payload(self.w), format="json")
        self.assertEqual(response.status_code, 403)
        self.assert_nothing_created()

    def test_inactive_membership_inactive_account_and_inactive_institution(self):
        w = self.w
        OrganizationMembership.objects.filter(user=w.admin).update(is_active=False)
        self.assertEqual(self.post(w.admin, w.source).status_code, 403)
        OrganizationMembership.objects.filter(user=w.admin).update(is_active=True)

        OrganizationMembership.objects.filter(user=w.admin).update(role="viewer")
        self.assertEqual(self.post(w.admin, w.source).json()["code"], "sharing_admin_required")
        OrganizationMembership.objects.filter(user=w.admin).update(role="admin")

        client = client_for(w.admin, w.source)
        type(w.admin).objects.filter(pk=w.admin.pk).update(is_active=False)
        self.assertEqual(client.post(URL, create_payload(w), format="json").status_code, 403)
        type(w.admin).objects.filter(pk=w.admin.pk).update(is_active=True)

        type(w.source).objects.filter(pk=w.source.pk).update(is_active=False)
        self.assertEqual(self.post(w.admin, w.source).status_code, 403)
        self.assert_nothing_created()

    def test_authority_is_rechecked_inside_the_mutation(self):
        w = self.w
        share, _token = create_share(w)
        client = client_for(w.admin, w.source)
        # The request-level context still resolves; the locked re-check refuses.
        with patch.object(services, "require_source_admin", side_effect=AssertionError("not the authority")):
            OrganizationMembership.objects.filter(user=w.admin).update(role="lab_technician")
            response = client.post(f"{URL}{share['id']}/revoke/")
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(BoxReaderShare.objects.get().revoked_at)

    def test_merged_box_cannot_be_shared(self):
        w = self.w
        retired = Box.objects.create(
            organization=w.source, strain=w.strain, global_code="SHR-AAA-1.003", box_number="3",
            status=Box.Status.INACTIVE, thermal_zone=None,
        )
        Box.objects.filter(pk=retired.pk).update(merged_into=w.box, previous_global_code="SHR-AAA-1.003")
        response = self.post(w.admin, w.source, box_id=retired.pk)
        self.assertEqual((response.status_code, response.json()["code"]), (404, "box_not_found"))

    def test_other_institution_admin_cannot_see_or_manage_a_share(self):
        w = self.w
        share, _token = create_share(w)
        client = client_for(w.third_admin, w.third)
        self.assertEqual(client.get(URL).json()["shares"], [])
        for method, path, body in (
            ("get", f"{URL}{share['id']}/", None),
            ("patch", f"{URL}{share['id']}/", {"expected_policy_revision": 1, "selectors": BASE_SELECTORS}),
            ("post", f"{URL}{share['id']}/revoke/", None),
            ("post", f"{URL}{share['id']}/reissue-invitation/", {"invitation_expires_at": in_days(1)}),
        ):
            with self.subTest(path=path, method=method):
                response = getattr(client, method)(path, body, format="json")
                self.assertEqual((response.status_code, response.json()["code"]), (404, "share_not_found"))
        stored = BoxReaderShare.objects.get()
        self.assertIsNone(stored.revoked_at)
        self.assertEqual(stored.invitation_generation, 1)

    def test_any_source_admin_manages_a_share_created_by_another(self):
        w = self.w
        share, _token = create_share(w)
        response = client_for(w.second_admin, w.source).post(f"{URL}{share['id']}/revoke/")
        self.assertEqual(response.json()["share"]["state"], "revoked")

    def test_csrf_is_enforced_on_management_mutations(self):
        w = self.w
        client = client_for(w.admin, w.source, enforce_csrf=True)
        self.assertEqual(client.post(URL, create_payload(w), format="json").status_code, 403)
        self.assert_nothing_created()


@override_settings(**SHARING_SETTINGS)
class ShareCreationRulesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.w = build_world()

    def post(self, **overrides):
        return client_for(self.w.admin, self.w.source).post(
            URL, create_payload(self.w, **overrides), format="json",
        )

    def test_unknown_malformed_and_incomplete_selections_are_rejected(self):
        cases = {
            "unknown": (["source.institution_name", "observation.notes"], "unknown_selector"),
            "orm path": (["source.institution_name", "box__organization__notes"], "unknown_selector"),
            "empty": ([], "empty_selection"),
            "duplicate": (["source.institution_name", "source.institution_name"], "duplicate_selector"),
            "value without time": (["source.institution_name", "observation.polyp_count"], "observation_time_required"),
            # The mandatory identifier is never added silently.
            "mandatory missing": (["source.box_code"], "required_selector_missing"),
        }
        for label, (selectors, code) in cases.items():
            with self.subTest(label):
                response = self.post(selectors=selectors)
                self.assertEqual(response.status_code, 400, response.content)
                body = response.json()
                self.assertEqual(body["code"], "invalid_disclosure")
                self.assertIn(code, [item["code"] for item in body["diagnostics"]])
        self.assertEqual(self.post(selectors="source.institution_name").json()["code"], "invalid_request")
        self.assertEqual(BoxReaderShare.objects.count(), 0)

    def test_options_name_the_mandatory_identifier(self):
        body = client_for(self.w.admin, self.w.source).get(f"{URL}options/").json()
        self.assertEqual(body["mandatory_selectors"], ["source.institution_name"])
        mandatory = [item["name"] for item in body["selectors"] if item["mandatory"]]
        self.assertEqual(mandatory, ["source.institution_name"])
        self.assertEqual({item["name"] for item in body["selectors"]}, set(services.select_disclosure.__globals__["SELECTORS"]))
        self.assertTrue(body["lifetime_policy"]["configured"])
        self.assertEqual(client_for(self.w.viewer, self.w.source).get(f"{URL}options/").status_code, 403)

    def test_lifetimes_are_explicit_and_bounded(self):
        for field in ("invitation_expires_at", "access_expires_at"):
            payload = create_payload(self.w)
            del payload[field]
            response = client_for(self.w.admin, self.w.source).post(URL, payload, format="json")
            self.assertEqual(response.json()["code"], "invalid_request")
        cases = {
            "invitation in the past": ({"invitation_expires_at": in_days(-1)}, "invitation_expiry_out_of_bounds"),
            "invitation beyond bound": ({"invitation_expires_at": in_days(8)}, "invitation_expiry_out_of_bounds"),
            "invitation after access": (
                {"invitation_expires_at": in_days(3), "access_expires_at": in_days(2)},
                "invitation_expiry_out_of_bounds",
            ),
            "access beyond bound": ({"access_expires_at": in_days(91)}, "access_expiry_out_of_bounds"),
            "access in the past": ({"access_expires_at": in_days(-1)}, "access_expiry_out_of_bounds"),
        }
        for label, (overrides, code) in cases.items():
            with self.subTest(label):
                response = self.post(**overrides)
                self.assertEqual((response.status_code, response.json()["code"]), (400, code))
        self.assertEqual(BoxReaderShare.objects.count(), 0)

    def test_sharing_is_unavailable_until_lifetimes_are_configured(self):
        for missing in ("BOX_SHARING_INVITATION_MAX_LIFETIME_SECONDS", "BOX_SHARING_ACCESS_MAX_LIFETIME_SECONDS"):
            with self.subTest(missing), override_settings(**{missing: None}):
                response = self.post()
                self.assertEqual((response.status_code, response.json()["code"]), (503, "sharing_policy_not_configured"))
                options = client_for(self.w.admin, self.w.source).get(f"{URL}options/").json()
                self.assertFalse(options["lifetime_policy"]["configured"])
        self.assertEqual(BoxReaderShare.objects.count(), 0)

    def test_default_settings_leave_sharing_unconfigured(self):
        from django.conf import settings
        from config import settings as base_settings

        self.assertIsNone(base_settings.BOX_SHARING_INVITATION_MAX_LIFETIME_SECONDS)
        self.assertIsNone(base_settings.BOX_SHARING_ACCESS_MAX_LIFETIME_SECONDS)
        self.assertTrue(settings.BOX_SHARING_ACCESS_MAX_LIFETIME_SECONDS)  # overridden here only

    @override_settings(EMAIL_DELIVERY_ENABLED=False)
    def test_email_delivery_is_required(self):
        response = self.post()
        self.assertEqual((response.status_code, response.json()["code"]), (503, "email_delivery_unavailable"))
        self.assertEqual(BoxReaderShare.objects.count(), 0)

    def test_smtp_failure_keeps_the_committed_share_and_reports_it(self):
        refused = smtplib.SMTPRecipientsRefused({"recipient@example.test": (550, b"no")})
        with patch.object(services, "send_mail", side_effect=refused):
            response = self.post()
        # Not reported as a rollback: the share exists and says delivery failed.
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["share"]["invitation"]["delivery"]["status"], "failed")
        share = BoxReaderShare.objects.get()
        self.assertEqual((share.delivery_status, share.recipient_user_id), ("failed", None))
        self.assertEqual(AuditLog.objects.filter(action="creation", object_type="box_reader_share").count(), 1)
        self.assertEqual(len(mail.outbox), 0)

    def test_unknown_smtp_outcome_is_uncertain_and_zero_accepted_is_failed(self):
        for label, kwargs, expected in (
            ("timeout", {"side_effect": TimeoutError("slow")}, "uncertain"),
            ("disconnect", {"side_effect": smtplib.SMTPServerDisconnected("eof")}, "uncertain"),
            ("data error", {"side_effect": smtplib.SMTPDataError(451, b"later")}, "uncertain"),
            ("nothing accepted", {"return_value": 0}, "failed"),
        ):
            with self.subTest(label):
                BoxReaderShare.objects.all().delete()
                with patch.object(services, "send_mail", **kwargs):
                    response = self.post()
                self.assertEqual(response.status_code, 201)
                self.assertEqual(BoxReaderShare.objects.get().delivery_status, expected)

    def test_successful_delivery_is_recorded_and_audited_without_the_secret(self):
        response = self.post()
        self.assertEqual(response.json()["share"]["invitation"]["delivery"]["status"], "sent")
        share = BoxReaderShare.objects.get()
        self.assertIsNotNone(share.delivery_updated_at)
        audit = AuditLog.objects.get(description="Box reader share invitation delivery recorded")
        self.assertEqual((audit.action, audit.user_id, audit.organization_id), ("update", None, self.w.source.pk))
        self.assertEqual(audit.metadata["delivery_status"], "sent")
        self.assertNotIn(last_token(), json.dumps(audit.metadata))

    def test_delivery_outcome_audit_failure_does_not_undo_the_sent_email(self):
        original = AuditLog.objects.create

        def fail_only_delivery(**kwargs):
            if kwargs.get("description") == "Box reader share invitation delivery recorded":
                raise RuntimeError("audit down")
            return original(**kwargs)

        with patch.object(AuditLog.objects, "create", side_effect=fail_only_delivery):
            response = self.post()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(mail.outbox), 1)
        # Honest: nothing was recorded, so the share still reads as pending.
        self.assertEqual(response.json()["share"]["invitation"]["delivery"]["status"], "pending")

    def test_audit_failure_rolls_the_share_back_and_sends_nothing(self):
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.post()
        self.assertEqual(BoxReaderShare.objects.count(), 0)
        self.assertEqual(len(mail.outbox), 0)

    def test_one_open_share_per_box_and_recipient(self):
        share, _token = create_share(self.w)
        duplicate = self.post(recipient_email="  Recipient@Example.Test ")
        self.assertEqual((duplicate.status_code, duplicate.json()["code"]), (409, "share_already_exists"))
        # Another recipient, or the same one after revocation, is a new share.
        self.assertEqual(self.post(recipient_email="loner@example.test").status_code, 201)
        client_for(self.w.admin, self.w.source).post(f"{URL}{share['id']}/revoke/")
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(BoxReaderShare.objects.filter(revoked_at__isnull=True).count(), 2)

    def test_response_does_not_reveal_whether_the_recipient_has_an_account(self):
        known = self.post().json()["share"]
        unknown = self.post(recipient_email="nobody@example.test").json()["share"]
        volatile = {"id", "recipient_email", "created_at", "invitation", "access_expires_at"}
        self.assertEqual(
            {key: value for key, value in known.items() if key not in volatile},
            {key: value for key, value in unknown.items() if key not in volatile},
        )

    def test_secret_is_only_in_the_email(self):
        response = self.post()
        token = last_token()
        share = BoxReaderShare.objects.get()
        self.assertNotIn(token, response.content.decode())
        self.assertNotEqual(share.invitation_token_hash, token)
        self.assertNotIn(token, json.dumps(list(BoxReaderShare.objects.values()), default=str))
        audits = AuditLog.objects.filter(object_type="box_reader_share")
        self.assertEqual(audits.count(), 2)  # creation + delivery outcome
        dumped = json.dumps(list(audits.values()), default=str)
        self.assertNotIn(token, dumped)
        self.assertNotIn(share.invitation_token_hash, dumped)
        self.assertNotIn("#token", dumped)

    def test_creation_is_audited_in_the_source_institution(self):
        self.post()
        audit = AuditLog.objects.get(object_type="box_reader_share", action="creation")
        share = BoxReaderShare.objects.get()
        self.assertEqual((audit.organization_id, audit.user_id, audit.action), (self.w.source.pk, self.w.admin.pk, "creation"))
        self.assertEqual(audit.object_id, str(share.public_id))
        self.assertEqual(audit.metadata["box_id"], self.w.box.pk)
        self.assertEqual(audit.metadata["recipient_email"], "recipient@example.test")
        self.assertEqual(audit.metadata["selectors"], sorted(OBSERVATION_SELECTORS))

    def test_sharing_changes_no_scientific_state(self):
        def state():
            return (
                list(Box.objects.order_by("pk").values()),
                list(BiologicalMeasurement.objects.order_by("pk").values()),
                OrganizationMembership.objects.count(),
            )
        before = state()
        share, token = create_share(self.w)
        accept(self.w.recipient, token)
        client = client_for(self.w.admin, self.w.source)
        client.patch(f"{URL}{share['id']}/", {"expected_policy_revision": 1, "selectors": BASE_SELECTORS}, format="json")
        client.post(f"{URL}{share['id']}/revoke/")
        self.assertEqual(state(), before)

    def test_preview_returns_only_the_selection_and_creates_nothing(self):
        client = client_for(self.w.admin, self.w.source)
        response = client.post(f"{URL}preview/", {"box_id": self.w.box.pk, "selectors": BASE_SELECTORS}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["snapshot"]["disclosed"], BASE_SELECTORS)
        self.assertNotIn("observations", response.json()["snapshot"])
        foreign = client.post(f"{URL}preview/", {"box_id": self.w.third_box.pk, "selectors": BASE_SELECTORS}, format="json")
        self.assertEqual((foreign.status_code, foreign.json()["code"]), (404, "box_not_found"))
        denied = client_for(self.w.viewer, self.w.source).post(
            f"{URL}preview/", {"box_id": self.w.box.pk, "selectors": BASE_SELECTORS}, format="json",
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(BoxReaderShare.objects.count(), 0)


@override_settings(**SHARING_SETTINGS)
class ShareManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.w = build_world()

    def setUp(self):
        self.client_admin = client_for(self.w.admin, self.w.source)
        self.share, self.token = create_share(self.w)
        self.detail = f"{URL}{self.share['id']}/"

    def patch_share(self, **body):
        return self.client_admin.patch(self.detail, body, format="json")

    def audits(self):
        return list(
            AuditLog.objects.filter(object_type="box_reader_share").order_by("pk")
            .values_list("action", "description")
        )

    def test_list_is_scoped_filtered_and_has_no_per_row_queries(self):
        create_share(self.w, recipient_email="loner@example.test", box_id=self.w.sibling.pk)
        create_share(self.w, recipient_email="third-admin@example.test", mode="live")
        with self.assertNumQueries(self._list_query_count()):
            body = self.client_admin.get(URL).json()
        self.assertEqual(len(body["shares"]), 3)
        filtered = self.client_admin.get(URL, {"box_id": self.w.sibling.pk}).json()["shares"]
        self.assertEqual([item["recipient_email"] for item in filtered], ["loner@example.test"])
        self.assertEqual(self.client_admin.get(URL, {"box_id": "1 OR 1=1"}).status_code, 400)
        page = self.client_admin.get(URL, {"limit": 2}).json()
        self.assertEqual((len(page["shares"]), page["has_more"]), (2, True))

    def _list_query_count(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as one:
            self.client_admin.get(URL, {"limit": 1})
        return len(one)

    def test_reducing_disclosure_creates_a_revision_without_confirmation(self):
        response = self.patch_share(expected_policy_revision=1, selectors=BASE_SELECTORS)
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()["share"]
        self.assertEqual((data["policy_revision"], data["selectors"]), (2, BASE_SELECTORS))
        audit = AuditLog.objects.filter(object_type="box_reader_share").order_by("-pk").first()
        self.assertEqual(audit.metadata["policy_revision"], 2)
        self.assertEqual(audit.metadata["selectors_added"], [])
        self.assertEqual(len(audit.metadata["selectors_removed"]), 4)

    def test_expanding_disclosure_needs_an_explicit_confirmation(self):
        self.patch_share(expected_policy_revision=1, selectors=BASE_SELECTORS)
        wider = [*BASE_SELECTORS, "source.box_code"]
        refused = self.patch_share(expected_policy_revision=2, selectors=wider)
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "disclosure_expansion_requires_confirmation"))
        self.assertEqual(refused.json()["added_selectors"], ["source.box_code"])
        self.assertEqual(BoxReaderShare.objects.get().policy_revision, 2)
        confirmed = self.patch_share(expected_policy_revision=2, selectors=wider, confirm_expansion=True)
        self.assertEqual(confirmed.json()["share"]["policy_revision"], 3)
        unknown = self.patch_share(expected_policy_revision=3, selectors=[*wider, "observation.notes"], confirm_expansion=True)
        self.assertEqual(unknown.json()["code"], "invalid_disclosure")

    def test_stale_policy_revision_is_refused(self):
        self.patch_share(expected_policy_revision=1, selectors=BASE_SELECTORS)
        stale = self.patch_share(expected_policy_revision=1, selectors=OBSERVATION_SELECTORS, confirm_expansion=True)
        self.assertEqual((stale.status_code, stale.json()["code"]), (409, "policy_revision_conflict"))
        self.assertEqual(stale.json()["current_policy_revision"], 2)
        self.assertEqual(BoxReaderShare.objects.get().selectors, BASE_SELECTORS)
        missing = self.client_admin.patch(self.detail, {"selectors": BASE_SELECTORS}, format="json")
        self.assertEqual(missing.json()["code"], "invalid_request")

    def test_identical_policy_creates_no_revision_and_no_audit(self):
        before = self.audits()
        response = self.patch_share(expected_policy_revision=1, selectors=list(reversed(OBSERVATION_SELECTORS)))
        self.assertEqual(response.json()["share"]["policy_revision"], 1)
        self.assertEqual(self.audits(), before)

    def test_access_end_can_be_moved_within_bounds(self):
        target = timezone.now() + timedelta(days=60)
        response = self.patch_share(expected_policy_revision=1, access_expires_at=target.isoformat())
        self.assertEqual(response.status_code, 200, response.content)
        share = BoxReaderShare.objects.get()
        self.assertEqual((share.access_expires_at, share.policy_revision), (target, 1))
        for days in (91, -1, 1):  # beyond bound, past, before the pending invitation end
            refused = self.patch_share(expected_policy_revision=1, access_expires_at=in_days(days))
            self.assertEqual((refused.status_code, refused.json()["code"]), (400, "access_expiry_out_of_bounds"))
        self.assertEqual(BoxReaderShare.objects.get().access_expires_at, target)

    def test_revocation_is_audited_idempotent_and_final(self):
        first = self.client_admin.post(f"{self.detail}revoke/")
        self.assertEqual(first.json()["share"]["state"], "revoked")
        share = BoxReaderShare.objects.get()
        self.assertIsNone(share.invitation_token_hash)
        self.assertEqual(share.revoked_by_id, self.w.admin.pk)
        revoked_at = share.revoked_at
        self.client_admin.post(f"{self.detail}revoke/")
        self.assertEqual(BoxReaderShare.objects.get().revoked_at, revoked_at)
        self.assertEqual(self.audits(), [
            ("creation", "Box reader share created"),
            ("update", "Box reader share invitation delivery recorded"),
            ("archive", "Box reader share revoked"),
        ])
        # Nothing reopens a revoked share.
        self.assertEqual(self.patch_share(expected_policy_revision=1, selectors=BASE_SELECTORS).json()["code"], "share_revoked")
        reissue = self.client_admin.post(
            f"{self.detail}reissue-invitation/", {"invitation_expires_at": in_days(1)}, format="json",
        )
        self.assertEqual((reissue.status_code, reissue.json()["code"]), (409, "invitation_not_pending"))
        self.assertEqual(accept(self.w.recipient, self.token).json()["code"], "invitation_unavailable")
        self.assertIsNone(BoxReaderShare.objects.get().recipient_user_id)

    def test_revocation_audit_failure_keeps_the_share_active(self):
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                self.client_admin.post(f"{self.detail}revoke/")
        self.assertIsNone(BoxReaderShare.objects.get().revoked_at)

    def test_reissue_supersedes_the_previous_secret(self):
        sent = len(mail.outbox)
        response = self.client_admin.post(
            f"{self.detail}reissue-invitation/", {"invitation_expires_at": in_days(3)}, format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["share"]["invitation"]["generation"], 2)
        self.assertEqual(len(mail.outbox), sent + 1)
        new_token = last_token()
        self.assertNotEqual(new_token, self.token)
        self.assertNotIn(new_token, response.content.decode())
        self.assertEqual(accept(self.w.recipient, self.token).json()["code"], "invitation_unavailable")
        self.assertEqual(accept(self.w.recipient, new_token).status_code, 200)
        after = self.client_admin.post(
            f"{self.detail}reissue-invitation/", {"invitation_expires_at": in_days(3)}, format="json",
        )
        self.assertEqual(after.json()["code"], "invitation_not_pending")
        dumped = json.dumps(list(AuditLog.objects.filter(object_type="box_reader_share").values()), default=str)
        self.assertNotIn(new_token, dumped)

    def test_reissue_is_bounded(self):
        refused = self.client_admin.post(
            f"{self.detail}reissue-invitation/", {"invitation_expires_at": in_days(8)}, format="json",
        )
        self.assertEqual(refused.json()["code"], "invitation_expiry_out_of_bounds")
        self.assertEqual(BoxReaderShare.objects.get().invitation_generation, 1)

    def test_failed_reissue_delivery_is_committed_and_retryable(self):
        access_end = BoxReaderShare.objects.get().access_expires_at
        with patch.object(services, "send_mail", return_value=0):
            failed = self.client_admin.post(
                f"{self.detail}reissue-invitation/", {"invitation_expires_at": in_days(3)}, format="json",
            )
        self.assertEqual(failed.status_code, 200)
        body = failed.json()["share"]["invitation"]
        self.assertEqual((body["generation"], body["delivery"]["status"]), (2, "failed"))
        # The first secret is already superseded; the failed one never left.
        self.assertEqual(accept(self.w.recipient, self.token).json()["code"], "invitation_unavailable")
        retry = self.client_admin.post(
            f"{self.detail}reissue-invitation/", {"invitation_expires_at": in_days(3)}, format="json",
        )
        self.assertEqual(retry.json()["share"]["invitation"]["delivery"]["status"], "sent")
        self.assertEqual(accept(self.w.recipient, last_token()).status_code, 200)
        # Retry never touches the approved access end.
        self.assertEqual(BoxReaderShare.objects.get().access_expires_at, access_end)

    def test_delivery_is_skipped_for_a_revoked_or_superseded_invitation(self):
        share = BoxReaderShare.objects.get()
        secret, digest = services.new_invitation_secret()
        BoxReaderShare.objects.filter(pk=share.pk).update(invitation_token_hash=digest, invitation_generation=5)
        sent = len(mail.outbox)
        # A stale generation (an older secret) is not sent.
        self.assertIsNone(services.deliver_invitation(share.pk, 1, self.token))
        client_for(self.w.admin, self.w.source).post(f"{self.detail}revoke/")
        self.assertIsNone(services.deliver_invitation(share.pk, 5, secret))
        self.assertEqual(len(mail.outbox), sent)

    def test_revocation_during_smtp_is_not_overwritten_by_the_outcome(self):
        share = BoxReaderShare.objects.get()
        secret, digest = services.new_invitation_secret()
        BoxReaderShare.objects.filter(pk=share.pk).update(invitation_token_hash=digest, invitation_generation=2)

        def revoke_during_send(*args, **kwargs):
            client_for(self.w.admin, self.w.source).post(f"{self.detail}revoke/")
            return 1

        status_before = (share.delivery_status, share.delivery_updated_at)
        with patch.object(services, "send_mail", side_effect=revoke_during_send):
            services.deliver_invitation(share.pk, 2, secret)
        stored = BoxReaderShare.objects.get()
        self.assertIsNotNone(stored.revoked_at)
        self.assertEqual(stored.delivery_updated_at, status_before[1])
        self.assertEqual(stored.delivery_status, status_before[0])
        self.assertFalse(AuditLog.objects.filter(
            description="Box reader share invitation delivery recorded", metadata__invitation_generation=2,
        ).exists())
        self.assertEqual(accept(self.w.recipient, secret).json()["code"], "invitation_unavailable")

    def test_reissue_is_refused_for_an_expired_share(self):
        BoxReaderShare.objects.update(access_expires_at=timezone.now() - timedelta(minutes=1))
        response = self.client_admin.post(
            f"{self.detail}reissue-invitation/", {"invitation_expires_at": in_days(1)}, format="json",
        )
        self.assertIn(response.json()["code"], ("share_expired", "invitation_expiry_out_of_bounds"))

    def test_management_responses_are_not_cacheable(self):
        for response in (self.client_admin.get(URL), self.client_admin.get(self.detail),
                         client_for(self.w.viewer, self.w.source).get(URL)):
            self.assertEqual(response["Cache-Control"], "no-store")
            self.assertIn("Cookie", response["Vary"])

    def test_live_share_stores_no_capture(self):
        self.assertEqual(BoxReaderShareCapture.objects.count(), 0)
        self.assertIsNone(self.share["capture"])
