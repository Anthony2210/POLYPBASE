"""Storage tests use rollback-only TestCase: immutable SQL rows cannot be flushed."""
from copy import deepcopy
import hashlib
from importlib import import_module
import json
from unittest.mock import Mock
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, models, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain
from .history_preview_validation import MAX_JUSTIFICATION_LENGTH, MAX_JUSTIFICATIONS, MAX_OPERATIONS
from .models import Box, BoxHistoryEventIdentity, BoxHistoryRevision, BoxHistoryStream


class HistoryRevisionStorageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.org = Organization.objects.create(name="History storage laboratory")
        cls.other_org = Organization.objects.create(name="Other history laboratory")
        species = Species.objects.create(scientific_name="History species")
        strain = Strain.objects.create(organization=cls.org, species=species, code="HISTORY")
        cls.box = Box.objects.create(organization=cls.org, strain=strain, global_code="HISTORY.001", box_number="1")
        cls.user = get_user_model().objects.create_user(username="history-author", email="history-author@example.test", first_name="Stored", last_name="Author")
        cls.stream = BoxHistoryStream.objects.create(organization=cls.org, box=cls.box)
        other_strain = Strain.objects.create(organization=cls.other_org, species=species, code="OTHER-HISTORY")
        cls.other_box = Box.objects.create(organization=cls.other_org, strain=other_strain, global_code="OTHER-HISTORY.001", box_number="1")
        cls.other = BoxHistoryStream.objects.create(organization=cls.other_org, box=cls.other_box)

    def revision(self, **kwargs):
        return BoxHistoryRevision.objects.create(**{
            "stream": self.stream, "sequence": 1, "author": self.user,
            "reason": "Reviewed correction", "source_fingerprint": "a" * 64,
            "request_fingerprint": "b" * 64, **kwargs,
        })

    def sql(self, query, params):
        with connection.cursor() as cursor:
            cursor.execute(query, params)

    def rejects_sql(self, query, params):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.sql(query, params)

    def raw_insert_revision(self, **changes):
        fields = {
            "id": uuid4(), "stream_id": self.stream.pk, "sequence": 1,
            "predecessor_id": None, "author_id": self.user.pk,
            "author_identifier": self.user.pk, "author_label": "Stored Author",
            "reason": "SQL review", "created_at": "2026-11-01 12:00:00",
            "operations": "[]", "justifications": "[]", "review_values": "{}", "metadata": "{}",
            "schema_version": 1, "source_fingerprint": "a" * 64,
            "idempotency_key": uuid4(), "request_fingerprint": "b" * 64,
            **changes,
        }
        fields = {key: (value.hex if isinstance(value, UUID) and connection.vendor == "sqlite" else value) for key, value in fields.items()}
        self.sql(f"INSERT INTO cultures_boxhistoryrevision ({', '.join(fields)}) VALUES ({', '.join(['%s'] * len(fields))})", list(fields.values()))

    def test_creation_is_inert_and_snapshots_fresh_author(self):
        get_user_model().objects.filter(pk=self.user.pk).update(first_name="Fresh")
        revision = self.revision(author_identifier=777, author_label="Untrusted", operations=[{"type": "adjust_event_zones", "target": {"key": "box_movement:1"}, "origin_zone_id": None}])
        self.assertEqual(revision.author_identifier, self.user.pk)
        self.assertEqual(revision.author_label, "Fresh Author")
        self.assertIsNone(revision.operations[0]["origin_zone_id"])
        self.assertNotIn("destination_zone_id", revision.operations[0])
        self.stream.refresh_from_db()
        self.assertIsNone(self.stream.head_id)
        self.assertEqual(self.stream.original_box_id, self.box.pk)
        self.assertEqual(revision.id.version, 4)
        self.assertIsNotNone(revision.created_at)

    def test_author_must_be_stored_and_currently_active(self):
        for author_id in (None, 999999):
            with self.assertRaises(ValidationError):
                self.revision(author=None, author_id=author_id)
        get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(ValidationError):
            self.revision()

    def test_orm_immutability(self):
        revision = self.revision()
        identity = BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="inserted")
        for obj in (revision, identity):
            with self.assertRaises(ValidationError):
                obj.save()
            with self.assertRaises(ValidationError):
                obj.delete()
            with self.assertRaises(ValidationError):
                type(obj).objects.filter(pk=obj.pk).update(id=uuid4())
            with self.assertRaises(ValidationError):
                type(obj).objects.filter(pk=obj.pk).delete()
            with self.assertRaises(ValidationError):
                type(obj).objects.bulk_create([obj])

    def test_sql_guards_cover_every_immutable_column_and_delete(self):
        revision = self.revision()
        identity = BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="inserted")
        for obj in (revision, identity):
            pk = obj.pk.hex if connection.vendor == "sqlite" else obj.pk
            for field in obj._meta.local_fields:
                if field.name == "author":
                    continue
                # Even valid no-op identity updates are forbidden; revision guards
                # compare all columns, so alter each using its storage type.
                if isinstance(field, models.UUIDField):
                    value = uuid4().hex if connection.vendor == "sqlite" else uuid4()
                elif isinstance(field, models.ForeignKey):
                    value = self.other.pk.hex if connection.vendor == "sqlite" else self.other.pk
                elif isinstance(field, models.JSONField):
                    value = '{"changed":1}'
                elif isinstance(field, models.DateTimeField):
                    value = "2025-01-01 00:00:00"
                elif isinstance(field, (models.IntegerField, models.BigIntegerField)):
                    value = 777
                else:
                    value = "changed"
                self.rejects_sql(f"UPDATE {obj._meta.db_table} SET {field.column} = %s WHERE id = %s", [value, pk])
            self.rejects_sql(f"DELETE FROM {obj._meta.db_table} WHERE id = %s", [pk])

    def test_box_and_author_deletion_preserve_storage(self):
        revision = self.revision()
        identity = BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="persisted", source_key="measurement:1")
        original_box_id, author_id = self.box.pk, self.user.pk
        self.box.delete()
        self.user.delete()
        self.stream.refresh_from_db()
        revision.refresh_from_db()
        self.assertIsNone(self.stream.box_id)
        self.assertEqual(self.stream.original_box_id, original_box_id)
        self.assertIsNone(revision.author_id)
        self.assertEqual(revision.author_identifier, author_id)
        self.assertEqual(revision.author_label, "Stored Author")
        self.assertTrue(BoxHistoryEventIdentity.objects.filter(pk=identity.pk).exists())
        with self.assertRaises(ProtectedError):
            self.org.delete()
        self.rejects_sql("UPDATE cultures_boxhistoryrevision SET author_id = %s WHERE id = %s", [author_id, revision.pk.hex])

    def test_staged_chain_head_advances_only_one_step_in_same_stream(self):
        first = self.revision()
        self.stream.head = first
        self.stream.save()
        second = self.revision(sequence=2, predecessor=first)
        third = self.revision(sequence=3, predecessor=second)
        for head in (None, third):
            self.stream.head = head
            with self.assertRaises(ValidationError):
                self.stream.save()
            self.rejects_sql("UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s", [head.pk.hex if head else None, self.stream.pk.hex])
        foreign = self.revision(stream=self.other)
        self.rejects_sql("UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s", [foreign.pk.hex, self.stream.pk.hex])
        self.stream.head = foreign
        with self.assertRaises(ValidationError):
            self.stream.save()
        for head in (second, third):
            self.stream.head = head
            self.stream.save()
            self.stream.refresh_from_db()
            self.assertEqual(self.stream.head_id, head.pk)

    def test_normal_head_advancement_and_no_rollback(self):
        first = self.revision()
        self.stream.head = first
        self.stream.save()
        second = self.revision(sequence=2, predecessor=first)
        self.stream.head = second
        self.stream.save()
        self.stream.head = first
        with self.assertRaises(ValidationError):
            self.stream.save()
        self.rejects_sql("UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s", [first.pk.hex, self.stream.pk.hex])

    def test_chain_validation_sql_and_unique_keys(self):
        first = self.revision()
        foreign = self.revision(stream=self.other)
        for changes in ({"sequence": 0}, {"sequence": 2}, {"sequence": 3, "predecessor_id": first.pk}, {"sequence": 2, "predecessor_id": foreign.pk}, {"sequence": 1, "predecessor_id": first.pk}, {"stream_id": self.other.pk, "idempotency_key": foreign.idempotency_key}, {"reason": "   "}, {"source_fingerprint": "x" * 64}, {"operations": "{}"}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                self.raw_insert_revision(**changes)
        with self.assertRaises(ValidationError):
            self.revision(sequence=2, predecessor=foreign)
        # Use an otherwise valid successor so JSON/digest/reason guards are not
        # accidentally tested only through the duplicate-root constraint.
        for changes in ({"reason": "   "}, {"source_fingerprint": "x" * 64}, {"request_fingerprint": "short"}, {"operations": "{}"}, {"review_values": "[]"}, {"metadata": "[]"}, {"schema_version": 0}, {"author_identifier": 999}, {"author_id": None}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                self.raw_insert_revision(**{"sequence": 2, "predecessor_id": first.pk, **changes})
        # Successful raw insertion demonstrates that the valid SQL shape works.
        self.raw_insert_revision(sequence=2, predecessor_id=first.pk)
        second = BoxHistoryRevision.objects.get(stream=self.stream, sequence=2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.raw_insert_revision(sequence=3, predecessor_id=second.pk, idempotency_key=first.idempotency_key)
        with self.assertRaises(ValidationError):
            self.revision(sequence=2, predecessor=first)

    def test_scope_immutable_in_sql_and_orm(self):
        for column, value in (("organization_id", self.other_org.pk), ("original_box_id", 9999), ("id", uuid4().hex)):
            self.rejects_sql(f"UPDATE cultures_boxhistorystream SET {column} = %s WHERE id = %s", [value, self.stream.pk.hex])
        self.stream.organization = self.other_org
        with self.assertRaises(ValidationError):
            self.stream.save()
        self.rejects_sql("UPDATE cultures_boxhistorystream SET box_id = %s WHERE id = %s", [self.box.pk, self.other.pk.hex])
        self.rejects_sql("DELETE FROM cultures_boxhistorystream WHERE id = %s", [self.other.pk.hex])

    def test_identity_shapes_and_source_uniqueness(self):
        first = BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="persisted", source_key="measurement:1")
        with self.assertRaises(ValidationError):
            BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="persisted", source_key=first.source_key)
        BoxHistoryEventIdentity.objects.create(stream=self.other, kind="persisted", source_key=first.source_key)
        BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="derived", locator={"iso_week": 1, "iso_year": 2026}, source_fingerprint="a" * 64)
        for kwargs in ({"kind": "persisted", "source_key": " "}, {"kind": "derived"}, {"kind": "inserted", "source_key": "browser-id"}, {"kind": "unknown"}, {"kind": "inserted", "locator": {"iso_week": 1}}):
            with self.assertRaises(ValidationError):
                BoxHistoryEventIdentity.objects.create(stream=self.stream, **kwargs)
        for kind, key in (("persisted", " "), ("inserted", "browser-id"), ("unknown", "")):
            self.rejects_sql("INSERT INTO cultures_boxhistoryeventidentity (id, stream_id, kind, source_key, derived_source_key, locator, source_fingerprint, source_snapshot) VALUES (%s, %s, %s, %s, '', '{}', '', '{}')", [uuid4().hex, self.stream.pk.hex, kind, key])

    def test_derived_identity_key_is_canonical_server_computed_and_scoped(self):
        locator = {"policy": "transition", "origin_reading_id": 1, "destination_reading_id": 2}
        first = BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="derived", locator=locator, source_fingerprint="a" * 64, derived_source_key="f" * 64)
        canonical = json.dumps({"locator": locator, "source_fingerprint": "a" * 64}, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        self.assertEqual(first.derived_source_key, hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        self.assertNotEqual(first.derived_source_key, "f" * 64)
        self.assertEqual(first.source_key, "")
        self.assertEqual(first.locator, locator)
        self.assertEqual(first.source_fingerprint, "a" * 64)
        reordered = dict(reversed(list(locator.items())))
        duplicate = BoxHistoryEventIdentity(stream=self.stream, kind="derived", locator=reordered, source_fingerprint="a" * 64)
        duplicate.clean()
        self.assertEqual(duplicate.derived_source_key, first.derived_source_key)
        with self.assertRaises(ValidationError):
            duplicate.save()
        for changes in ({"locator": dict(locator, destination_reading_id=3)}, {"source_fingerprint": "b" * 64}, {"stream": self.other}):
            BoxHistoryEventIdentity.objects.create(**{"stream": self.stream, "kind": "derived", "locator": locator, "source_fingerprint": "a" * 64, **changes})
        self.assertEqual(BoxHistoryEventIdentity.objects.count(), 4)

    def test_derived_identity_sql_uniqueness_and_key_shape(self):
        identity = BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="derived", locator={"reading": 1}, source_fingerprint="a" * 64)
        query = "INSERT INTO cultures_boxhistoryeventidentity (id, stream_id, kind, source_key, derived_source_key, locator, source_fingerprint, source_snapshot) VALUES (%s, %s, %s, '', %s, %s, %s, '{}')"
        # Different locator JSON cannot evade the explicit stream/key index.
        self.rejects_sql(query, [uuid4().hex, self.stream.pk.hex, "derived", identity.derived_source_key, '{"reading":2}', "a" * 64])
        for key in ("", "a" * 63, "a" * 65, "A" * 64, "x" * 64):
            self.rejects_sql(query, [uuid4().hex, self.stream.pk.hex, "derived", key, '{"reading":2}', "a" * 64])
        self.rejects_sql(query, [uuid4().hex, self.stream.pk.hex, "inserted", "a" * 64, '{}', ""])
        for kind in ("persisted", "inserted"):
            with self.assertRaises(ValidationError):
                BoxHistoryEventIdentity.objects.create(stream=self.stream, kind=kind, source_key="measurement:1" if kind == "persisted" else "", derived_source_key="a" * 64)
        self.sql(query, [uuid4().hex, self.other.pk.hex, "derived", identity.derived_source_key, '{"reading":1}', "a" * 64])
        # Deliberately do not claim SQL recomputes the canonical hash.
        self.sql(query, [uuid4().hex, self.stream.pk.hex, "derived", "c" * 64, '{"reading":3}', "a" * 64])

    def test_raw_sql_head_recovers_through_preinserted_successors(self):
        first = self.revision()
        self.sql("UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s", [first.pk.hex, self.stream.pk.hex])
        second = self.revision(sequence=2, predecessor=first)
        third = self.revision(sequence=3, predecessor=second)
        self.rejects_sql("UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s", [third.pk.hex, self.stream.pk.hex])
        for head in (second, third):
            self.sql("UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s", [head.pk.hex, self.stream.pk.hex])
            self.stream.refresh_from_db()
            self.assertEqual(self.stream.head_id, head.pk)
        for head in (None, first, second):
            self.rejects_sql("UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s", [head.pk.hex if head else None, self.stream.pk.hex])
            self.stream.head = head
            with self.assertRaises(ValidationError):
                self.stream.save()

    def test_conservative_json_validation(self):
        for kwargs in ({"operations": {}}, {"operations": [0]}, {"operations": [{"raw_payload": "hidden"}]}, {"review_values": {"password": "hidden"}}, {"metadata": {"producer": {"nested": 1}}}, {"metadata": {"unknown": 1}}, {"review_values": {"polyp_count": float("nan")}}, {"reason": " "}, {"source_fingerprint": "A" * 64}, {"operations": [{"value": "a" * 1001}]}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValidationError):
                self.revision(**kwargs)

    def test_detachment_with_unpublished_successor_and_no_reattachment(self):
        first = self.revision()
        self.stream.head = first
        self.stream.save()
        self.revision(sequence=2, predecessor=first)
        box_id = self.box.pk
        self.stream.box = None
        self.stream.save()
        self.rejects_sql("UPDATE cultures_boxhistorystream SET box_id = %s WHERE id = %s", [box_id, self.stream.pk.hex])
        self.stream.box = self.box
        with self.assertRaises(ValidationError):
            self.stream.save()

    def test_staged_root_can_initialize_without_skipping_and_insert_scope_is_guarded(self):
        first = self.revision()
        second = self.revision(sequence=2, predecessor=first)
        self.rejects_sql("UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s", [second.pk.hex, self.stream.pk.hex])
        self.stream.head = second
        with self.assertRaises(ValidationError):
            self.stream.save()
        self.sql("UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s", [first.pk.hex, self.stream.pk.hex])
        self.stream.refresh_from_db()
        self.stream.head = second
        self.stream.save()
        self.stream.box = None
        self.stream.save()
        for org_id, original_id, box_id, head_id in ((self.other_org.pk, self.box.pk, self.box.pk, None), (self.org.pk, 9999, self.box.pk, None), (self.org.pk, 9999, None, first.pk.hex), (self.org.pk, 0, None, None), (self.org.pk, self.box.pk, None, None), (self.org.pk, 999999, None, None)):
            self.rejects_sql("INSERT INTO cultures_boxhistorystream (id, organization_id, original_box_id, box_id, head_id) VALUES (%s, %s, %s, %s, %s)", [uuid4().hex, org_id, original_id, box_id, head_id])

    def test_author_nulling_is_the_only_permitted_sql_update(self):
        revision = self.revision()
        other_user = get_user_model().objects.create_user(username="other-history-author", email="other-history@example.test")
        self.rejects_sql("UPDATE cultures_boxhistoryrevision SET author_id = %s WHERE id = %s", [other_user.pk, revision.pk.hex])
        self.sql("UPDATE cultures_boxhistoryrevision SET author_id = NULL WHERE id = %s", [revision.pk.hex])
        revision.refresh_from_db()
        self.assertEqual(revision.author_identifier, self.user.pk)
        self.assertEqual(revision.author_label, "Stored Author")
        self.rejects_sql("UPDATE cultures_boxhistoryrevision SET author_id = %s WHERE id = %s", [self.user.pk, revision.pk.hex])

    def snapshot(self, temporal=None, **changes):
        return {
            "event_key": "box_movement:1", "kind": "movement",
            "temporal": temporal or {"precision": "exact", "instant": "2026-11-01T10:00:00+01:00"},
            "origin_zone_id": 1, "destination_zone_id": 2,
            "source_fingerprint": "a" * 64,
            "sources": [{"source": "historical_temperature_reading", "source_id": 1,
                         "role": "observation", "basis": "recorded", "exclusive": False}],
            **changes,
        }

    def test_exact_and_uncertain_per_event_snapshots_round_trip(self):
        temporal_values = [
            {"precision": "exact", "instant": "2026-10-25T02:30:00+02:00"},
            {"precision": "recorded_boundary", "instant": "2026-10-25T02:30:00+01:00"},
            {"precision": "observation_interval", "not_before": "2026-10-01T00:00:00Z", "before": "2026-11-01T00:00:00Z", "ordering_anchor": "2026-10-15T00:00:00Z"},
            {"precision": "iso_week", "iso_year": 2026, "iso_week": 44, "not_before": "2026-10-26T00:00:00+01:00", "before": "2026-11-02T00:00:00+01:00"},
            {"precision": "unknown", "instant": None, "not_before": "2026-10-01T00:00:00Z", "before": None, "ordering_anchor": None},
        ]
        events = [self.snapshot(temporal, event_key=f"box_movement:{index + 1}") for index, temporal in enumerate(temporal_values)]
        revision = self.revision(review_values={"events": events})
        revision.refresh_from_db()
        self.assertEqual(revision.review_values, {"events": events})
        self.assertFalse(revision.review_values["events"][0]["sources"][0]["exclusive"])
        identity = BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="derived", locator={"policy": "historical_transition", "origin_reading_id": 1, "destination_reading_id": 2, "historical_temperature_reading": 3}, source_fingerprint="a" * 64, source_snapshot=events[2])
        identity.refresh_from_db()
        self.assertEqual(identity.source_snapshot, events[2])
        self.assertEqual(identity.locator["historical_temperature_reading"], 3)

    def test_snapshot_temporal_and_source_shape_rejections(self):
        invalid_temporals = [
            {"precision": "exact", "instant": "2026-11-01T12:00:00"},
            {"precision": "exact", "instant": "not-a-date"},
            {"precision": "exact"}, {"precision": []},
            {"precision": "unknown", "instant": "2026-11-01T12:00:00Z"},
            {"precision": "observation_interval", "not_before": "2026-11-01T12:00:00Z"},
            {"precision": "observation_interval", "not_before": "2026-11-02T12:00:00Z", "before": "2026-11-01T12:00:00Z"},
            {"precision": "iso_week", "iso_year": 2026, "iso_week": 99},
            {"precision": "unknown", "iso_year": 2026},
        ]
        snapshots = [self.snapshot(temporal) for temporal in invalid_temporals]
        snapshots += [self.snapshot(origin_zone_id=0), self.snapshot(destination_zone_id=True), self.snapshot(source_fingerprint="x" * 64), self.snapshot(sources=[{"source": "table", "source_id": 1, "role": "event", "exclusive": 0}]), self.snapshot(raw_data={"temperature": 10}), self.snapshot(sources=[{"source": "table", "source_id": 1, "role": "event", "thermal_snapshot": {"readings": []}}])]
        for event in snapshots:
            with self.subTest(event=event):
                with self.assertRaises(ValidationError):
                    self.revision(review_values={"events": [event]})
                with self.assertRaises(ValidationError):
                    BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="inserted", source_snapshot=event)
        with self.assertRaises(ValidationError):
            self.revision(review_values={"events": [self.snapshot()] * 101})

    def test_phase_three_temporal_rejections_prevent_storage(self):
        invalid = [
            {"precision": "exact", "instant": "2026-11-01T12:00:00Z", "ordering_anchor": "2026-11-01T12:00:00Z"},
            {"precision": "recorded_boundary", "instant": "2026-11-01T12:00:00Z", "ordering_anchor": "2026-11-01T13:00:00Z"},
            {"precision": "iso_week", "iso_year": 2026, "iso_week": 44, "not_before": "2026-10-26T00:00:00Z", "before": "2026-11-02T00:00:00Z"},
            {"precision": "iso_week", "iso_year": 2026, "iso_week": 43, "not_before": "2026-10-19T00:00:00+02:00", "before": "2026-10-26T00:00:00+02:00"},
        ]
        for temporal in invalid:
            with self.subTest(temporal=temporal):
                with self.assertRaises(ValidationError):
                    self.revision(operations=[{"type": "adjust_event_temporal", "target": {"key": "box_movement:1"}, "temporal": temporal}])
                with self.assertRaises(ValidationError):
                    self.revision(review_values={"events": [self.snapshot(temporal)]})
                with self.assertRaises(ValidationError):
                    BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="inserted", source_snapshot=self.snapshot(temporal))
        self.assertFalse(BoxHistoryRevision.objects.exists())
        self.assertFalse(BoxHistoryEventIdentity.objects.exists())

    def test_paris_dst_week_and_equivalent_utc_bounds_are_preserved(self):
        paris = {"precision": "iso_week", "iso_year": 2026, "iso_week": 43, "not_before": "2026-10-19T00:00:00+02:00", "before": "2026-10-26T00:00:00+01:00"}
        utc = {**paris, "not_before": "2026-10-18T22:00:00Z", "before": "2026-10-25T23:00:00Z"}
        events = [self.snapshot(paris), self.snapshot(utc, event_key="box_movement:2")]
        revision = self.revision(review_values={"events": events})
        revision.refresh_from_db()
        self.assertEqual(revision.review_values["events"], events)

    def test_locator_preserves_names_but_rejects_credentials_and_untyped_values(self):
        for locator in ({"api_token": "hidden"}, {"raw_data": "hidden"}, {"source": {"nested": 1}}, {"source": [1]}, {"source": True}, {"source": 0}, {"source": None}, {"source": ""}, {"a" * 81: 1}):
            with self.subTest(locator=locator), self.assertRaises(ValidationError):
                BoxHistoryEventIdentity.objects.create(stream=self.stream, kind="derived", locator=locator, source_fingerprint="a" * 64)

    def test_phase_four_operations_with_backend_identity_keys(self):
        inserted_id, anchor_id = str(uuid4()), str(uuid4())
        temporal = {"precision": "exact", "instant": "2026-11-01T12:00:00Z"}
        operations = [
            {"type": "insert_event", "draft_id": inserted_id, "kind": "movement", "temporal": temporal, "origin_zone_id": 1, "destination_zone_id": 2},
            {"type": "modify_event", "target": {"key": f"draft:{inserted_id}"}, "destination_zone_id": None},
            {"type": "adjust_event_kind", "target": {"key": "box_movement:1"}, "kind": "deactivation"},
            {"type": "adjust_event_temporal", "target": {"key": f"anchor:{anchor_id}", "expected_evidence_fingerprint": "a" * 64}, "temporal": {"precision": "unknown"}},
            {"type": "adjust_event_zones", "target": {"key": "box_location:2", "expected_evidence_fingerprint": None}, "origin_zone_id": None},
        ]
        justifications = [{"operation_index": 0, "reason": "Reviewed source: " + "x" * (MAX_JUSTIFICATION_LENGTH - len("Reviewed source: "))}]
        revision = self.revision(operations=operations, justifications=justifications)
        revision.refresh_from_db()
        self.assertEqual(revision.operations, operations)
        self.assertEqual(revision.justifications, justifications)
        self.assertNotIn("origin_zone_id", revision.operations[1])
        self.assertEqual(revision.operations[3]["temporal"], {"precision": "unknown"})

    def test_operation_shape_and_uuid_rejections(self):
        valid = {"type": "insert_event", "draft_id": str(uuid4()), "kind": "movement", "temporal": {"precision": "exact", "instant": "2026-11-01T12:00:00Z"}}
        invalid = [dict(valid, draft_id="browser-draft-1"), dict(valid, raw_data={}), dict(valid, kind="unknown-kind"), dict(valid, temporal={"precision": "exact", "instant": "2026-11-01T12:00:00"}), {"type": "adjust_event_kind", "target": {"key": "draft:browser-draft-1"}, "kind": "movement"}, {"type": "adjust_event_temporal", "target": {"key": "anchor:derived-label"}, "temporal": {"precision": "unknown"}}, {"type": "modify_event", "target": {"key": "box_movement:1", "extra": True}}, {"type": "adjust_event_zones", "target": {"key": "box_movement:1"}, "origin_zone_id": False}]
        missing = deepcopy(valid)
        del missing["temporal"]
        invalid.append(missing)
        for operation in invalid:
            with self.subTest(operation=operation), self.assertRaises(ValidationError):
                self.revision(operations=[operation])

    def test_operation_limit_in_model_and_database(self):
        operation = {"type": "adjust_event_zones", "target": {"key": "box_movement:1"}, "origin_zone_id": None}
        revision = self.revision(operations=[operation] * 100)
        self.assertEqual(len(revision.operations), 100)
        with self.assertRaises(ValidationError):
            self.revision(sequence=2, predecessor=revision, operations=[operation] * 101)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.raw_insert_revision(sequence=2, predecessor_id=revision.pk, operations=json.dumps([operation] * 101))

    def test_justifications_default_and_required_for_insertions(self):
        self.assertEqual(BoxHistoryRevision().justifications, [])
        insertion = {"type": "insert_event", "draft_id": str(uuid4()), "kind": "movement", "temporal": {"precision": "unknown"}}
        with self.assertRaisesMessage(ValidationError, "insertion requires"):
            self.revision(operations=[insertion])
        operation = {"type": "adjust_event_zones", "target": {"key": "box_movement:1"}, "origin_zone_id": None}
        with self.assertRaisesMessage(ValidationError, "insertion requires"):
            self.revision(operations=[operation, insertion], justifications=[{"operation_index": 0, "reason": "Not the insertion"}])
        revision = self.revision(operations=[operation, insertion], justifications=[{"operation_index": 1, "reason": "Source reviewed"}])
        revision.refresh_from_db()
        self.assertEqual(revision.justifications, [{"operation_index": 1, "reason": "Source reviewed"}])

    def test_justification_exact_shape_indices_and_reason_validation(self):
        operation = {"type": "adjust_event_kind", "target": {"key": "box_movement:1"}, "kind": "movement"}
        invalid = [
            {}, [None], [{}], [{"operation_index": 0}], [{"reason": "Source"}],
            [{"operation_index": 0, "reason": "Source", "extra": True}],
            *[[{"operation_index": index, "reason": "Source"}] for index in (True, 0.0, "0", -1, 1, None)],
            *[[{"operation_index": 0, "reason": reason}] for reason in (None, False, 42, "", " \n\t", "x" * (MAX_JUSTIFICATION_LENGTH + 1))],
            [{"operation_index": 0, "reason": "One"}, {"operation_index": 0, "reason": "Two"}],
        ]
        for justifications in invalid:
            with self.subTest(justifications=justifications), self.assertRaises(ValidationError):
                self.revision(operations=[operation], justifications=justifications)
        with self.assertRaises(ValidationError):
            self.revision(justifications=[{"operation_index": 0, "reason": "No operation"}])

    def test_justification_limit_and_technical_byte_cap(self):
        operation = {"type": "adjust_event_kind", "target": {"key": "box_movement:1"}, "kind": "movement"}
        operations = [operation] * MAX_OPERATIONS
        justifications = [{"operation_index": index, "reason": "Reviewed"} for index in range(MAX_JUSTIFICATIONS)]
        revision = self.revision(operations=operations, justifications=justifications)
        revision.refresh_from_db()
        self.assertEqual(revision.justifications, justifications)
        with self.assertRaises(ValidationError):
            self.revision(sequence=2, predecessor=revision, operations=operations, justifications=justifications + [{"operation_index": 0, "reason": "Extra"}])
        with self.assertRaisesMessage(ValidationError, "storage limit"):
            self.revision(sequence=2, predecessor=revision, operations=operations, justifications=[{"operation_index": index, "reason": "x" * MAX_JUSTIFICATION_LENGTH} for index in range(MAX_JUSTIFICATIONS)])

    def test_sql_justification_container_and_count_guards(self):
        for justifications in ("{}", "null", "0", json.dumps([{}] * (MAX_JUSTIFICATIONS + 1))):
            with self.subTest(justifications=justifications), self.assertRaises(IntegrityError), transaction.atomic():
                self.raw_insert_revision(justifications=justifications)
        # SQL guards only cover container/count; indexed reason syntax is model
        # validation and the future decoder's responsibility.
        self.raw_insert_revision(justifications=json.dumps([{}] * MAX_JUSTIFICATIONS))

    def test_detached_stream_still_reserves_original_box_identity(self):
        self.stream.box = None
        self.stream.save()
        with self.assertRaises(ValidationError):
            BoxHistoryStream.objects.create(organization=self.org, box=self.box)
        self.rejects_sql("INSERT INTO cultures_boxhistorystream (id, organization_id, original_box_id, box_id, head_id) VALUES (%s, %s, %s, %s, NULL)", [uuid4().hex, self.org.pk, self.box.pk, self.box.pk])
        with connection.cursor() as cursor:
            constraints = connection.introspection.get_constraints(cursor, "cultures_boxhistorystream")
        constraint = constraints["history_stream_original_unique"]
        self.assertTrue(constraint["unique"])
        self.assertEqual(constraint["columns"], ["organization_id", "original_box_id"])

    def test_new_stream_requires_live_box_in_model_and_database(self):
        for original_id in (self.box.pk, 999999):
            with self.subTest(original_id=original_id):
                with self.assertRaises(ValidationError):
                    BoxHistoryStream.objects.create(organization=self.org, original_box_id=original_id)
                self.rejects_sql("INSERT INTO cultures_boxhistorystream (id, organization_id, original_box_id, box_id, head_id) VALUES (%s, %s, %s, NULL, NULL)", [uuid4().hex, self.org.pk, original_id])
        self.rejects_sql("INSERT INTO cultures_boxhistorystream (id, organization_id, original_box_id, box_id, head_id) VALUES (%s, %s, %s, %s, NULL)", [uuid4().hex, self.org.pk, 999999, 999999])

    def test_guards_are_installed_and_reversible(self):
        guards = import_module("apps.cultures.migrations.0020_history_revision_guards")
        from django.apps import apps
        editor = connection.SchemaEditorClass(connection, atomic=False)
        guards.remove_guards(apps, editor)
        guards.install_guards(apps, editor)
        self.rejects_sql("DELETE FROM cultures_boxhistorystream WHERE id = %s", [self.other.pk.hex])

    def test_postgresql_guard_generation_without_connection(self):
        # SQL generation coverage only; not PostgreSQL execution or lock proof.
        from django.apps import apps
        guards = import_module("apps.cultures.migrations.0020_history_revision_guards")
        editor = Mock()
        editor.connection.vendor = "postgresql"
        editor.quote_name.side_effect = lambda value: '"' + value + '"'
        guards.install_guards(apps, editor)
        sql = "\n".join(call.args[0] for call in editor.execute.call_args_list)
        self.assertNotIn("TRUNCATE", sql)
        self.assertIn("BEFORE UPDATE", sql)
        self.assertIn("BEFORE DELETE", sql)
        self.assertIn("NEW.justifications IS DISTINCT FROM OLD.justifications", sql)
        self.assertIn("jsonb_array_length(NEW.justifications) > 100", sql)
        guards.remove_guards(apps, editor)
        self.assertNotIn("TRUNCATE", "\n".join(call.args[0] for call in editor.execute.call_args_list))
        self.assertIn("IS DISTINCT FROM", sql)
        self.assertIn("NEW.author_id IS NULL", sql)
