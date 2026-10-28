"""Synthetic PostgreSQL proofs; run only through the owned native-cluster launcher."""

from contextlib import contextmanager
from datetime import date
from pathlib import Path
from threading import Event, Thread, current_thread
from time import monotonic
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import (
    DatabaseError, close_old_connections, connection, connections, transaction,
)
from django.test import TransactionTestCase

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import LocalStrainIdentity, OrganizationSpeciesCode, Species, Strain

from . import box_codes
from .historical_2026 import identity_execution as execution
from .historical_2026 import identity_reconciliation as reconciliation
from .historical_2026 import identity_references as references
from .models import Box, BoxCodeNamespace
from .qr import box_scan_url, sync_box_qr_tag


FAMILIES = {
    "cla": ("CLA", "CLC", "Cyanea lamarckii", "Chrysaora lactea", tuple(range(1, 11))),
    "sme": ("SME", "SSP", "Stomolophus meleagris", "Stomolophus sp.2", (1, 4, 5, 6, 7, 8, 9, 10)),
}
WAIT_SECONDS = 20


def sqlstate(error):
    cause = error
    while cause is not None:
        code = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
        if code:
            return code
        cause = cause.__cause__
    return None


class PostgreSQLFixture:
    def _fixture_teardown(self):
        if execution.require_database() != "disposable":
            raise RuntimeError("Trigger-aware flush requires the owned fresh native cluster")
        with execution.session() as binding:
            self.assertEqual(binding["mode"], "disposable")
        # Only synthetic teardown may bypass these append-only TRUNCATE guards.
        tables = (
            "measurements_historicaltemperaturereading",
            "measurements_historicaltemperatureevidence",
            "measurements_historicaltemperatureimportreceipt",
        )
        disabled = []
        try:
            with connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(f"ALTER TABLE {connection.ops.quote_name(table)} DISABLE TRIGGER USER")
                    disabled.append(table)
            super()._fixture_teardown()
        finally:
            with connection.cursor() as cursor:
                for table in disabled:
                    cursor.execute(f"ALTER TABLE {connection.ops.quote_name(table)} ENABLE TRIGGER USER")

    def setUp(self):
        super().setUp()
        self.assertFalse(connection.in_atomic_block)
        self.org = Organization.objects.create(name="Synthetic identity PostgreSQL QA", slug="identity-pg-qa")
        self.admin = get_user_model().objects.create_user(
            username="identity-pg-admin", email="identity-pg-admin@example.org",
        )
        OrganizationMembership.objects.create(organization=self.org, user=self.admin, role="admin")
        self.sources, self.targets = {}, {}
        for family, (old, new, old_name, new_name, _) in FAMILIES.items():
            old_species = Species.objects.create(scientific_name=old_name)
            new_species = Species.objects.create(scientific_name=new_name)
            self.sources[family] = Strain.objects.create(
                organization=self.org, species=old_species, code=f"{old}-JKA-1",
            )
            self.targets[family] = Strain.objects.create(
                organization=self.org, species=new_species, code=f"{new}-JKA-1",
                number=1, origin_code="JKA",
            )
            for species, code in ((old_species, old), (new_species, new)):
                OrganizationSpeciesCode.objects.create(organization=self.org, species=species, code=code)

    def snapshot(self):
        with connection.cursor() as cursor:
            result = {}
            for table in connection.introspection.table_names(cursor):
                cursor.execute(f"SELECT * FROM {connection.ops.quote_name(table)}")
                result[table] = sorted(cursor.fetchall(), key=repr)
            return result

    def plan(self, family="cla"):
        return reconciliation.build_plan(
            self.org.pk, family=family, target_strain_id=self.targets[family].pk,
        )

    def apply(self, reviewed, family="cla"):
        return reconciliation.apply_plan(
            self.org.pk, family=family, target_strain_id=self.targets[family].pk,
            actor=self.admin, expected_plan_hash=reconciliation.fingerprint(reviewed),
        )

    def create_box(self, code):
        return Box.objects.create(
            organization=self.org,
            strain=self.targets["cla"] if code.startswith("CLC-") else self.sources["cla"],
            global_code=code, box_number=code.rsplit(".", 1)[1],
        )

    @contextmanager
    def workers(self):
        """Always release barriers and join before TransactionTestCase flushes."""
        workers, releases = [], []

        def start(action, name):
            state = {"ready": Event(), "done": Event()}

            def run():
                close_old_connections()
                try:
                    with connection.cursor() as cursor:
                        cursor.execute("SET lock_timeout = '8s'")
                        cursor.execute("SET statement_timeout = '15s'")
                        cursor.execute("SELECT pg_backend_pid()")
                        state["pid"] = cursor.fetchone()[0]
                    state["ready"].set()
                    state["result"] = action()
                except BaseException as error:
                    state["error"] = error
                finally:
                    connections.close_all()
                    close_old_connections()
                    state["done"].set()

            thread = Thread(target=run, name=name, daemon=True)
            workers.append((thread, state))
            thread.start()
            self.assertTrue(state["ready"].wait(WAIT_SECONDS), state.get("error"))
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                self.assertNotEqual(state["pid"], cursor.fetchone()[0])
            return state

        try:
            yield start, releases
        finally:
            for event in releases:
                event.set()
            for thread, state in workers:
                thread.join(WAIT_SECONDS)
                self.assertFalse(thread.is_alive(), f"Worker did not terminate: {thread.name}")
                self.assertTrue(state["done"].is_set())
            close_old_connections()

    def wait_for(self, event, message):
        self.assertTrue(event.wait(WAIT_SECONDS), message)

    def assert_blocked_by(self, waiter, holder):
        self.assertNotEqual(waiter["pid"], holder["pid"])
        deadline = monotonic() + 10
        while monotonic() < deadline:
            with connection.cursor() as cursor:
                cursor.execute("SELECT %s = ANY(pg_blocking_pids(%s))", [holder["pid"], waiter["pid"]])
                if cursor.fetchone()[0]:
                    self.assertFalse(waiter["done"].is_set())
                    return
            if waiter["done"].wait(0.02):
                break
        self.fail(f"No PostgreSQL lock wait observed; worker error: {waiter.get('error')!r}")

    def assert_busy_or_stale(self, error):
        self.assertIsInstance(error, (ValueError, DatabaseError))
        if isinstance(error, DatabaseError):
            self.assertEqual(sqlstate(error), "55P03")
        else:
            self.assertRegex(str(error).lower(), "busy|lock|concurrent|maintenance|changed|review|stale")


@skipUnless(connection.vendor == "postgresql", "Requires the disposable native PostgreSQL cluster.")
class IdentityNamespacePostgreSQLTests(PostgreSQLFixture, TransactionTestCase):
    def race(self, suffix, winner_prefix, *, rename):
        loser_prefix = "CLC" if winner_prefix == "CLA" else "CLA"
        winner_code = f"{winner_prefix}-JKA-1.{suffix}"
        loser_code = f"{loser_prefix}-JKA-1.{suffix}"
        self.assertFalse(Box.objects.filter(global_code__in=(winner_code, loser_code)).exists())
        initial = {}
        if rename:
            for prefix in (winner_prefix, loser_prefix):
                initial[prefix] = self.create_box(f"{prefix}-JKA-1.1{suffix}").pk
        else:
            self.assertFalse(BoxCodeNamespace.objects.exists())
        held, release, attempted = Event(), Event(), Event()
        lock_order = []
        original_lock = box_codes._lock_namespace_row

        def record_lock(namespace):
            if current_thread().name == "identity-winner":
                lock_order.append(namespace)
            return original_lock(namespace)

        def save(prefix, code):
            if rename:
                box = Box.objects.get(pk=initial[prefix])
                box.global_code = code
                box.save(update_fields=["global_code"])
                return box.pk
            return self.create_box(code).pk

        def first():
            with transaction.atomic():
                box_codes.locked_namespace(f"{winner_prefix}-JKA-1", reconcile=False)
                pk = save(winner_prefix, winner_code)
                held.set()
                if not release.wait(WAIT_SECONDS):
                    raise TimeoutError("Namespace winner was not released")
            return pk

        def second():
            attempted.set()
            return save(loser_prefix, loser_code)

        with patch.object(box_codes, "_lock_namespace_row", side_effect=record_lock):
            with self.workers() as (start, releases):
                releases.append(release)
                one = start(first, "identity-winner")
                self.wait_for(held, "Winner did not hold its uncommitted Box and namespace locks")
                two = start(second, "identity-loser")
                self.wait_for(attempted, "Counterpart writer did not attempt save")
                self.assert_blocked_by(two, one)
                release.set()
                self.wait_for(one["done"], "Winner did not commit")
                self.wait_for(two["done"], "Counterpart writer did not finish")
        self.assertNotIn("error", one, one.get("error"))
        self.assertIsInstance(two.get("error"), ValidationError)
        self.assertEqual(lock_order[:2], ["CLA-JKA-1", "CLC-JKA-1"])
        self.assertEqual(list(Box.objects.filter(global_code__in=(winner_code, loser_code))
                              .values_list("global_code", flat=True)), [winner_code])
        self.assertEqual(Box.objects.count(), 2 if rename else 1)
        if rename:
            self.assertEqual(Box.objects.get(pk=initial[loser_prefix]).global_code,
                             f"{loser_prefix}-JKA-1.1{suffix}")
        Box.objects.all().delete()
        BoxCodeNamespace.objects.all().delete()

    def test_opposite_alias_creation_serializes_from_empty_in_both_directions(self):
        for suffix in ("009", "010"):
            for winner in ("CLA", "CLC"):
                with self.subTest(suffix=suffix, winner=winner):
                    self.race(suffix, winner, rename=False)

    def test_opposite_alias_code_changes_serialize_in_both_directions(self):
        for suffix in ("009", "010"):
            for winner in ("CLA", "CLC"):
                with self.subTest(suffix=suffix, winner=winner):
                    self.race(suffix, winner, rename=True)


@skipUnless(connection.vendor == "postgresql", "Requires the disposable native PostgreSQL cluster.")
class IdentityReconciliationPostgreSQLTests(PostgreSQLFixture, TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.boxes = {}
        for family, (old, _, _, _, numbers) in FAMILIES.items():
            for number in numbers:
                code = f"{old}-JKA-1.{number:03}"
                box = Box.objects.create(
                    organization=self.org, strain=self.sources[family],
                    global_code=code, box_number=f"{number:03}", notes="Synthetic unchanged history",
                    volume_liters="0.00", entered_on=date(2026, 1, 5),
                )
                self.boxes[code] = box
                sync_box_qr_tag(box)
                for week, strobila, salinity in ((37, 0, "0.00"), (38, None, None)):
                    BiologicalMeasurement.objects.create(
                        box=box, measured_on=date.fromisocalendar(2026, week, 1),
                        polyp_count=0, ephyrae_count=0, strobila_count=strobila,
                        salinity_psu=salinity, user=self.admin,
                    )
        self.assertEqual(Box.objects.count(), 18)

    def test_cla_then_sme_preserves_pks_qr_history_and_zero_null(self):
        before = self.snapshot()
        expected = {row["id"]: row for row in Box.objects.order_by("pk").values()}
        urls = {box.pk: box_scan_url(box) for box in self.boxes.values()}
        for family, count in (("cla", 10), ("sme", 8)):
            reviewed = self.plan(family)
            self.assertEqual([e["classification"] for e in reviewed["entries"]], ["READY"] * count)
            self.assertEqual(self.apply(reviewed, family), reviewed)
            for entry in reviewed["entries"]:
                expected[entry["box_pk"]].update(
                    strain_id=self.targets[family].pk, global_code=entry["target_code"],
                )
            self.assertEqual({row["id"]: row for row in Box.objects.order_by("pk").values()}, expected)
            self.assertEqual(AuditLog.objects.filter(
                object_type="identity_reconciliation", object_id=family,
            ).count(), 1)
        after = self.snapshot()
        for table in before.keys() - {Box._meta.db_table, AuditLog._meta.db_table, BoxCodeNamespace._meta.db_table}:
            self.assertEqual(after[table], before[table], table)
        for box in Box.objects.order_by("pk"):
            self.assertEqual(box_scan_url(box), urls[box.pk])
            self.assertEqual(box.tags.get(tag_type="qr").url, urls[box.pk])
            self.assertEqual(box.previous_global_code, "")
            self.assertIsNone(box.merged_into_id)
        self.assertEqual(BiologicalMeasurement.objects.filter(strobila_count=0, salinity_psu=0).count(), 18)
        self.assertEqual(BiologicalMeasurement.objects.filter(
            strobila_count__isnull=True, salinity_psu__isnull=True,
        ).count(), 18)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=reconciliation.OPERATION).count(), 20)

    def shared_references(self, family, *, target=True):
        other = Organization.objects.create(
            name=f"Synthetic foreign {family} reference QA", slug=f"foreign-{family}-reference-qa",
        )
        strains = [self.sources[family]]
        if target:
            strains.append(self.targets[family])
        for index, strain in enumerate(strains, 1):
            Strain.objects.filter(pk=strain.pk).update(organization=None)
            strain.refresh_from_db()
            foreign = Box.objects.create(
                organization=other, strain=strain, global_code=f"PRIVATE-{family.upper()}.{index:03}",
                box_number=f"{index:03}", notes="Synthetic foreign shared reference witness",
            )
            BiologicalMeasurement.objects.create(
                box=foreign, measured_on=date(2026, 1, 5), polyp_count=0,
                ephyrae_count=0, strobila_count=None, salinity_psu=None,
            )
        return other

    def sme_witnesses(self):
        witnesses = []
        for number in (8, 9, 10):
            box = Box.objects.create(
                organization=self.org, strain=self.targets["sme"],
                global_code=f"SSP-JKA-1.{number:03}", box_number=f"{number:03}",
                notes="Synthetic SSP history is not a merge target",
            )
            sync_box_qr_tag(box)
            for week, count in ((1, 0), (2, None)):
                BiologicalMeasurement.objects.create(
                    box=box, measured_on=date.fromisocalendar(2024, week, 1),
                    polyp_count=0, ephyrae_count=0, strobila_count=count,
                    salinity_psu="0.00" if count == 0 else None,
                )
            witnesses.append(box)
        return witnesses

    def assert_reconciliation_delta(self, before, boxes_before, reviewed, family):
        expected = {row["id"]: dict(row) for row in boxes_before}
        for entry in reviewed["entries"]:
            expected[entry["box_pk"]].update(
                strain_id=self.targets[family].pk, global_code=entry["target_code"],
            )
        self.assertEqual({row["id"]: row for row in Box.objects.order_by("pk").values()}, expected)
        after = self.snapshot()
        mutable = {Box._meta.db_table, AuditLog._meta.db_table, BoxCodeNamespace._meta.db_table}
        for table in before.keys() - mutable:
            self.assertEqual(after[table], before[table], table)
        self.assertTrue(all(row in after[AuditLog._meta.db_table] for row in before[AuditLog._meta.db_table]))
        box_ids = [box.pk for box in self.boxes.values()]
        measures = BiologicalMeasurement.objects.filter(box_id__in=box_ids)
        self.assertEqual(measures.filter(strobila_count=0, salinity_psu=0).count(), 18)
        self.assertEqual(measures.filter(strobila_count__isnull=True, salinity_psu__isnull=True).count(), 18)
        for box in Box.objects.filter(pk__in=box_ids):
            self.assertEqual(box.tags.get(tag_type="qr").url, box_scan_url(box))
            self.assertEqual(box.previous_global_code, "")
            self.assertIsNone(box.merged_into_id)

    def test_global_cla_target_requires_prior_own_witness_and_never_claims_shared_refs(self):
        self.shared_references("cla")
        without_witness = self.plan()
        self.assertTrue(any(entry["classification"] == "BLOCKED" for entry in without_witness["entries"]))
        witness = Box.objects.create(
            organization=self.org, strain=self.targets["cla"],
            global_code="CLC-JKA-1.099", box_number="099", notes="Synthetic prior own witness",
        )
        BiologicalMeasurement.objects.create(
            box=witness, measured_on=date(2024, 1, 1), polyp_count=0,
            ephyrae_count=0, strobila_count=None,
        )
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.targets["cla"]).exists())
        before, boxes_before = self.snapshot(), list(Box.objects.order_by("pk").values())
        reviewed = self.plan()
        self.assertEqual([entry["classification"] for entry in reviewed["entries"]], ["READY"] * 10)
        self.assertEqual(self.apply(reviewed), reviewed)
        self.assert_reconciliation_delta(before, boxes_before, reviewed, "cla")
        self.targets["cla"].refresh_from_db()
        self.assertIsNone(self.targets["cla"].organization_id)
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.targets["cla"]).exists())

    def test_global_sme_target_allows_observational_ssp_coexistence_without_merges(self):
        self.shared_references("sme")
        witnesses = self.sme_witnesses()
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.targets["sme"]).exists())
        witness_ids = [box.pk for box in witnesses]
        witness_boxes = list(Box.objects.filter(pk__in=witness_ids).order_by("pk").values())
        witness_measures = list(BiologicalMeasurement.objects.filter(box_id__in=witness_ids).order_by("pk").values())
        before, boxes_before = self.snapshot(), list(Box.objects.order_by("pk").values())
        reviewed = self.plan("sme")
        self.assertEqual([entry["classification"] for entry in reviewed["entries"]], ["READY"] * 8)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.apply(reviewed, "sme"), reviewed)
        self.assert_reconciliation_delta(before, boxes_before, reviewed, "sme")
        self.assertEqual(list(Box.objects.filter(pk__in=witness_ids).order_by("pk").values()), witness_boxes)
        self.assertEqual(list(BiologicalMeasurement.objects.filter(
            box_id__in=witness_ids).order_by("pk").values()), witness_measures)
        for entry in reviewed["entries"]:
            self.assertEqual(entry["old_code"], entry["target_code"])
            self.assertTrue(entry["target_code"].startswith("SME-JKA-1."))
        self.targets["sme"].refresh_from_db()
        self.assertIsNone(self.targets["sme"].organization_id)
        self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.targets["sme"]).exists())

    def remove_cla_target_references(self):
        target = self.targets.pop("cla")
        species = target.species
        OrganizationSpeciesCode.objects.filter(organization=self.org, code__in=("CLA", "CLC")).delete()
        target.delete()
        species.delete()
        self.assertFalse(Species.objects.filter(scientific_name="Chrysaora lactea").exists())
        self.assertFalse(Strain.objects.filter(code="CLC-JKA-1").exists())

    def prepare(self, reviewed):
        return references.apply_plan(
            self.org.pk, family=reviewed["family"], source_strain_id=reviewed["source_strain_id"],
            target_strain_id=reviewed["requested_target_strain_id"], actor=self.admin,
            expected_plan_hash=references.fingerprint(reviewed),
        )

    def test_prepare_missing_refs_then_reconcile_both_families_without_shared_mutations(self):
        self.remove_cla_target_references()
        self.shared_references("cla", target=False)
        self.shared_references("sme")
        self.sme_witnesses()
        OrganizationSpeciesCode.objects.filter(organization=self.org).delete()
        self.assertFalse(OrganizationSpeciesCode.objects.filter(organization=self.org).exists())
        self.assertFalse(LocalStrainIdentity.objects.exists())
        for family in ("cla", "sme"):
            with self.subTest(family=family):
                scope = {"family": family, "source_strain_id": self.sources[family].pk}
                if family == "sme":
                    scope["target_strain_id"] = self.targets[family].pk
                before = self.snapshot()
                reviewed = references.build_plan(self.org.pk, **scope)
                self.assertEqual(references.build_plan(self.org.pk, **scope), reviewed)
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(reviewed["classification"], "READY")
                self.assertNotEqual(reviewed["operation"], reconciliation.OPERATION)
                expected_creates = {"source_assignment", "target_assignment"}
                if family == "cla":
                    expected_creates |= {"target_species", "target_strain", "target_local_identity"}
                self.assertEqual({entry["reference"] for entry in reviewed["entries"] if entry["create"]},
                                 expected_creates)
                prepared = self.prepare(reviewed)
                self.targets[family] = Strain.objects.get(pk=prepared["prepared_target_strain_id"])
                after = self.snapshot()
                additive = {Species._meta.db_table, Strain._meta.db_table,
                            OrganizationSpeciesCode._meta.db_table, LocalStrainIdentity._meta.db_table,
                            AuditLog._meta.db_table}
                for table, rows in before.items():
                    if table in additive:
                        self.assertTrue(all(row in after[table] for row in rows), table)
                    else:
                        self.assertEqual(after[table], rows, table)
                self.assertEqual(AuditLog.objects.filter(
                    object_type="identity_reference_preparation", object_id=family,
                    metadata__operation=references.OPERATION,
                ).count(), 1)
                if family == "cla":
                    self.assertEqual(self.targets[family].organization_id, self.org.pk)
                    local = LocalStrainIdentity.objects.get(strain=self.targets[family])
                    self.assertEqual(local.species_code_assignment.code, "CLC")
                else:
                    self.assertIsNone(self.targets[family].organization_id)
                    self.assertFalse(LocalStrainIdentity.objects.filter(strain=self.targets[family]).exists())
                fresh_preparation = references.build_plan(self.org.pk, **scope)
                self.assertEqual(fresh_preparation["classification"], "ALREADY_PRESENT")
                self.prepare(fresh_preparation)
                self.assertEqual(self.snapshot(), after)
                boxes_before = list(Box.objects.order_by("pk").values())
                correction = self.plan(family)
                expected_count = 10 if family == "cla" else 8
                self.assertEqual([entry["classification"] for entry in correction["entries"]],
                                 ["READY"] * expected_count)
                self.assertEqual(self.apply(correction, family), correction)
                self.assert_reconciliation_delta(after, boxes_before, correction, family)

    def test_preparation_receipt_failure_rolls_back_all_new_references_and_audits(self):
        self.remove_cla_target_references()
        reviewed = references.build_plan(self.org.pk, family="cla", source_strain_id=self.sources["cla"].pk)
        before, calls = self.snapshot(), []
        original = AuditLog.objects.create

        def fail_receipt(**kwargs):
            calls.append(kwargs["object_type"])
            if kwargs["object_type"] == "identity_reference_preparation":
                self.assertTrue(Strain.objects.filter(code="CLC-JKA-1").exists())
                self.assertTrue(LocalStrainIdentity.objects.filter(strain__code="CLC-JKA-1").exists())
                self.assertEqual(AuditLog.objects.filter(metadata__operation=references.OPERATION).count(), 5)
                raise RuntimeError("Synthetic preparation receipt failure")
            return original(**kwargs)

        with patch.object(AuditLog.objects, "create", side_effect=fail_receipt):
            with self.assertRaisesMessage(RuntimeError, "Synthetic preparation receipt failure"):
                self.prepare(reviewed)
        self.assertEqual(len(calls), 6)
        self.assertEqual(calls[-1], "identity_reference_preparation")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(connection.in_atomic_block)

    def test_measurement_change_invalidates_review_without_partial_writes(self):
        for family in FAMILIES:
            with self.subTest(family=family):
                reviewed = self.plan(family)
                measurement = self.boxes[reviewed["entries"][0]["old_code"]].biological_measurements.first()
                BiologicalMeasurement.objects.filter(pk=measurement.pk).update(strobila_count=7)
                self.assertNotEqual(reconciliation.fingerprint(self.plan(family)), reconciliation.fingerprint(reviewed))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.apply(reviewed, family)
                self.assertEqual(self.snapshot(), before)

    def test_last_batch_receipt_failure_rolls_back_boxes_namespaces_and_all_audits(self):
        original = AuditLog.objects.create
        for family, count in (("cla", 10), ("sme", 8)):
            with self.subTest(family=family):
                reviewed, calls = self.plan(family), []
                before = self.snapshot()

                def fail_receipt(**kwargs):
                    calls.append(kwargs["object_type"])
                    if kwargs["object_type"] == "identity_reconciliation":
                        self.assertEqual(AuditLog.objects.filter(
                            metadata__operation=reconciliation.OPERATION, object_type="box",
                        ).count(), count)
                        raise RuntimeError("Synthetic last receipt failure")
                    return original(**kwargs)

                with patch.object(AuditLog.objects, "create", side_effect=fail_receipt):
                    with self.assertRaisesMessage(RuntimeError, "Synthetic last receipt failure"):
                        self.apply(reviewed, family)
                self.assertEqual(calls, ["box"] * count + ["identity_reconciliation"])
                self.assertEqual(self.snapshot(), before)

    def test_existing_legacy_collisions_block_without_rewriting_either_identity(self):
        for suffix in ("009", "010"):
            with self.subTest(suffix=suffix):
                collision = self.create_box(f"CLC-JKA-1.1{suffix}")
                # Bypass ordinary reservations only to model already-persisted legacy corruption.
                Box._base_manager.filter(pk=collision.pk).update(global_code=f"CLC-JKA-1.{suffix}")
                reviewed = self.plan()
                self.assertTrue(any(entry["classification"] == "BLOCKED" for entry in reviewed["entries"]))
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    self.apply(reviewed)
                self.assertEqual(self.snapshot(), before)
                collision.delete()

    def test_concurrent_apply_has_one_audit_batch_then_fresh_review_is_idempotent(self):
        reviewed = self.plan()
        held, release = Event(), Event()
        original = AuditLog.objects.create

        def hold_first_audit(**kwargs):
            result = original(**kwargs)
            if current_thread().name == "identity-apply-first" and not held.is_set():
                held.set()
                if not release.wait(WAIT_SECONDS):
                    raise TimeoutError("First apply was not released")
            return result

        with patch.object(AuditLog.objects, "create", side_effect=hold_first_audit):
            with self.workers() as (start, releases):
                releases.append(release)
                one = start(lambda: self.apply(reviewed), "identity-apply-first")
                self.wait_for(held, "First apply did not reach its transactional audit")
                two = start(lambda: self.apply(reviewed), "identity-apply-second")
                self.wait_for(two["done"], "Concurrent apply did not fail closed while maintenance locks were held")
                self.assert_busy_or_stale(two.get("error"))
                self.assertFalse(one["done"].is_set())
                release.set()
                self.wait_for(one["done"], "First apply did not commit")
        self.assertNotIn("error", one, one.get("error"))
        self.assertEqual(one["result"], reviewed)
        self.assertEqual(AuditLog.objects.filter(metadata__operation=reconciliation.OPERATION).count(), 11)
        fresh = self.plan()
        self.assertEqual([entry["classification"] for entry in fresh["entries"]], ["ALREADY_CORRECT"] * 10)
        before = self.snapshot()
        self.assertEqual(self.apply(fresh), fresh)
        self.assertEqual(self.snapshot(), before)

    def test_active_box_writer_aborts_apply_nowait_then_invalidates_old_review(self):
        reviewed = self.plan()
        box = self.boxes["CLA-JKA-1.001"]
        held, release = Event(), Event()

        def writer():
            with transaction.atomic():
                current = Box.objects.select_for_update().get(pk=box.pk)
                current.notes = "Synthetic concurrent ordinary writer"
                current.save(update_fields=["notes"])
                held.set()
                if not release.wait(WAIT_SECONDS):
                    raise TimeoutError("Ordinary writer was not released")

        before = self.snapshot()
        with self.workers() as (start, releases):
            releases.append(release)
            one = start(writer, "identity-ordinary-writer")
            self.wait_for(held, "Ordinary writer did not hold the Box lock")
            statements = []

            def record_sql(execute, sql, params, many, context):
                statements.append(sql)
                return execute(sql, params, many, context)

            def apply_while_busy():
                with connection.execute_wrapper(record_sql):
                    return self.apply(reviewed)

            two = start(apply_while_busy, "identity-maintenance-apply")
            self.wait_for(two["done"], "Apply waited instead of rejecting a busy writer with NOWAIT")
            lock_statements = [sql for sql in statements if sql.lstrip().upper().startswith("LOCK TABLE")]
            self.assertTrue(lock_statements, statements)
            self.assertTrue(all("EXCLUSIVE MODE NOWAIT" in sql.upper() for sql in lock_statements))
            self.assertFalse(any("FOR UPDATE" in sql.upper() or "FOR NO KEY UPDATE" in sql.upper()
                                 for sql in statements), "Maintenance must reject busy tables before row locks")
            self.assert_busy_or_stale(two.get("error"))
            self.assertFalse(one["done"].is_set())
            self.assertEqual(self.snapshot(), before)
            release.set()
            self.wait_for(one["done"], "Ordinary writer did not commit")
        self.assertNotIn("error", one, one.get("error"))
        fresh = self.plan()
        self.assertNotEqual(reconciliation.fingerprint(fresh), reconciliation.fingerprint(reviewed))
        after_writer = self.snapshot()
        with self.assertRaises(ValueError):
            self.apply(reviewed)
        self.assertEqual(self.snapshot(), after_writer)
        self.assertEqual(self.apply(fresh), fresh)

    def test_missing_global_code_unique_constraint_blocks_plan_and_rolls_back_ddl(self):
        class RollbackSchemaProbe(Exception):
            pass

        if execution.require_database() != "disposable":
            self.fail("Schema DDL requires the owned fresh native cluster")
        baseline = self.plan()
        before = self.snapshot()
        table = Box._meta.db_table
        column = Box._meta.get_field("global_code").column
        with connection.cursor() as cursor:
            original_constraints = connection.introspection.get_constraints(cursor, table)
        names = [name for name, shape in original_constraints.items()
                 if shape.get("unique") and not shape.get("primary_key")
                 and shape.get("columns") == [column]]
        self.assertEqual(len(names), 1, names)
        name = names[0]
        try:
            # DDL needs a real read-write setup session. Exercise the actual inner
            # planner in that session; the public planner owns a read-only outer one.
            with execution.session(apply=True) as binding:
                self.assertEqual(binding["mode"], "disposable")
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"ALTER TABLE {connection.ops.quote_name(table)} "
                        f"DROP CONSTRAINT {connection.ops.quote_name(name)}"
                    )
                    self.assertNotIn(name, connection.introspection.get_constraints(cursor, table))
                with self.assertRaisesMessage(ValueError, "Missing required primary/unique identity constraint"):
                    reconciliation._build_plan(
                        self.org.pk, family="cla", target_strain_id=self.targets["cla"].pk,
                        binding=binding,
                    )
                raise RollbackSchemaProbe()
        except RollbackSchemaProbe:
            pass
        finally:
            with connection.cursor() as cursor:
                restored = connection.introspection.get_constraints(cursor, table)
            self.assertEqual(restored, original_constraints)
        self.assertFalse(connection.in_atomic_block)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.plan(), baseline)

    def test_same_name_weakened_check_changes_hash_and_rejects_reviewed_apply(self):
        if execution.require_database() != "disposable":
            self.fail("Schema DDL requires the owned fresh native cluster")
        reviewed = self.plan()
        before = self.snapshot()
        table = Box._meta.db_table
        with execution.session() as binding:
            self.assertEqual(binding["mode"], "disposable")
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT k.conname, pg_get_constraintdef(k.oid, true) "
                    "FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid "
                    "JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relname=%s AND k.conname=%s AND k.contype='c'",
                    [table, "merged_box_is_retired"],
                )
                rows = cursor.fetchall()
                self.assertEqual(len(rows), 1)
                name, definition = rows[0]
                original_shape = connection.introspection.get_constraints(cursor, table)[name]
        weakened = definition.replace(" AND ", " OR ")
        self.assertNotEqual(weakened, definition, definition)

        def replace_check(sql_definition):
            with execution.session(apply=True) as binding:
                self.assertEqual(binding["mode"], "disposable")
                with connection.cursor() as cursor:
                    quoted_table = '"public".' + connection.ops.quote_name(table)
                    quoted_name = connection.ops.quote_name(name)
                    cursor.execute(f"ALTER TABLE {quoted_table} DROP CONSTRAINT {quoted_name}")
                    # Definition comes only from pg_get_constraintdef on this owned cluster.
                    cursor.execute(f"ALTER TABLE {quoted_table} ADD CONSTRAINT {quoted_name} {sql_definition}")

        try:
            replace_check(weakened)
            with connection.cursor() as cursor:
                self.assertEqual(connection.introspection.get_constraints(cursor, table)[name], original_shape)
            changed = self.plan()
            self.assertNotEqual(reconciliation.fingerprint(changed), reconciliation.fingerprint(reviewed))
            self.assertEqual([entry["classification"] for entry in changed["entries"]], ["READY"] * 10)
            with self.assertRaisesMessage(ValueError, "Persisted state changed since review"):
                self.apply(reviewed)
            self.assertEqual(self.snapshot(), before)
        finally:
            replace_check(definition)
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_get_constraintdef(k.oid, true) FROM pg_constraint k "
                    "JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                    "WHERE n.nspname='public' AND c.relname=%s AND k.conname=%s",
                    [table, name],
                )
                self.assertEqual(cursor.fetchall(), [(definition,)])
                self.assertEqual(connection.introspection.get_constraints(cursor, table)[name], original_shape)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.plan(), reviewed)

    def test_source_diagnostic_sql_executes_read_only_without_mutations(self):
        if execution.require_database() != "disposable":
            self.fail("Diagnostic SQL requires the owned fresh native cluster")
        with execution.session() as binding:
            self.assertEqual(binding["mode"], "disposable")
        source = Path(__file__).resolve().parents[3] / "deploy/sql/cla_sme_readonly_diagnostics.sql"
        script = source.read_text(encoding="utf-8")
        before = self.snapshot()
        result_columns = []
        try:
            # Execute the operator source unchanged, including its own BEGIN READ ONLY.
            with connection.connection.cursor() as cursor:
                cursor.execute(script, prepare=False)
                while True:
                    if cursor.description is not None:
                        columns = [column.name for column in cursor.description]
                        rows = cursor.fetchall()
                        result_columns.append(columns)
                        if "transaction_read_only" in columns:
                            self.assertEqual(len(rows), 1)
                            self.assertEqual(rows[0][columns.index("transaction_read_only")], "on")
                    if not cursor.nextset():
                        break
                cursor.execute("SHOW transaction_read_only")
                self.assertEqual(cursor.fetchone(), ("on",))
        finally:
            connection.rollback()
        self.assertGreaterEqual(len(result_columns), 9)
        self.assertTrue(any("transaction_read_only" in columns for columns in result_columns))
        self.assertTrue(any("receipt_entry_count" in columns for columns in result_columns))
        self.assertEqual(self.snapshot(), before)
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            self.assertEqual(cursor.fetchone(), ("off",))

    def test_planning_uses_read_only_transaction_without_row_locks(self):
        original = reconciliation._build_plan
        observed = []

        def assert_read_only():
            self.assertTrue(connection.in_atomic_block)
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                self.assertEqual(cursor.fetchone(), ("on",))
                cursor.execute(
                    "SELECT locktype, mode FROM pg_locks WHERE pid=pg_backend_pid() "
                    "AND (locktype='tuple' OR (locktype='relation' AND mode IN "
                    "('RowShareLock', 'RowExclusiveLock', 'ShareRowExclusiveLock', "
                    "'ExclusiveLock', 'AccessExclusiveLock')))"
                )
                self.assertEqual(cursor.fetchall(), [])
            observed.append(True)

        def inspect(*args, **kwargs):
            assert_read_only()
            result = original(*args, **kwargs)
            assert_read_only()
            return result

        before = self.snapshot()
        with patch.object(reconciliation, "_build_plan", side_effect=inspect):
            first = self.plan()
            self.assertEqual(self.plan(), first)
        self.assertEqual(len(observed), 4)
        self.assertFalse(connection.in_atomic_block)
        with connection.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            self.assertEqual(cursor.fetchone(), ("off",))
        self.assertEqual(self.snapshot(), before)

    def test_planning_rejects_injected_orm_write_and_rolls_back_session(self):
        original = reconciliation._build_plan
        before = self.snapshot()

        def attempt_write(*args, **kwargs):
            original(*args, **kwargs)
            Organization.objects.filter(pk=self.org.pk).update(name="Forbidden synthetic planner write")
            self.fail("PostgreSQL accepted a write inside read-only planning")

        with patch.object(reconciliation, "_build_plan", side_effect=attempt_write):
            with self.assertRaises(DatabaseError) as caught:
                self.plan()
        self.assertEqual(sqlstate(caught.exception), "25006")
        self.assertFalse(connection.in_atomic_block)
        self.assertFalse(connection.needs_rollback)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([entry["classification"] for entry in self.plan()["entries"]], ["READY"] * 10)
