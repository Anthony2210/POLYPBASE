"""Phase 5A synthetic PostgreSQL transaction proofs, not a publication service.

Run the existing storage/codec/composition suites separately in the CI workflow.
These tests add real commit, deferred-FK and independent-connection race coverage;
they do not define authorization or activate overlay consumption by live readers.
The workflow owns disposable-database validation and evidence collection.
"""

from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
from queue import Queue
import threading
import time
from unittest import skipUnless
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from . import test_history_preview_service as source_fixtures
from .effective_history import AdjustEventZones, EventTarget
from .effective_history_sources import load_box_source_history
from .history_revision_codec import decode_revision, encode_revision
from .history_revision_composition import HistoricalRevision, compose_revisions, review_event
from .models import (
    MOVEMENT_WEEK_TIME_ZONE, Box, BoxHistoryEventIdentity, BoxHistoryRevision,
    BoxHistoryStream, BoxMovement, ThermalZone,
)


class InjectedFailure(RuntimeError):
    pass


def sqlstate(error):
    cause = error.__cause__ or error
    return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)


@skipUnless(connection.vendor == "postgresql", "Requires disposable PostgreSQL, not SQLite.")
class HistoryRevisionPostgresCITests(TransactionTestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Synthetic revision CI laboratory")
        species = Species.objects.create(scientific_name="Synthetic revision species")
        strain = Strain.objects.create(organization=self.org, species=species, code="REV-CI")
        self.box = Box.objects.create(
            organization=self.org, strain=strain, global_code="REV-CI.001", box_number="001",
        )
        self.user = get_user_model().objects.create_user(
            username="revision-ci-author", email="revision-ci@example.test",
        )
        self.stream = BoxHistoryStream.objects.create(organization=self.org, box=self.box)
        # Bound coordinator SQL too, including the catalog and lock-wait probes.
        with connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '10s'")
            cursor.execute("SET statement_timeout = '15s'")
        self.addCleanup(self.reset_timeouts)

    def reset_timeouts(self):
        with connection.cursor() as cursor:
            cursor.execute("RESET lock_timeout")
            cursor.execute("RESET statement_timeout")

    def execute(self, query, params=()):
        with connection.cursor() as cursor:
            cursor.execute(query, params)
            return cursor.rowcount

    def insert_revision(self, *, sequence=1, predecessor=None, revision_id=None,
                        request_id=None, stream_id=None):
        """Bypass full_clean so the database, not a preflight query, arbitrates."""
        revision_id = revision_id or uuid4()
        fields = {
            "id": revision_id, "stream_id": stream_id or self.stream.pk,
            "sequence": sequence, "predecessor_id": predecessor,
            "author_id": self.user.pk, "author_identifier": self.user.pk,
            "author_label": "Synthetic author", "created_at": "2026-11-01 12:00:00+00",
            "reason": "Synthetic CI decision", "operations": json.dumps([]),
            "justifications": json.dumps([]), "review_values": json.dumps({}),
            "metadata": json.dumps({}), "schema_version": 1,
            "source_fingerprint": "a" * 64, "request_fingerprint": "b" * 64,
            "idempotency_key": request_id or uuid4(),
        }
        self.execute(
            f"INSERT INTO cultures_boxhistoryrevision ({', '.join(fields)}) "
            f"VALUES ({', '.join(['%s'] * len(fields))})", list(fields.values()),
        )
        return revision_id

    def head(self, revision_id):
        self.assertEqual(self.execute(
            "UPDATE cultures_boxhistorystream SET head_id = %s WHERE id = %s",
            [revision_id, self.stream.pk],
        ), 1)

    def root(self):
        revision_id = self.insert_revision()
        self.head(revision_id)
        return revision_id

    def race(self, first, second, *, rollback_first=False):
        """Observe the second backend blocked by the first, then release it.

        No executor context manager: its implicit shutdown can wait forever.
        Each worker owns a fresh thread-local Django connection and closes it.
        """
        entered, release, attempted = threading.Event(), threading.Event(), threading.Event()
        pids, results = {}, Queue()

        def worker(index, action):
            db = connections["default"]
            try:
                db.close()
                db.settings_dict = deepcopy(db.settings_dict)
                options = db.settings_dict.setdefault("OPTIONS", {})
                options["connect_timeout"] = 5
                options["options"] = (
                    options.get("options", "")
                    + " -c lock_timeout=10000 -c statement_timeout=15000"
                    + " -c idle_in_transaction_session_timeout=20000"
                ).strip()
                with db.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    pids[index] = cursor.fetchone()[0]
                with transaction.atomic():
                    if index == 1:
                        attempted.set()
                    value = action()
                    if index == 0:
                        entered.set()
                        if not release.wait(15):
                            raise TimeoutError("First revision writer was not released")
                        if rollback_first:
                            raise InjectedFailure("Roll back the first revision writer")
                results.put((index, value))
            except Exception as error:
                results.put((index, error))
            finally:
                db.close()

        workers = [threading.Thread(target=worker, args=(i, action), daemon=True)
                   for i, action in enumerate((first, second))]
        started = []
        try:
            workers[0].start()
            started.append(workers[0])
            self.assertTrue(entered.wait(8), "First writer failed before reaching its barrier")
            workers[1].start()
            started.append(workers[1])
            self.assertTrue(attempted.wait(8), "Second writer did not connect and attempt SQL")
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                coordinator = cursor.fetchone()[0]
            self.assertEqual(len({coordinator, *pids.values()}), 3,
                             "Race requires three distinct PostgreSQL sessions")
            deadline = time.monotonic() + 5
            blocked = False
            while time.monotonic() < deadline:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [pids[0], pids[1]])
                    blocked = cursor.fetchone()[0]
                if blocked or not workers[1].is_alive():
                    break
                time.sleep(0.02)
            self.assertTrue(blocked, "Second writer never waited on the first backend")
        finally:
            release.set()
            deadline = time.monotonic() + 25
            for thread in started:
                thread.join(timeout=max(0, deadline - time.monotonic()))
            self.assertFalse(any(thread.is_alive() for thread in started),
                             "Revision worker exceeded bounded join; server timeouts must terminate SQL")
            # Never treat deadlock, lock timeout or statement cancellation as an
            # expected uniqueness/guard conflict, even on a failed barrier path.
            outcomes = dict(results.get_nowait() for _ in range(results.qsize()))
            for index, outcome in outcomes.items():
                if isinstance(outcome, Exception):
                    self.assertNotIn(sqlstate(outcome), {"40P01", "55P03", "57014"},
                                     f"Worker {index} deadlocked or timed out: {outcome!r}")
        self.assertEqual(set(outcomes), {0, 1})
        self._race_observation = {
            "backend_pids": {"coordinator": coordinator, "first": pids[0], "second": pids[1]},
            "observed_blocking": blocked,
            "blocker_pid": pids[0],
            "blocked_pid": pids[1],
            "worker_sqlstates": {
                name: sqlstate(outcomes[index]) if isinstance(outcomes[index], Exception) else None
                for index, name in enumerate(("first", "second"))
            },
        }
        return outcomes[0], outcomes[1]

    def write_concurrency_evidence(self, *, outcome, winner, worker_outcomes):
        """Call last, after persisted assertions; tests.json remains authoritative."""
        directory = os.environ.get("HR_CI_EVIDENCE_DIR")
        if not directory:
            return
        committed = {
            "revision_count": BoxHistoryRevision.objects.filter(stream=self.stream).count(),
            "head_id": str(BoxHistoryStream.objects.get(pk=self.stream.pk).head_id),
        }
        evidence = {
            "test_id": self.id(),
            "status": "persisted_assertions_passed",
            "suite_result_authority": "tests.json",
            **self._race_observation,
            "committed": committed,
            "outcome": outcome,
            "winner": winner,
            "worker_outcomes": worker_outcomes,
        }
        path = Path(directory) / f"concurrency-{self._testMethodName}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2, sort_keys=True, allow_nan=False) + "\n",
                        encoding="utf-8")

    def assert_integrity(self, outcome, state):
        self.assertIsInstance(outcome, IntegrityError, repr(outcome))
        self.assertEqual(sqlstate(outcome), state, repr(outcome))

    def test_concurrent_successors_commit_exactly_one_and_persist_its_head(self):
        root = self.root()
        winner, loser = uuid4(), uuid4()

        def publish(revision_id):
            self.insert_revision(sequence=2, predecessor=root, revision_id=revision_id)
            self.head(revision_id)
            return revision_id

        first, second = self.race(lambda: publish(winner), lambda: publish(loser))
        self.assertEqual(first, winner)
        self.assert_integrity(second, "23505")
        self.stream.refresh_from_db()
        self.assertEqual(self.stream.head_id, winner)
        self.assertEqual(list(BoxHistoryRevision.objects.filter(stream=self.stream)
                              .order_by("sequence").values_list("id", "predecessor_id")),
                         [(root, None), (winner, root)])
        self.assertFalse(BoxHistoryRevision.objects.filter(pk=loser).exists())
        self.write_concurrency_evidence(
            outcome="first_successor_committed_second_rejected", winner="first",
            worker_outcomes={"first": "committed", "second": "rejected_and_rolled_back"},
        )

    def test_failed_first_successor_releases_unique_slots_for_waiting_writer(self):
        root = self.root()
        failed, winner, request_id = uuid4(), uuid4(), uuid4()

        def publish(revision_id):
            self.insert_revision(sequence=2, predecessor=root, revision_id=revision_id,
                                 request_id=request_id)
            self.head(revision_id)
            return revision_id

        first, second = self.race(lambda: publish(failed), lambda: publish(winner), rollback_first=True)
        self.assertIsInstance(first, InjectedFailure, repr(first))
        self.assertEqual(second, winner)
        self.stream.refresh_from_db()
        self.assertEqual(self.stream.head_id, winner)
        self.assertFalse(BoxHistoryRevision.objects.filter(pk=failed).exists())
        stored = BoxHistoryRevision.objects.get(stream=self.stream, sequence=2)
        self.assertEqual((stored.pk, stored.predecessor_id, stored.idempotency_key),
                         (winner, root, request_id))
        self.assertEqual(BoxHistoryRevision.objects.filter(stream=self.stream).count(), 2)
        self.write_concurrency_evidence(
            outcome="first_successor_rolled_back_second_committed", winner="second",
            worker_outcomes={"first": "injected_failure_rolled_back", "second": "committed"},
        )

    def test_concurrent_head_updates_advance_against_committed_predecessor(self):
        root = self.root()
        second = self.insert_revision(sequence=2, predecessor=root)
        third = self.insert_revision(sequence=3, predecessor=second)
        one, two = self.race(lambda: self.head(second), lambda: self.head(third))
        self.assertIsNone(one)
        self.assertIsNone(two)
        self.stream.refresh_from_db()
        self.assertEqual(self.stream.head_id, third)
        self.assertEqual(BoxHistoryRevision.objects.filter(stream=self.stream).count(), 3)
        self.write_concurrency_evidence(
            outcome="both_head_updates_committed_in_order", winner="second",
            worker_outcomes={"first": "committed", "second": "committed"},
        )

    def test_stale_head_update_cannot_rewind_after_waiting_for_commit(self):
        root = self.root()
        second = self.insert_revision(sequence=2, predecessor=root)
        third = self.insert_revision(sequence=3, predecessor=second)

        def advance_twice():
            self.head(second)
            self.head(third)

        one, two = self.race(advance_twice, lambda: self.head(second))
        self.assertIsNone(one)
        self.assert_integrity(two, "23514")
        self.stream.refresh_from_db()
        self.assertEqual(self.stream.head_id, third)
        self.assertEqual(BoxHistoryRevision.objects.filter(stream=self.stream).count(), 3)
        self.write_concurrency_evidence(
            outcome="advanced_head_committed_stale_rewind_rejected", winner="first",
            worker_outcomes={"first": "committed", "second": "rejected_and_rolled_back"},
        )

    def test_failure_rolls_back_registry_revision_and_head_then_allows_retry(self):
        root = self.root()
        revision_id, identity_id, request_id = uuid4(), uuid4(), uuid4()
        with self.assertRaisesMessage(InjectedFailure, "after head update"):
            with transaction.atomic():
                BoxHistoryEventIdentity.objects.create(id=identity_id, stream=self.stream, kind="inserted")
                self.insert_revision(sequence=2, predecessor=root, revision_id=revision_id,
                                     request_id=request_id)
                self.head(revision_id)
                raise InjectedFailure("after head update")
        self.stream.refresh_from_db()
        self.assertEqual(self.stream.head_id, root)
        self.assertFalse(BoxHistoryRevision.objects.filter(pk=revision_id).exists())
        self.assertFalse(BoxHistoryEventIdentity.objects.filter(pk=identity_id).exists())
        with transaction.atomic():
            BoxHistoryEventIdentity.objects.create(id=identity_id, stream=self.stream, kind="inserted")
            self.insert_revision(sequence=2, predecessor=root, revision_id=revision_id,
                                 request_id=request_id)
            self.head(revision_id)
        self.stream.refresh_from_db()
        self.assertEqual(self.stream.head_id, revision_id)
        self.assertTrue(BoxHistoryEventIdentity.objects.filter(pk=identity_id).exists())
        self.assertEqual(BoxHistoryRevision.objects.get(pk=revision_id).idempotency_key, request_id)

    def test_deferred_revision_stream_fk_fails_at_commit_and_rolls_back_other_writes(self):
        root = self.root()
        orphan, missing_stream, registry = uuid4(), uuid4(), uuid4()
        reached_commit = False
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                self.execute("SET CONSTRAINTS ALL DEFERRED")
                BoxHistoryEventIdentity.objects.create(id=registry, stream=self.stream, kind="inserted")
                self.insert_revision(revision_id=orphan, stream_id=missing_stream)
                self.assertTrue(BoxHistoryRevision.objects.filter(pk=orphan).exists())
                reached_commit = True
        self.assertTrue(reached_commit, "Failure must occur at commit, not during INSERT")
        self.assertEqual(sqlstate(caught.exception), "23503")
        self.assertFalse(BoxHistoryRevision.objects.filter(pk=orphan).exists())
        self.assertFalse(BoxHistoryEventIdentity.objects.filter(pk=registry).exists())
        self.stream.refresh_from_db()
        self.assertEqual(self.stream.head_id, root)

    def test_deferred_identity_fk_can_be_repaired_before_commit(self):
        new_stream, identity_id = uuid4(), uuid4()
        box = Box.objects.create(organization=self.org, strain=self.box.strain,
                                 global_code="REV-CI.002", box_number="002")
        with transaction.atomic():
            # Exercise Django's initially deferred FK without changing its mode.
            self.execute(
                "INSERT INTO cultures_boxhistoryeventidentity "
                "(id, stream_id, kind, source_key, locator, derived_source_key, "
                "source_fingerprint, source_snapshot) VALUES (%s, %s, 'inserted', '', '{}', '', '', '{}')",
                [identity_id, new_stream],
            )
            self.assertTrue(BoxHistoryEventIdentity.objects.filter(pk=identity_id).exists())
            BoxHistoryStream.objects.create(id=new_stream, organization=self.org, box=box)
            self.execute("SET CONSTRAINTS ALL IMMEDIATE")
        self.assertEqual(BoxHistoryEventIdentity.objects.get(pk=identity_id).stream_id, new_stream)
        self.assertEqual(BoxHistoryStream.objects.get(pk=new_stream).box_id, box.pk)

    def test_deferred_fk_does_not_defer_structural_predecessor_guard(self):
        root = self.root()
        invalid = uuid4()
        with self.assertRaises(IntegrityError) as caught:
            with transaction.atomic():
                self.execute("SET CONSTRAINTS ALL DEFERRED")
                self.insert_revision(sequence=2, predecessor=uuid4(), revision_id=invalid)
        self.assertEqual(sqlstate(caught.exception), "23514")
        self.assertFalse(BoxHistoryRevision.objects.filter(pk=invalid).exists())
        self.stream.refresh_from_db()
        self.assertEqual(self.stream.head_id, root)


@skipUnless(connection.vendor == "postgresql", "Requires disposable PostgreSQL, not SQLite.")
class HistoryRevisionSourcePreservationPostgresCITests(TransactionTestCase):
    # Reuse only synthetic fixture builders, not another class's test methods.
    make_box = source_fixtures.HistoryPreviewServiceTests.make_box
    probe = source_fixtures.HistoryPreviewServiceTests.probe
    raw = source_fixtures.HistoryPreviewServiceTests.raw
    snapshot = source_fixtures.HistoryPreviewServiceTests.snapshot

    def setUp(self):
        self.org = Organization.objects.create(name="Synthetic revision source laboratory")
        self.admin = get_user_model().objects.create_user(
            username="revision-source-admin", email="revision-source-admin@example.test",
        )
        self.membership = OrganizationMembership.objects.create(
            user=self.admin, organization=self.org, role=OrganizationMembership.Role.ADMIN,
        )
        species = Species.objects.create(scientific_name="Revision source synthetic", genus_species_code="RVS")
        self.strain = Strain.objects.create(
            species=species, code="RVS-LAB-1", organization=self.org, number=1, origin_code="LAB",
        )
        self.a = ThermalZone.objects.create(organization=self.org, name="A", target_temperature_c=Decimal("15"))
        self.b = ThermalZone.objects.create(organization=self.org, name="B", target_temperature_c=Decimal("0"))
        self.c = ThermalZone.objects.create(organization=self.org, name="C", target_temperature_c=Decimal("10"))
        self.box = self.make_box("001", status=Box.Status.ACTIVE, thermal_zone=self.b)
        self.start = datetime(2026, 2, 9, tzinfo=MOVEMENT_WEEK_TIME_ZONE)
        self.moved_at = self.start + timedelta(days=2, hours=12)
        self.later = self.moved_at + timedelta(days=2)
        self.opening = BoxMovement.objects.create(
            box=self.box, to_thermal_zone=self.a, moved_at=self.start,
            kind=BoxMovement.Kind.INITIAL_LOCATION, user=self.admin,
        )
        self.movement = BoxMovement.objects.create(
            box=self.box, from_thermal_zone=self.a, to_thermal_zone=self.b,
            moved_at=self.moved_at, kind=BoxMovement.Kind.ORDINARY, user=self.admin,
        )
        self.target = EventTarget(f"movement:{self.movement.pk}")
        self.readings = []
        for zone in (self.a, self.b, self.c):
            probe = self.probe(f"zone-{zone.pk}", zone=zone)
            for instant in (self.moved_at - timedelta(microseconds=1), self.moved_at,
                            self.moved_at + timedelta(days=1), self.later):
                row = self.raw(probe, at=instant, value=str(zone.target_temperature_c))
                self.readings.append((zone.pk, row))

    def test_committed_revision_replay_preserves_scientific_and_operational_sources(self):
        from .models import BoxLineage, BoxLocation, SubcultureEvent

        missing_box = self.make_box("002")
        BoxLocation.objects.create(box=self.box, thermal_zone=self.a,
                                   starts_at=self.start, ends_at=self.moved_at)
        BoxLocation.objects.create(box=self.box, thermal_zone=self.b, starts_at=self.moved_at)
        legacy_event = SubcultureEvent.objects.create(
            parent_box=self.box, user=self.admin, event_date=self.start.date(),
            notes="Synthetic legacy source; no inferred counts or occurrence time",
        )
        BoxLineage.objects.create(parent_box=self.box, child_box=missing_box,
                                  subculture_event=legacy_event)
        zero = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 2, 9), user=self.admin,
            polyp_count=0, ephyrae_count=0, strobila_count=0, salinity_psu=Decimal("0"),
        )
        unknown = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=date(2026, 2, 16), user=self.admin,
            polyp_count=0, ephyrae_count=0, strobila_count=None, salinity_psu=None,
        )
        AuditLog.objects.create(
            organization=self.org, user=self.admin, action="update", object_type="box",
            object_id=self.box.global_code, description="Synthetic source receipt",
            metadata={"box_id": self.box.pk, "zero": 0, "unknown": None},
        )
        before = self.snapshot()
        source = load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk)
        baseline = compose_revisions(source).published_history
        event = next(item for item in baseline.events if item.key == self.target.key)
        decision = HistoricalRevision(
            str(uuid4()), self.org.pk, self.box.pk, 1, None,
            (AdjustEventZones(self.target, destination_zone_id=self.c.pk),), (review_event(event),),
        )
        with transaction.atomic():
            stream = BoxHistoryStream.objects.create(organization=self.org, box=self.box)
            stored = BoxHistoryRevision.objects.create(
                id=decision.revision_id, stream=stream, sequence=1, author=self.admin,
                reason="Synthetic source-preservation decision", source_fingerprint="a" * 64,
                request_fingerprint="b" * 64, **encode_revision(decision),
            )
            stream.head = stored
            stream.save()
        stored.refresh_from_db()
        stream.refresh_from_db()
        self.assertEqual(stream.head_id, stored.pk)
        replay = decode_revision(
            revision_id=str(stored.pk), organization_id=self.org.pk, box_id=self.box.pk,
            sequence=stored.sequence, predecessor_id=None, operations=stored.operations,
            review_values=stored.review_values, justifications=stored.justifications,
        )
        self.assertEqual(replay, decision)
        projected = compose_revisions(source, (replay,)).published_history
        changed = next(item for item in projected.events if item.key == self.target.key)
        self.assertEqual(changed.destination_zone_id, self.c.pk)
        self.assertEqual(changed.sources, event.sources)
        self.assertIs(changed.original, event.original)
        self.assertEqual(load_box_source_history(organization_id=self.org.pk, box_id=self.box.pk), source)
        storage = {"cultures.BoxHistoryStream", "cultures.BoxHistoryRevision"}
        after = self.snapshot()
        self.assertEqual({key: value for key, value in after.items() if key not in storage},
                         {key: value for key, value in before.items() if key not in storage})
        zero.refresh_from_db()
        unknown.refresh_from_db()
        self.assertEqual((zero.polyp_count, zero.ephyrae_count, zero.strobila_count, zero.salinity_psu),
                         (0, 0, 0, Decimal("0")))
        self.assertEqual((unknown.polyp_count, unknown.ephyrae_count, unknown.strobila_count,
                          unknown.salinity_psu), (0, 0, None, None))
        self.assertFalse(BiologicalMeasurement.objects.filter(box=missing_box).exists())
        self.assertEqual(self.box.biological_measurements.count(), 2)
        self.assertTrue(any(row.temperature_c == 0 for _, row in self.readings))


@skipUnless(connection.vendor == "postgresql", "Requires disposable PostgreSQL, not SQLite.")
class HistoryRevisionFunctionCatalogPostgresCITests(TestCase):
    def test_installed_function_mutations_are_rejected_and_rolled_back(self):
        # Exercise real pg_proc source storage and transactional DDL remotely only.
        path = Path(__file__).resolve().parents[3] / "scripts" / "validate_history_revision_ci.py"
        spec = importlib.util.spec_from_file_location("revision_function_catalog_qa", path)
        utility = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(utility)
        healthy = utility.installed_schema(connection, guarded=True)
        bodies = {row[0]: row[1] for row in healthy["function_definitions"]}
        name = "history_revision_insert"
        original = bodies[name]
        mutations = (
            original.replace("NEW.sequence < 1", "FALSE"),
            original.replace("OR NEW.author_id IS NULL", ""),
            original.replace("RETURN NEW;", "RETURN NULL;"),
            original.replace("BEGIN", "BEGIN RETURN NEW;", 1),
        )
        for index, body in enumerate(mutations):
            with self.subTest(mutation=index), transaction.atomic():
                self.assertNotEqual(body, original)
                self.assertIn("RAISE EXCEPTION", body)
                self.assertNotIn("$qa$", body)
                with connection.cursor() as cursor:
                    cursor.execute(
                        "CREATE OR REPLACE FUNCTION public.history_revision_insert() "
                        "RETURNS trigger LANGUAGE plpgsql AS $qa$" + body + "$qa$"
                    )
                with self.assertRaisesRegex(RuntimeError, "body differs from migration 0020"):
                    utility.installed_schema(connection, guarded=True)
                transaction.set_rollback(True)
            self.assertEqual(utility.installed_schema(connection, guarded=True), healthy)
        for statement in (
            "ALTER FUNCTION public.history_revision_insert() SECURITY DEFINER",
            "DROP FUNCTION public.history_revision_insert() CASCADE",
            "DROP TRIGGER history_revision_insert ON public.cultures_boxhistoryrevision; "
            "CREATE TRIGGER history_revision_insert BEFORE INSERT ON public.cultures_boxhistoryrevision "
            "FOR EACH ROW WHEN (false) EXECUTE FUNCTION public.history_revision_insert()",
        ):
            with self.subTest(statement=statement), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(statement)
                with self.assertRaises(RuntimeError):
                    utility.installed_schema(connection, guarded=True)
                transaction.set_rollback(True)
            self.assertEqual(utility.installed_schema(connection, guarded=True), healthy)
