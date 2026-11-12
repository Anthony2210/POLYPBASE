"""Scientific disclosure of the reader response, in LIVE and FROZEN modes."""

from datetime import date
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import patch

from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.measurements import scientific_snapshot
from apps.measurements.models import BiologicalMeasurement
from apps.measurements.scientific_snapshot import SELECTORS, canonical_json, validate_snapshot
from apps.taxonomy.models import GlobalStrainIdentity, Strain

from . import services
from .models import BoxReaderShare, BoxReaderShareCapture
from .sharing_testing import (
    BASE_SELECTORS,
    OBSERVATION_SELECTORS,
    SHARING_SETTINGS,
    build_world,
    client_for,
    create_accepted_share,
    create_payload,
    in_days,
)

ADMIN = "/api/admin/box-shares/"
READER = "/api/shared-boxes/"


class ReaderMixin:
    def share(self, **overrides):
        data, public_id = create_accepted_share(self.w, **overrides)
        return data, f"{READER}{public_id}/"

    def read(self, url):
        response = client_for(self.w.recipient).get(url)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def patch_policy(self, share_id, **body):
        return client_for(self.w.admin, self.w.source).patch(f"{ADMIN}{share_id}/", body, format="json")


@override_settings(**SHARING_SETTINGS)
class LiveDisclosureTests(ReaderMixin, TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.w = build_world()

    def test_only_selected_data_is_serialized(self):
        _data, url = self.share(selectors=["source.institution_name", "observation.time", "observation.polyp_count"])
        response = client_for(self.w.recipient).get(url)
        snapshot = response.json()["snapshot"]
        self.assertEqual(validate_snapshot(snapshot), ())
        self.assertEqual(set(snapshot), {"schema", "schema_version", "disclosed", "source", "observations"})
        self.assertEqual(snapshot["source"], {"institution_name": {"state": "value", "value": "Synthetic source lab"}})
        self.assertEqual(snapshot["observations"], [{
            "time": {"precision": "legacy_unverified", "iso_year": 2026, "iso_week": 16, "measured_on": "2026-04-15"},
            "values": {"polyp_count": {"state": "value", "value": 0}},
        }])
        text = response.content.decode()
        for hidden in ("ephyrae", "strobila", "SHR-AAA", "Aurelia", "box_code", "strain", "portable",
                       "private synthetic note", "origin", "observer", str(self.w.source.portable_id),
                       self.w.admin.email):
            self.assertNotIn(hidden, text)
        # No internal identifier of the source is exposed either.
        self.assertNotIn('"box_id"', text)
        self.assertNotIn('"organization', text)

    def test_zero_null_and_absent_stay_distinct(self):
        _data, url = self.share()
        values = self.read(url)["snapshot"]["observations"][0]["values"]
        self.assertEqual(values["polyp_count"], {"state": "value", "value": 0})
        self.assertEqual(values["ephyrae_count"], {"state": "value", "value": 0})
        self.assertEqual(values["strobila_count"], {"state": "null"})
        self.assertNotIn("value", values["strobila_count"])

    def test_empty_disclosed_history_differs_from_undisclosed_history(self):
        w = self.w
        empty_box = Box.objects.create(
            organization=w.source, strain=w.strain, global_code="SHR-AAA-1.050", box_number="50",
        )
        _data, disclosed_url = self.share(box_id=empty_box.pk)
        _data, undisclosed_url = self.share(box_id=w.sibling.pk, selectors=BASE_SELECTORS)
        self.assertEqual(self.read(disclosed_url)["snapshot"]["observations"], [])
        self.assertNotIn("observations", self.read(undisclosed_url)["snapshot"])

    def test_time_precision_is_reported_as_stored(self):
        w = self.w
        exact = BiologicalMeasurement.objects.create(
            box=w.box, measured_on=date(2026, 5, 6), polyp_count=4, ephyrae_count=1,
        )
        BiologicalMeasurement.objects.filter(pk=exact.pk).update(temporal_precision="exact_date")
        BiologicalMeasurement.objects.create(
            box=w.box, measured_on=date(2021, 1, 3), polyp_count=12, ephyrae_count=3, strobila_count=0,
        )
        _data, url = self.share(selectors=["source.institution_name", "observation.time"])
        times = [item["time"] for item in self.read(url)["snapshot"]["observations"]]
        self.assertEqual(times, [
            {"precision": "legacy_unverified", "iso_year": 2020, "iso_week": 53, "measured_on": "2021-01-03"},
            {"precision": "legacy_unverified", "iso_year": 2026, "iso_week": 16, "measured_on": "2026-04-15"},
            {"precision": "exact_date", "iso_year": 2026, "iso_week": 19, "measured_on": "2026-05-06"},
        ])

    def test_identity_is_never_invented(self):
        selectors = [*BASE_SELECTORS, "identity.global_strain_id", "identity.strain_code", "source.box_code"]
        before = GlobalStrainIdentity.objects.count()
        _data, url = self.share(selectors=selectors)
        snapshot = self.read(url)["snapshot"]
        self.assertEqual(snapshot["identity"]["global_strain_id"], {"state": "null"})
        self.assertEqual(snapshot["identity"]["strain_code"], {"state": "value", "value": "SHR-AAA-1"})
        self.assertEqual(snapshot["source"]["box_code"], {"state": "value", "value": "SHR-AAA-1.001"})
        self.assertEqual(GlobalStrainIdentity.objects.count(), before)
        identity = GlobalStrainIdentity.objects.create()
        Strain.objects.filter(pk=self.w.strain.pk).update(global_identity=identity)
        self.assertEqual(
            self.read(url)["snapshot"]["identity"]["global_strain_id"],
            {"state": "value", "value": str(identity.global_id)},
        )

    def test_lineage_and_neighbouring_boxes_are_never_revealed(self):
        _data, url = self.share(selectors=sorted(SELECTORS))
        text = client_for(self.w.recipient).get(url).content.decode()
        for hidden in ("SHR-AAA-1.777", '"value":999', '"value":998', "lineage", "parent", "child", "THD-AAA"):
            self.assertNotIn(hidden, text)

    def test_new_measurements_and_corrections_of_disclosed_fields_appear(self):
        w = self.w
        _data, url = self.share()
        self.assertEqual(len(self.read(url)["snapshot"]["observations"]), 1)
        BiologicalMeasurement.objects.create(box=w.box, measured_on=date(2026, 5, 6), polyp_count=8, ephyrae_count=2)
        BiologicalMeasurement.objects.filter(pk=w.zero.pk).update(polyp_count=3)
        body = self.read(url)
        observations = body["snapshot"]["observations"]
        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0]["values"]["polyp_count"], {"state": "value", "value": 3})
        self.assertEqual(observations[1]["values"]["polyp_count"], {"state": "value", "value": 8})
        self.assertEqual(body["share"]["policy_revision"], 1)

    def test_fields_outside_the_policy_stay_excluded_whatever_the_source_holds(self):
        w = self.w
        _data, url = self.share(selectors=["source.institution_name", "observation.time", "observation.polyp_count"])
        BiologicalMeasurement.objects.filter(pk=w.zero.pk).update(ephyrae_count=77, strobila_count=66)
        body = self.read(url)
        self.assertEqual(body["snapshot"]["disclosed"], ["observation.polyp_count", "observation.time", "source.institution_name"])
        self.assertEqual(set(body["snapshot"]["observations"][0]["values"]), {"polyp_count"})

    def test_new_catalogue_selector_does_not_expand_an_existing_grant(self):
        data, url = self.share()
        before = self.read(url)["snapshot"]
        # A later catalogue offers one more selector; stored policies must not gain it.
        grown = dict(scientific_snapshot.SOURCE_SELECTORS, **{"source.future_field": "future_field"})
        with patch.object(scientific_snapshot, "SELECTORS", frozenset({*SELECTORS, "source.future_field"})), \
                patch.object(scientific_snapshot, "SOURCE_SELECTORS", grown):
            self.assertEqual(self.read(url)["snapshot"], before)
        self.assertEqual(BoxReaderShare.objects.get(pk=data["id"]).selectors, sorted(OBSERVATION_SELECTORS))

    def test_restrictive_policy_update_is_honoured_on_the_next_read(self):
        data, url = self.share()
        self.assertIn("observations", self.read(url)["snapshot"])
        self.assertEqual(self.patch_policy(data["id"], expected_policy_revision=1, selectors=BASE_SELECTORS).status_code, 200)
        body = self.read(url)
        self.assertEqual(body["share"]["policy_revision"], 2)
        self.assertEqual(body["snapshot"]["disclosed"], BASE_SELECTORS)
        self.assertNotIn("observations", body["snapshot"])
        self.assertNotIn("polyp_count", str(body))

    def test_tampered_stored_policy_serves_nothing(self):
        data, url = self.share()
        for selectors in (["source.institution_name", "observation.notes"], ["source.box_code"], [], "source.institution_name"):
            with self.subTest(selectors=selectors):
                BoxReaderShare.objects.filter(pk=data["id"]).update(selectors=selectors)
                response = client_for(self.w.recipient).get(url)
                self.assertEqual((response.status_code, response.json()["code"]), (409, "share_source_unavailable"))
                self.assertEqual(set(response.json()), {"detail", "code"})
        BoxReaderShare.objects.filter(pk=data["id"]).update(selectors=sorted(OBSERVATION_SELECTORS), schema_version="9.9")
        self.assertEqual(client_for(self.w.recipient).get(url).json()["code"], "share_source_unavailable")
        self.assertFalse(AuditLog.objects.filter(action="view", object_type="box_reader_share").exists())

    def test_merged_box_becomes_unavailable_and_inactive_box_stays_readable(self):
        w = self.w
        _data, url = self.share()
        # Deactivation alone does not change what the projection contract reads.
        Box.objects.filter(pk=w.box.pk).update(status=Box.Status.INACTIVE, thermal_zone=None)
        self.assertEqual(client_for(w.recipient).get(url).status_code, 200)
        Box.objects.filter(pk=w.box.pk).update(merged_into=w.sibling, previous_global_code="SHR-AAA-1.001")
        response = client_for(w.recipient).get(url)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "share_source_unavailable"))
        self.assertNotIn("SHR-AAA-1.777", response.content.decode())


@override_settings(**SHARING_SETTINGS)
class FrozenSharingTests(ReaderMixin, TransactionTestCase):
    """Own transactions: a frozen capture must own its database transaction."""

    def setUp(self):
        self.w = build_world()

    def test_capture_is_bound_to_the_policy_revision_and_reproducible(self):
        data, url = self.share(mode="frozen")
        capture = BoxReaderShareCapture.objects.get()
        self.assertEqual((capture.share_id, capture.policy_revision, capture.schema_version), (data["id"], 1, "1.0"))
        self.assertEqual(capture.captured_by_id, self.w.admin.pk)
        self.assertEqual(capture.content_sha256, hashlib.sha256(capture.content.encode()).hexdigest())
        self.assertEqual(data["capture"]["content_sha256"], capture.content_sha256)
        body = self.read(url)
        self.assertEqual(body["share"]["mode"], "frozen")
        self.assertEqual(body["content"]["kind"], "frozen_capture")
        self.assertEqual(body["content"]["content_sha256"], capture.content_sha256)
        self.assertIn("captured_at", body["content"])
        self.assertEqual(canonical_json(body["snapshot"]), capture.content)
        self.assertEqual(validate_snapshot(body["snapshot"]), ())
        # Only the disclosed selection was stored, not a wider private copy.
        self.assertEqual(body["snapshot"]["disclosed"], sorted(OBSERVATION_SELECTORS))
        for hidden in ("SHR-AAA", "private synthetic note", "Aurelia"):
            self.assertNotIn(hidden, capture.content)
        audit = AuditLog.objects.get(description="Box reader share created")
        self.assertEqual(audit.metadata["capture_sha256"], capture.content_sha256)
        self.assertNotIn("observations", str(audit.metadata))

    def test_content_does_not_change_after_source_edits(self):
        w = self.w
        _data, url = self.share(mode="frozen")
        before = self.read(url)
        BiologicalMeasurement.objects.create(box=w.box, measured_on=date(2026, 5, 6), polyp_count=8, ephyrae_count=2)
        BiologicalMeasurement.objects.filter(pk=w.zero.pk).update(polyp_count=41, strobila_count=5)
        type(w.source).objects.filter(pk=w.source.pk).update(name="Renamed source lab")
        after = self.read(url)
        self.assertEqual(after["snapshot"], before["snapshot"])
        self.assertEqual(after["content"], before["content"])
        self.assertEqual(len(after["snapshot"]["observations"]), 1)
        self.assertEqual(after["snapshot"]["observations"][0]["values"]["polyp_count"], {"state": "value", "value": 0})
        # The banner names the current owner; the capture keeps the captured name.
        self.assertEqual(after["share"]["source_institution"], {"name": "Renamed source lab"})
        self.assertEqual(after["snapshot"]["source"]["institution_name"]["value"], "Synthetic source lab")
        self.assertEqual(BoxReaderShareCapture.objects.count(), 1)

    def test_policy_change_is_an_explicit_new_capture_and_keeps_the_old_one(self):
        w = self.w
        data, url = self.share(mode="frozen")
        first = BoxReaderShareCapture.objects.get()
        first_content = first.content
        BiologicalMeasurement.objects.filter(pk=w.zero.pk).update(polyp_count=9)
        selectors = ["source.institution_name", "observation.time", "observation.polyp_count"]
        refused = self.patch_policy(data["id"], expected_policy_revision=1, selectors=selectors)
        self.assertEqual((refused.status_code, refused.json()["code"]), (409, "frozen_policy_change_requires_recapture"))
        self.assertEqual(BoxReaderShareCapture.objects.count(), 1)
        self.assertEqual(self.read(url)["share"]["policy_revision"], 1)

        accepted = self.patch_policy(data["id"], expected_policy_revision=1, selectors=selectors, confirm_recapture=True)
        self.assertEqual(accepted.status_code, 200, accepted.content)
        self.assertEqual(accepted.json()["share"]["policy_revision"], 2)
        captures = list(BoxReaderShareCapture.objects.order_by("policy_revision"))
        self.assertEqual([item.policy_revision for item in captures], [1, 2])
        self.assertEqual(captures[0].content, first_content)
        self.assertEqual(accepted.json()["share"]["capture"]["content_sha256"], captures[1].content_sha256)
        body = self.read(url)
        self.assertEqual(body["share"]["policy_revision"], 2)
        self.assertEqual(body["content"]["content_sha256"], captures[1].content_sha256)
        # A new capture of the current state, restricted to the new policy.
        self.assertEqual(body["snapshot"]["observations"][0]["values"], {"polyp_count": {"state": "value", "value": 9}})
        self.assertNotIn("ephyrae", str(body["snapshot"]))

    def test_moving_the_access_end_does_not_recapture(self):
        data, _url = self.share(mode="frozen")
        response = self.patch_policy(data["id"], expected_policy_revision=1, access_expires_at=in_days(60))
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(BoxReaderShareCapture.objects.count(), 1)
        self.assertEqual(response.json()["share"]["policy_revision"], 1)

    def test_captures_cannot_be_rewritten_or_deleted(self):
        self.share(mode="frozen")
        capture = BoxReaderShareCapture.objects.get()
        capture.content = "{}"
        with self.assertRaises(ValidationError):
            capture.save()
        with self.assertRaises(ValidationError):
            capture.delete()
        with self.assertRaises(ValidationError):
            BoxReaderShareCapture.objects.filter(pk=capture.pk).update(content="{}")
        with self.assertRaises(ValidationError):
            BoxReaderShareCapture.objects.all().delete()

    def test_corrupted_or_mismatched_capture_is_not_served(self):
        data, url = self.share(mode="frozen")
        capture = BoxReaderShareCapture.objects.get()
        tampered = capture.content.replace('"value":0', '"value":7', 1)
        self.assertNotEqual(tampered, capture.content)
        # Raw SQL: the model deliberately offers no update path.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE sharing_boxreadersharecapture SET content = %s WHERE id = %s", [tampered, capture.pk],
            )
        response = client_for(self.w.recipient).get(url)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "share_source_unavailable"))
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE sharing_boxreadersharecapture SET content = %s WHERE id = %s", [capture.content, capture.pk],
            )
        self.assertEqual(client_for(self.w.recipient).get(url).status_code, 200)
        # A policy without a capture of its own revision serves nothing.
        BoxReaderShare.objects.filter(pk=data["id"]).update(policy_revision=5)
        self.assertEqual(client_for(self.w.recipient).get(url).json()["code"], "share_source_unavailable")

    def test_revocation_and_merge_apply_to_frozen_shares_too(self):
        w = self.w
        data, url = self.share(mode="frozen")
        Box.objects.filter(pk=w.box.pk).update(
            status=Box.Status.INACTIVE, thermal_zone=None, merged_into=w.sibling, previous_global_code="SHR-AAA-1.001",
        )
        self.assertEqual(client_for(w.recipient).get(url).json()["code"], "share_source_unavailable")
        Box.objects.filter(pk=w.box.pk).update(merged_into=None, previous_global_code="")
        self.assertEqual(client_for(w.recipient).get(url).status_code, 200)
        client_for(w.admin, w.source).post(f"{ADMIN}{data['id']}/revoke/")
        response = client_for(w.recipient).get(url)
        self.assertEqual((response.status_code, response.json()["code"]), (410, "share_revoked"))
        # Revocation keeps the capture as source-side evidence.
        self.assertEqual(BoxReaderShareCapture.objects.count(), 1)

    def test_failed_delivery_keeps_the_committed_capture(self):
        with patch.object(services, "send_mail", side_effect=RuntimeError("smtp down")):
            response = client_for(self.w.admin, self.w.source).post(
                ADMIN, create_payload(self.w, mode="frozen"), format="json",
            )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["share"]["invitation"]["delivery"]["status"], "uncertain")
        self.assertEqual((BoxReaderShare.objects.count(), BoxReaderShareCapture.objects.count()), (1, 1))

    def test_failed_audit_leaves_no_capture_and_sends_nothing(self):
        with patch.object(AuditLog.objects, "create", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                client_for(self.w.admin, self.w.source).post(
                    ADMIN, create_payload(self.w, mode="frozen"), format="json",
                )
        self.assertEqual((BoxReaderShare.objects.count(), BoxReaderShareCapture.objects.count()), (0, 0))
        self.assertEqual(len(mail.outbox), 0)

    def test_sqlite_frozen_capture_needs_the_verified_environment_opt_in(self):
        if connection.vendor != "sqlite":
            self.skipTest("SQLite guard")
        admin = client_for(self.w.admin, self.w.source)
        with override_settings(BOX_SHARING_SQLITE_FROZEN_CAPTURE=False):
            response = admin.post(ADMIN, create_payload(self.w, mode="frozen"), format="json")
            self.assertEqual((response.status_code, response.json()["code"]), (503, "frozen_capture_unavailable"))
            self.assertEqual((BoxReaderShare.objects.count(), BoxReaderShareCapture.objects.count()), (0, 0))
            self.assertEqual(len(mail.outbox), 0)
            self.assertFalse(admin.get(f"{ADMIN}options/").json()["frozen_capture_available"])
            # LIVE sharing does not depend on a snapshot guarantee.
            self.assertEqual(admin.post(ADMIN, create_payload(self.w), format="json").status_code, 201)
        # An existing frozen share is still served; its recapture is refused.
        data, url = self.share(recipient_email="loner@example.test", user=self.w.loner, mode="frozen")
        with override_settings(BOX_SHARING_SQLITE_FROZEN_CAPTURE=False):
            self.assertEqual(client_for(self.w.loner).get(url).status_code, 200)
            refused = self.patch_policy(
                data["id"], expected_policy_revision=1, selectors=BASE_SELECTORS, confirm_recapture=True,
            )
            self.assertEqual((refused.status_code, refused.json()["code"]), (503, "frozen_capture_unavailable"))
        self.assertTrue(admin.get(f"{ADMIN}options/").json()["frozen_capture_available"])

    def test_default_settings_refuse_sqlite_frozen_capture(self):
        from config import settings as base_settings

        self.assertIs(base_settings.BOX_SHARING_SQLITE_FROZEN_CAPTURE, False)


@override_settings(**SHARING_SETTINGS)
class CorruptedCaptureTests(TransactionTestCase):
    """F1: a bad stored capture is `share_source_unavailable`, never a 500.

    Own transactions: a frozen capture must own its database transaction.
    """

    def setUp(self):
        self.w = build_world()
        data, public_id = create_accepted_share(self.w, mode="frozen")
        self.data, self.url = data, f"{READER}{public_id}/"
        self.capture = BoxReaderShareCapture.objects.get()
        self.reader = client_for(self.w.recipient)

    def store(self, content, digest=None):
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest() if digest is None else digest
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE sharing_boxreadersharecapture SET content = %s, content_sha256 = %s WHERE id = %s",
                [content, digest, self.capture.pk],
            )

    def assert_unavailable(self):
        response = self.reader.get(self.url)
        self.assertEqual((response.status_code, response.json()["code"]), (409, "share_source_unavailable"))
        self.assertEqual(set(response.json()), {"detail", "code"})
        self.assertFalse(AuditLog.objects.filter(action="view", object_type="box_reader_share").exists())

    def test_invalid_json_with_a_matching_digest(self):
        for content in ("{not json", "", "[" * 5000):
            with self.subTest(content=content[:10]):
                self.store(content)
                self.assert_unavailable()

    def test_wrong_digest_is_rejected_before_parsing(self):
        def refuse(*args, **kwargs):
            raise AssertionError("parsed untrusted text")

        # Only the sharing module's parser: sessions and DRF keep using json.
        with patch.object(services, "json", SimpleNamespace(loads=refuse)):
            self.store(self.capture.content, digest="0" * 64)
            self.assert_unavailable()
            self.store("{not json", digest="0" * 64)
            self.assert_unavailable()

    def test_correctly_hashed_but_non_canonical_or_invalid_content(self):
        good = json.loads(self.capture.content)
        for label, content in (
            ("not an object", "[]"),
            ("scalar", "7"),
            ("null", "null"),
            ("schema invalid", canonical_json({**good, "observations": "not a list"})),
            ("unknown key", canonical_json({**good, "private": "note"})),
            ("other policy", canonical_json({**good, "disclosed": ["source.institution_name"]})),
            ("pretty printed", json.dumps(good, indent=2)),
        ):
            with self.subTest(label):
                self.store(content)
                self.assert_unavailable()

    def test_original_content_is_served_again(self):
        self.store("{broken")
        self.assert_unavailable()
        self.store(self.capture.content)
        self.assertEqual(self.reader.get(self.url).status_code, 200)
