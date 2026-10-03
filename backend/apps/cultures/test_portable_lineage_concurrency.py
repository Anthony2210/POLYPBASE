"""Real PostgreSQL races for portable bridges and scoped source snapshots."""

import threading
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from uuid import uuid4

from django.db import (
    IntegrityError, close_old_connections, connection, connections, transaction,
)
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError

from apps.audit.models import AuditLog

from . import portable_lineage
from .models import (
    BoxLineage, PortableLineageEdge, PortableLineageNode, TransferEnvelope, TransferItem,
)
from .portable_lineage import (
    assert_edge, assert_node, build_known_ancestry, project_local_box, project_local_lineage,
)
from .test_portable_lineage import PortableLineageFixtures
from .test_transfer_v2 import TransferV2Fixtures
from .transfer_v2 import create_source_package
from .transfer_v2_protocol import (
    normalize_lineage_snapshot, parse_transfer_envelope, serialize_transfer_envelope,
)


WAIT_TIMEOUT = 10
FUTURE_TIMEOUT = 60


def normalized_sql(sql):
    return " ".join(sql.replace('"', '').upper().split())


def database_diagnostic(error):
    cause = error.__cause__ or error
    return {
        "sqlstate": getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None),
        "constraint": getattr(getattr(cause, "diag", None), "constraint_name", None),
    }


@skipUnless(
    connection.vendor == "postgresql",
    "Portable lineage concurrency requires an isolated PostgreSQL test database.",
)
class PortableLineageConcurrencyTests(TransactionTestCase):
    # Reuse fixture builders, not TestCase's caller-owned atomic transaction.
    local_box = PortableLineageFixtures.local_box
    lineage = PortableLineageFixtures.lineage
    projection_state = PortableLineageFixtures.projection_state
    assert_graph = PortableLineageFixtures.assert_graph
    assert_no_package = TransferV2Fixtures.assert_no_package

    def setUp(self):
        super().setUp()
        TransferV2Fixtures.setUpTestData.__func__(self)
        self.assertTrue(connection.get_autocommit())
        self.assertFalse(connection.in_atomic_block)

    def package(self, selections=None):
        return create_source_package(
            actor=self.actor,
            source_organization=self.source,
            selections=selections if selections is not None else [
                {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
            ],
            protocol_version=(2, 1),
        )

    def _worker(self, operation, synchronize=None):
        """Use a thread-local connection and prove isolation did not leak to it."""
        close_old_connections()
        observations = {"sets": [], "isolations": [], "errors": []}
        try:
            self.assertTrue(connection.get_autocommit())
            self.assertFalse(connection.in_atomic_block)
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '15s'")
                cursor.execute("SET statement_timeout = '20s'")
                cursor.execute("SELECT pg_backend_pid()")
                observations["backend_pid"] = cursor.fetchone()[0]
                cursor.execute("SHOW transaction_isolation")
                observations["baseline_isolation"] = cursor.fetchone()[0]

            def observe(execute, sql, params, many, context):
                statement = normalized_sql(sql)
                is_isolation_set = statement.startswith("SET TRANSACTION ISOLATION LEVEL ")
                if is_isolation_set:
                    observations["sets"].append(statement)
                    self.assertTrue(connection.in_atomic_block)
                    self.assertFalse(connection.get_autocommit())
                try:
                    if synchronize is None:
                        result = execute(sql, params, many, context)
                    else:
                        result = synchronize(execute, sql, params, many, context, observations)
                except Exception as error:
                    diagnostic = database_diagnostic(error)
                    if diagnostic["sqlstate"] is not None:
                        observations["errors"].append(diagnostic)
                    raise
                if is_isolation_set:
                    # Observe PostgreSQL itself, not only the SQL string or a mock.
                    with connection.cursor() as cursor:
                        cursor.execute("SHOW transaction_isolation")
                        observations["isolations"].append(cursor.fetchone()[0])
                return result

            with connection.execute_wrapper(observe):
                result = operation(observations)
            self.assertTrue(connection.get_autocommit())
            self.assertFalse(connection.in_atomic_block)
            self.assertFalse(connection.needs_rollback)
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_isolation")
                observations["after_isolation"] = cursor.fetchone()[0]
                cursor.execute("SELECT 1, pg_backend_pid()")
                usable, backend_pid = cursor.fetchone()
            self.assertEqual(usable, 1)
            self.assertEqual(backend_pid, observations["backend_pid"])
            self.assertEqual(observations["after_isolation"], observations["baseline_isolation"])
            return result, observations
        finally:
            connections.close_all()

    def _assert_serializable(self, observations):
        self.assertEqual(observations["baseline_isolation"], "read committed")
        self.assertTrue(observations["sets"])
        self.assertEqual(
            observations["sets"],
            ["SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"] * len(observations["sets"]),
        )
        self.assertEqual(observations["isolations"], ["serializable"] * len(observations["sets"]))
        self.assertEqual(observations["after_isolation"], observations["baseline_isolation"])

    def _bridge_race(self, operation, model, bridge_column):
        barrier = threading.Barrier(2, timeout=WAIT_TIMEOUT)
        table = model._meta.db_table.upper()
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_backend_pid()")
            main_pid = cursor.fetchone()[0]

        def synchronize(execute, sql, params, many, context, observations):
            statement = normalized_sql(sql)
            if (
                statement.startswith("SELECT ")
                and f" FROM {table} " in statement
                and f"{bridge_column.upper()} = %S" in statement
            ):
                observations["bridge_selects"] = observations.get("bridge_selects", 0) + 1
            if statement.startswith(f"INSERT INTO {table} ") and not observations.get("barrier_hit"):
                # Both transactions have observed the absent bridge before INSERT.
                # Retries must not wait for a worker that has already committed.
                self.assertGreaterEqual(observations.get("bridge_selects", 0), 1)
                observations["barrier_hit"] = True
                barrier.wait()
            return execute(sql, params, many, context)

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._worker, operation, synchronize) for _ in range(2)]
            results = [future.result(timeout=FUTURE_TIMEOUT) for future in futures]
        pids = {observations["backend_pid"] for _, observations in results}
        self.assertEqual(len(pids), 2)
        self.assertNotIn(main_pid, pids)
        for _, observations in results:
            self.assertTrue(observations["barrier_hit"])
            self._assert_serializable(observations)
        errors = [error for _, observations in results for error in observations["errors"]]
        self.assertTrue(errors, "The forced absent-bridge race must reach a database conflict.")
        self.assertTrue(all(error["sqlstate"] in {"40001", "40P01", "23505"} for error in errors), errors)
        self.assertGreaterEqual(sum(len(observations["sets"]) for _, observations in results), 3)
        return results

    def test_node_bridge_race_returns_one_identity_for_the_same_unbridged_box(self):
        self.assertFalse(PortableLineageNode.objects.filter(local_box=self.box).exists())

        def project(observations):
            node = project_local_box(organization=self.source, box=self.box)
            return node.pk, node.node_id

        results = self._bridge_race(project, PortableLineageNode, "local_box_id")
        node = PortableLineageNode.objects.get()
        self.assertEqual(node.local_box_id, self.box.pk)
        self.assertEqual(node.organization_id, self.source.pk)
        self.assertEqual([result for result, _ in results], [(node.pk, node.node_id)] * 2)
        self.assertEqual(node.node_id.version, 4)
        self.assertFalse(PortableLineageEdge.objects.exists())

    def test_edge_bridge_race_with_preprojected_nodes_returns_one_edge(self):
        row = self.lineage()
        source = project_local_box(organization=self.source, box=self.second_box)
        target = project_local_box(organization=self.source, box=self.box)
        before_nodes = list(PortableLineageNode.objects.order_by("pk").values())
        self.assertFalse(PortableLineageEdge.objects.filter(local_lineage=row).exists())

        def project(observations):
            edge = project_local_lineage(organization=self.source, lineage=row)
            return edge.pk, edge.edge_id

        results = self._bridge_race(project, PortableLineageEdge, "local_lineage_id")
        edge = PortableLineageEdge.objects.get()
        self.assertEqual([result for result, _ in results], [(edge.pk, edge.edge_id)] * 2)
        self.assertEqual(edge.organization_id, self.source.pk)
        self.assertEqual(edge.local_lineage_id, row.pk)
        self.assertEqual((edge.source_node_id, edge.target_node_id), (source.pk, target.pk))
        self.assertEqual(edge.relationship_type, row.relationship_type)
        self.assertEqual(edge.edge_id.version, 4)
        self.assertEqual(list(PortableLineageNode.objects.order_by("pk").values()), before_nodes)

    def test_two_21_builders_share_stable_history_but_not_package_ids_or_audits(self):
        parent_edge = self.lineage()
        grandparent = self.local_box("SRC.003")
        grandparent_edge = self.lineage(grandparent, self.second_box)
        before_history = list(BoxLineage.objects.order_by("pk").values())
        results = self._bridge_race(
            lambda observations: self.package().pk, PortableLineageNode, "local_box_id",
        )
        envelopes = [TransferEnvelope.objects.get(pk=pk) for pk, _ in results]
        self.assertEqual(TransferEnvelope.objects.count(), 2)
        self.assertEqual(TransferItem.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), 2)
        self.assertNotEqual(envelopes[0].transfer_id, envelopes[1].transfer_id)
        items = [envelope.items.get() for envelope in envelopes]
        self.assertNotEqual(items[0].item_id, items[1].item_id)
        self.assertEqual(items[0].lineage_snapshot, items[1].lineage_snapshot)
        self.assertEqual(PortableLineageNode.objects.count(), 3)
        self.assertEqual(PortableLineageEdge.objects.count(), 2)
        self.assertEqual(
            set(PortableLineageEdge.objects.values_list("local_lineage_id", flat=True)),
            {parent_edge.pk, grandparent_edge.pk},
        )
        nodes = list(PortableLineageNode.objects.all())
        edges = list(PortableLineageEdge.objects.select_related("source_node", "target_node"))
        root = PortableLineageNode.objects.get(local_box=self.box)
        for envelope, item in zip(envelopes, items):
            self.assert_graph(item.lineage_snapshot, nodes, edges, root)
            self.assertEqual(normalize_lineage_snapshot(item.lineage_snapshot), item.lineage_snapshot)
            data = serialize_transfer_envelope(envelope)
            parsed = parse_transfer_envelope(data)
            self.assertEqual((data["protocol_major"], data["protocol_minor"]), (2, 1))
            self.assertEqual(parsed["transfer_id"], envelope.transfer_id)
            self.assertEqual(parsed["items"][0]["item_id"], item.item_id)
            self.assertEqual(parsed["items"][0]["declared_polyp_quantity"], 0)
            self.assertEqual(data["items"][0]["lineage"], item.lineage_snapshot)
            audit = AuditLog.objects.get(object_type="transfer_envelope", object_id=str(envelope.transfer_id))
            self.assertEqual(audit.organization_id, self.source.pk)
            self.assertEqual(audit.user_id, self.actor.pk)
            self.assertEqual(audit.action, AuditLog.Action.TRANSFER)
            self.assertEqual(audit.metadata, {
                "protocol_major": 2, "protocol_minor": 1, "item_ids": [str(item.item_id)],
            })
        self.assertEqual(list(BoxLineage.objects.order_by("pk").values()), before_history)

    def test_21_snapshot_cannot_mix_reads_around_an_ordinary_committed_lineage_writer(self):
        original = self.lineage()
        project_local_lineage(organization=self.source, lineage=original)
        newancestor = self.local_box("NEW-ROOT-PREDECESSOR.003")
        grandparent = self.local_box("NEW-PARENT-PREDECESSOR.004")
        root_read = threading.Event()
        writer_committed = threading.Event()

        def observe_reads(execute, sql, params, many, context, observations):
            statement = normalized_sql(sql)
            predecessor_select = (
                statement.startswith("SELECT ")
                and " FROM CULTURES_BOXLINEAGE " in statement
                and "CULTURES_BOXLINEAGE.CHILD_BOX_ID = %S" in statement
            )
            result = execute(sql, params, many, context)
            if predecessor_select and params[0] == self.box.pk and not observations.get("paused_root"):
                # execute() has already taken the root predecessor statement's
                # snapshot. Pause before any later ancestry read, not before SQL.
                observations["paused_root"] = True
                self.assertTrue(connection.in_atomic_block)
                with connection.cursor() as cursor:
                    cursor.execute("SHOW transaction_isolation")
                    observations["paused_isolation"] = cursor.fetchone()[0]
                root_read.set()
                if not writer_committed.wait(timeout=WAIT_TIMEOUT):
                    raise TimeoutError("Ordinary lineage writer did not commit during the root read.")
                self.assertTrue(connection.in_atomic_block)
            elif predecessor_select and params[0] == self.second_box.pk:
                observations["parent_read_after_commit"] = writer_committed.is_set()
                self.assertTrue(observations["parent_read_after_commit"])
            if statement.startswith("INSERT INTO CULTURES_TRANSFERITEM "):
                observations["item_frozen_after_commit"] = writer_committed.is_set()
                self.assertTrue(observations["item_frozen_after_commit"])
                self.assertTrue(connection.in_atomic_block)
            return result

        def write_lineage(observations):
            if not root_read.wait(timeout=WAIT_TIMEOUT):
                raise TimeoutError("Builder did not execute its root predecessor SELECT.")
            self.assertFalse(writer_committed.is_set())
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SHOW transaction_isolation")
                    observations["writer_isolation"] = cursor.fetchone()[0]
                # An ordinary writer uses no portable service, advisory lock, or
                # process lock. Both new facts commit together after the root read.
                sibling_parent = BoxLineage.objects.create(parent_box=newancestor, child_box=self.box)
                older_parent = BoxLineage.objects.create(parent_box=grandparent, child_box=self.second_box)
                observations["wrote_while_builder_paused"] = root_read.is_set()
            self.assertFalse(connection.in_atomic_block)
            writer_committed.set()
            return sibling_parent.pk, older_parent.pk

        with ThreadPoolExecutor(max_workers=2) as executor:
            builder = executor.submit(self._worker, lambda observations: self.package().pk, observe_reads)
            writer = executor.submit(self._worker, write_lineage)
            added_ids, writer_observations = writer.result(timeout=FUTURE_TIMEOUT)
            package_pk, builder_observations = builder.result(timeout=FUTURE_TIMEOUT)
        self.assertNotEqual(builder_observations["backend_pid"], writer_observations["backend_pid"])
        self._assert_serializable(builder_observations)
        self.assertEqual(builder_observations["paused_isolation"], "serializable")
        self.assertTrue(builder_observations["paused_root"])
        self.assertTrue(builder_observations["parent_read_after_commit"])
        self.assertTrue(builder_observations["item_frozen_after_commit"])
        self.assertTrue(writer_observations["wrote_while_builder_paused"])
        self.assertEqual(writer_observations["sets"], [])
        self.assertEqual(writer_observations["writer_isolation"], "read committed")
        self.assertEqual(BoxLineage.objects.count(), 3)

        envelope = TransferEnvelope.objects.get(pk=package_pk)
        snapshot = envelope.items.get().lineage_snapshot
        self.assertEqual(normalize_lineage_snapshot(snapshot), snapshot)
        edge_ids = {edge["edge_id"] for edge in snapshot["edges"]}
        exported_rows = set(PortableLineageEdge.objects.filter(
            edge_id__in=edge_ids, organization=self.source,
        ).values_list("local_lineage_id", flat=True))
        old_graph = {original.pk}
        full_graph = old_graph | set(added_ids)
        mixed_graph = old_graph | {added_ids[1]}
        # READ COMMITTED would miss newancestor -> root yet see grandparent ->
        # parent. A SERIALIZABLE attempt sees the old graph, or a full retry sees
        # both committed facts. The normalized mixed DAG is valid but forbidden.
        self.assertNotEqual(exported_rows, mixed_graph)
        self.assertIn(exported_rows, (old_graph, full_graph))
        self.assertEqual(len(snapshot["nodes"]), 2 if exported_rows == old_graph else 4)
        self.assertEqual(len(snapshot["edges"]), len(exported_rows))
        parse_transfer_envelope(serialize_transfer_envelope(envelope))
        self.assertEqual(TransferEnvelope.objects.count(), 1)
        self.assertEqual(TransferItem.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_id=str(envelope.transfer_id)).count(), 1)

    def test_late_audit_failure_rolls_back_new_bridges_items_envelope_and_audit(self):
        self.lineage()
        project_local_box(organization=self.source, box=self.second_box)
        before = self.projection_state()
        written_audits = []

        def fail_after_audit(execute, sql, params, many, context, observations):
            result = execute(sql, params, many, context)
            if normalized_sql(sql).startswith("INSERT INTO AUDIT_AUDITLOG "):
                self.assertEqual(PortableLineageNode.objects.count(), 2)
                self.assertEqual(PortableLineageEdge.objects.count(), 1)
                self.assertEqual(TransferEnvelope.objects.count(), 1)
                self.assertEqual(TransferItem.objects.count(), 2)
                audit = AuditLog.objects.get()
                written_audits.append(audit.pk)
                raise RuntimeError("late audit failure")
            return result

        def fail(observations):
            with self.assertRaisesMessage(RuntimeError, "late audit failure"):
                self.package([
                    {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                    {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 1},
                ])

        _, observations = self._worker(fail, fail_after_audit)
        self._assert_serializable(observations)
        self.assertEqual(len(observations["sets"]), 1)
        self.assertEqual(len(written_audits), 1)
        self.assertEqual(self.projection_state(), before)
        self.assert_no_package()

    def _ambient_operations(self):
        row = self.lineage()
        source = PortableLineageNode.objects.create(organization=self.source, local_box=self.second_box)
        target = PortableLineageNode.objects.create(organization=self.source, local_box=self.box)
        return {
            "project_local_box": lambda: project_local_box(organization=self.source, box=self.box),
            "project_local_lineage": lambda: project_local_lineage(organization=self.source, lineage=row),
            "build_known_ancestry": lambda: build_known_ancestry(organization=self.source, source_box=self.box),
            "assert_node": lambda: assert_node(organization=self.source, node_id=uuid4()),
            "assert_edge": lambda: assert_edge(
                organization=self.source, edge_id=uuid4(), source_node=source, target_node=target,
                relationship_type="subculture", local_lineage=row,
            ),
            "create_source_package_21": self.package,
        }

    def _assert_ambient_rejections(self, operations):
        before = self.projection_state()
        for name, operation in operations.items():
            with self.subTest(service=name):
                with CaptureQueriesContext(connection) as queries:
                    with self.assertRaises(ValidationError) as caught:
                        operation()
                self.assertEqual(caught.exception.get_codes(), {"lineage": "source_lineage_conflict"})
                self.assertEqual(list(queries), [], "Ambient transactions must fail before any source SQL.")
                self.assertFalse(connection.needs_rollback)
        self.assertEqual(self.projection_state(), before)
        self.assert_no_package()

    def test_all_lineage_entrypoints_reject_a_caller_owned_atomic_transaction(self):
        operations = self._ambient_operations()
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            self._assert_ambient_rejections(operations)
        self.assertTrue(connection.get_autocommit())
        self.assertFalse(portable_lineage._lineage_transaction.get())

    def test_all_lineage_entrypoints_reject_manually_disabled_autocommit(self):
        operations = self._ambient_operations()
        transaction.set_autocommit(False)
        try:
            self.assertFalse(connection.in_atomic_block)
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            self._assert_ambient_rejections(operations)
        finally:
            transaction.rollback()
            transaction.set_autocommit(True)
        self.assertTrue(connection.get_autocommit())
        self.assertFalse(connection.needs_rollback)
        self.assertFalse(portable_lineage._lineage_transaction.get())

    def test_real_serialization_failure_retries_the_entire_package_without_orphans(self):
        self.lineage()
        attempted_transfers = []
        attempted_items = []

        def fail_first_audit(execute, sql, params, many, context, observations):
            if normalized_sql(sql).startswith("INSERT INTO AUDIT_AUDITLOG "):
                attempted_transfers.append(TransferEnvelope.objects.get().transfer_id)
                attempted_items.append(TransferItem.objects.get().item_id)
                if len(attempted_transfers) == 1:
                    # Raise a genuine PostgreSQL error after all package writes;
                    # do not replace the transaction wrapper or its retry policy.
                    return execute(
                        "DO $$ BEGIN RAISE EXCEPTION 'forced serialization failure' "
                        "USING ERRCODE = '40001'; END; $$",
                        None, False, context,
                    )
            return execute(sql, params, many, context)

        pk, observations = self._worker(lambda observations: self.package().pk, fail_first_audit)
        self._assert_serializable(observations)
        self.assertEqual(len(observations["sets"]), 2)
        self.assertEqual([error["sqlstate"] for error in observations["errors"]], ["40001"])
        self.assertEqual(len(attempted_transfers), 2)
        self.assertNotEqual(*attempted_transfers)
        self.assertNotEqual(*attempted_items)
        envelope = TransferEnvelope.objects.get(pk=pk)
        self.assertEqual(envelope.transfer_id, attempted_transfers[1])
        self.assertEqual(envelope.items.get().item_id, attempted_items[1])
        self.assertFalse(TransferEnvelope.objects.filter(transfer_id=attempted_transfers[0]).exists())
        self.assertFalse(TransferItem.objects.filter(item_id=attempted_items[0]).exists())
        self.assertEqual(PortableLineageNode.objects.count(), 2)
        self.assertEqual(PortableLineageEdge.objects.count(), 1)
        self.assertEqual(TransferEnvelope.objects.count(), 1)
        self.assertEqual(TransferItem.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 1)
        parse_transfer_envelope(serialize_transfer_envelope(envelope))

    def test_retry_exhaustion_returns_a_conflict_and_rolls_back_every_attempt(self):
        self.lineage()
        before = self.projection_state()
        attempted_transfers = []

        def always_fail_audit(execute, sql, params, many, context, observations):
            if normalized_sql(sql).startswith("INSERT INTO AUDIT_AUDITLOG "):
                attempted_transfers.append(TransferEnvelope.objects.get().transfer_id)
                self.assertEqual(TransferItem.objects.count(), 1)
                return execute(
                    "DO $$ BEGIN RAISE EXCEPTION 'forced serialization failure' "
                    "USING ERRCODE = '40001'; END; $$",
                    None, False, context,
                )
            return execute(sql, params, many, context)

        def exhaust(observations):
            with self.assertRaises(ValidationError) as caught:
                self.package()
            return caught.exception.get_codes()

        codes, observations = self._worker(exhaust, always_fail_audit)
        self._assert_serializable(observations)
        self.assertEqual(codes, {"lineage": "source_lineage_conflict"})
        self.assertEqual(len(observations["sets"]), portable_lineage.TRANSACTION_ATTEMPTS)
        self.assertEqual(len(attempted_transfers), portable_lineage.TRANSACTION_ATTEMPTS)
        self.assertEqual(len(set(attempted_transfers)), portable_lineage.TRANSACTION_ATTEMPTS)
        self.assertEqual(
            [error["sqlstate"] for error in observations["errors"]],
            ["40001"] * portable_lineage.TRANSACTION_ATTEMPTS,
        )
        self.assertEqual(self.projection_state(), before)
        self.assert_no_package()
        self.assertFalse(portable_lineage._lineage_transaction.get())
        # A subsequent real service call must still establish its own isolation.
        _, recovered = self._worker(lambda observations: self.package().pk)
        self._assert_serializable(recovered)
        self.assertEqual(len(recovered["sets"]), 1)

    def test_unrelated_integrity_error_is_not_retried_or_translated_to_a_conflict(self):
        self.lineage()
        before = self.projection_state()

        def violate_check(execute, sql, params, many, context, observations):
            if normalized_sql(sql).startswith("INSERT INTO AUDIT_AUDITLOG "):
                node = PortableLineageNode.objects.get(local_box=self.box)
                return execute(
                    'INSERT INTO "cultures_portablelineageedge" '
                    '("organization_id", "edge_id", "source_node_id", "target_node_id", "relationship_type") '
                    'VALUES (%s, %s, %s, %s, %s)',
                    [self.source.pk, uuid4(), node.pk, node.pk, "other"], False, context,
                )
            return execute(sql, params, many, context)

        def fail(observations):
            with self.assertRaises(IntegrityError) as caught:
                self.package()
            return database_diagnostic(caught.exception)

        error, observations = self._worker(fail, violate_check)
        self._assert_serializable(observations)
        self.assertEqual(len(observations["sets"]), 1)
        self.assertEqual(error, {"sqlstate": "23514", "constraint": "portable_edge_not_self"})
        self.assertEqual(self.projection_state(), before)
        self.assert_no_package()

    def test_postgresql_bridge_constraints_and_indexes_are_live_and_immediate(self):
        for model, identity_column, bridge_column, foreign_table in (
            (PortableLineageNode, "node_id", "local_box_id", "cultures_box"),
            (PortableLineageEdge, "edge_id", "local_lineage_id", "cultures_boxlineage"),
        ):
            with self.subTest(model=model.__name__), connection.cursor() as cursor:
                table = model._meta.db_table
                constraints = connection.introspection.get_constraints(cursor, table)
                description = connection.introspection.get_table_description(cursor, table)
                bridge = next(column for column in description if column.name == bridge_column)
                self.assertTrue(bridge.null_ok)
                identity_name = (
                    "portable_node_identity_unique" if model is PortableLineageNode
                    else "portable_edge_identity_unique"
                )
                self.assertTrue(constraints[identity_name]["unique"])
                self.assertEqual(constraints[identity_name]["columns"], ["organization_id", identity_column])
                bridge_names = [
                    name for name, constraint in constraints.items()
                    if constraint["unique"] and constraint["columns"] == [bridge_column]
                ]
                self.assertEqual(len(bridge_names), 1)
                self.assertFalse(any(
                    constraint["unique"] and constraint["columns"] == [identity_column]
                    for constraint in constraints.values()
                ))
                self.assertTrue(any(
                    constraint["columns"] == [bridge_column]
                    and constraint["foreign_key"] == (foreign_table, "id")
                    for constraint in constraints.values()
                ))
                cursor.execute(
                    "SELECT index_class.relname, index_data.indisunique, index_data.indisvalid, "
                    "index_data.indisready, index_data.indimmediate "
                    "FROM pg_index AS index_data "
                    "JOIN pg_class AS index_class ON index_class.oid = index_data.indexrelid "
                    "WHERE index_data.indrelid = %s::regclass",
                    [table],
                )
                indexes = {row[0]: row[1:] for row in cursor.fetchall()}
                for name in [identity_name, *bridge_names]:
                    self.assertEqual(indexes[name], (True, True, True, True))
                for column in (
                    ["organization_id"] if model is PortableLineageNode
                    else ["organization_id", "source_node_id", "target_node_id"]
                ):
                    self.assertTrue(any(
                        constraint["index"] and constraint["columns"] == [column]
                        for constraint in constraints.values()
                    ), f"Missing indexed lookup on {table}.{column}")
                if model is PortableLineageEdge:
                    for name in (
                        "portable_edge_not_self", "portable_edge_type_valid",
                        "portable_edge_provenance_valid", "portable_transfer_no_local_lineage",
                    ):
                        self.assertTrue(constraints[name]["check"], name)
