from inspect import Parameter, signature
from unittest.mock import patch
from uuid import uuid4

from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.exceptions import ValidationError

from . import portable_lineage
from .models import Box, BoxLineage, PortableLineageEdge, PortableLineageNode, SubcultureEvent
from .portable_lineage import (
    assert_edge, assert_node, build_known_ancestry, project_local_box, project_local_lineage,
)
from .test_transfer_v2 import TransferV2Fixtures


class PortableLineageFixtures(TransferV2Fixtures):
    def local_box(self, code):
        return Box.objects.create(
            organization=self.source, strain=self.strain, global_code=code, box_number=code,
        )

    def lineage(self, parent=None, child=None, **kwargs):
        return BoxLineage.objects.create(
            parent_box=parent if parent is not None else self.second_box,
            child_box=child if child is not None else self.box,
            **kwargs,
        )

    def node(self, box=None, **kwargs):
        return assert_node(organization=self.source, node_id=uuid4(), local_box=box, **kwargs)

    def edge(self, source, target, **kwargs):
        return assert_edge(**{
            "organization": self.source, "edge_id": uuid4(),
            "source_node": source, "target_node": target,
            "relationship_type": "subculture", **kwargs,
        })

    def ancestry(self, box=None, **kwargs):
        return build_known_ancestry(
            organization=self.source, source_box=box if box is not None else self.box, **kwargs,
        )

    def projection_state(self):
        return {
            model: list(model.objects.order_by("pk").values())
            for model in (PortableLineageNode, PortableLineageEdge)
        }

    def assert_conflict_atomic(self, operation):
        before = self.projection_state()
        with self.assertRaises(ValidationError) as caught:
            operation()
        self.assertEqual(self.projection_state(), before)
        self.assertFalse(connection.needs_rollback)
        return caught.exception

    def assert_graph(self, graph, nodes, edges, root):
        self.assertEqual(set(graph), {"root_node_id", "nodes", "edges"})
        self.assertEqual(graph["root_node_id"], str(root.node_id))
        self.assertEqual(graph["nodes"], sorted(
            [{"node_id": str(node.node_id)} for node in nodes], key=lambda row: row["node_id"],
        ))
        expected_edges = []
        for edge in edges:
            data = {
                "edge_id": str(edge.edge_id),
                "source_node_id": str(edge.source_node.node_id),
                "target_node_id": str(edge.target_node.node_id),
                "relationship_type": edge.relationship_type,
            }
            if edge.relationship_type == "transfer":
                data.update(transfer_id=str(edge.transfer_id), item_id=str(edge.item_id))
            expected_edges.append(data)
        self.assertEqual(graph["edges"], sorted(expected_edges, key=lambda row: row["edge_id"]))


class PortableLineageServiceTests(PortableLineageFixtures):
    def test_public_service_arguments_are_keyword_only_and_limits_are_explicit(self):
        for service in (
            project_local_box, project_local_lineage, assert_node, assert_edge, build_known_ancestry,
        ):
            with self.subTest(service=service.__name__):
                self.assertTrue(all(
                    parameter.kind == Parameter.KEYWORD_ONLY
                    for parameter in signature(service).parameters.values()
                ))
        parameters = signature(build_known_ancestry).parameters
        self.assertEqual(parameters["max_nodes"].default, 250)
        self.assertEqual(parameters["max_edges"].default, 1000)

    def test_repeated_local_projection_and_assertions_keep_node_and_edge_identities(self):
        lineage = self.lineage()
        edge = project_local_lineage(organization=self.source, lineage=lineage)
        before = self.projection_state()
        for _ in range(2):
            repeated = project_local_lineage(organization=self.source, lineage=lineage)
            self.assertEqual((repeated.pk, repeated.edge_id), (edge.pk, edge.edge_id))
            for node, box in ((edge.source_node, self.second_box), (edge.target_node, self.box)):
                projected = project_local_box(organization=self.source, box=box)
                asserted = assert_node(organization=self.source, node_id=str(node.node_id), local_box=box)
                self.assertEqual((projected.pk, projected.node_id), (node.pk, node.node_id))
                self.assertEqual(asserted.pk, node.pk)
            repeated = self.edge(
                edge.source_node, edge.target_node, edge_id=str(edge.edge_id), local_lineage=lineage,
            )
            self.assertEqual(repeated.pk, edge.pk)
        self.assertEqual(self.projection_state(), before)
        self.assertEqual(edge.edge_id.version, 4)
        self.assertEqual(edge.source_node.node_id.version, 4)

    def test_unbridged_node_can_attach_once_and_omitted_bridge_preserves_it(self):
        node = self.node()
        attached = assert_node(organization=self.source, node_id=node.node_id, local_box=self.box)
        self.assertEqual(attached.pk, node.pk)
        self.assertEqual(attached.local_box_id, self.box.pk)
        repeated = assert_node(organization=self.source, node_id=node.node_id)
        self.assertEqual(repeated.local_box_id, self.box.pk)
        self.assertEqual(project_local_box(organization=self.source, box=self.box).pk, node.pk)

    def test_node_bridge_cannot_rebind_or_give_box_a_second_identity(self):
        node = self.node(self.box)
        for node_id, box in ((node.node_id, self.second_box), (uuid4(), self.box)):
            with self.subTest(node_id=node_id, box=box.pk):
                self.assert_conflict_atomic(lambda: assert_node(
                    organization=self.source, node_id=node_id, local_box=box,
                ))
        node.refresh_from_db()
        self.assertEqual(node.local_box_id, self.box.pk)

    def test_edge_bridge_can_attach_once_and_omission_preserves_it(self):
        lineage = self.lineage()
        source, target = self.node(self.second_box), self.node(self.box)
        edge = self.edge(source, target)
        attached = self.edge(source, target, edge_id=edge.edge_id, local_lineage=lineage)
        self.assertEqual(attached.pk, edge.pk)
        self.assertEqual(attached.local_lineage_id, lineage.pk)
        repeated = self.edge(source, target, edge_id=edge.edge_id)
        self.assertEqual(repeated.local_lineage_id, lineage.pk)
        self.assertEqual(project_local_lineage(organization=self.source, lineage=lineage).pk, edge.pk)
        self.assert_conflict_atomic(lambda: self.edge(source, target, local_lineage=lineage))

    def test_same_edge_identity_rejects_contradictory_endpoints_and_relationship(self):
        source, target, other = self.node(), self.node(), self.node()
        edge = self.edge(source, target)
        for changes in (
            {"source_node": other}, {"target_node": other},
            {"source_node": target, "target_node": source}, {"relationship_type": "other"},
            {"relationship_type": "transfer", "transfer_id": uuid4(), "item_id": uuid4()},
        ):
            with self.subTest(changes=changes):
                self.assert_conflict_atomic(lambda: assert_edge(**{
                    "organization": self.source, "edge_id": edge.edge_id,
                    "source_node": source, "target_node": target,
                    "relationship_type": "subculture", **changes,
                }))

    def test_established_edge_cannot_rebind_to_another_local_lineage(self):
        first_row = self.lineage()
        first = project_local_lineage(organization=self.source, lineage=first_row)
        other_parent = self.local_box("REBIND.003")
        second_row = self.lineage(other_parent, self.box)
        other_source = self.node(other_parent)
        self.assert_conflict_atomic(lambda: self.edge(
            other_source, first.target_node, edge_id=first.edge_id, local_lineage=second_row,
        ))
        first.refresh_from_db()
        self.assertEqual(first.local_lineage_id, first_row.pk)
        self.assertFalse(PortableLineageEdge.objects.filter(local_lineage=second_row).exists())

    def test_local_lineage_projection_rejects_unsupported_types_without_bridges(self):
        row = self.lineage()
        for relationship_type in ("unknown", "transfer", ""):
            with self.subTest(relationship_type=relationship_type):
                BoxLineage.objects.filter(pk=row.pk).update(relationship_type=relationship_type)
                self.assert_conflict_atomic(lambda: project_local_lineage(organization=self.source, lineage=row))
                self.assert_conflict_atomic(self.ancestry)
        self.assertFalse(PortableLineageNode.objects.exists())
        self.assertFalse(PortableLineageEdge.objects.exists())

    def test_transfer_edge_preserves_provenance_and_rejects_same_id_changes(self):
        source, target = self.node(), self.node()
        transfer_id, item_id = uuid4(), uuid4()
        edge = self.edge(
            source, target, relationship_type="transfer", transfer_id=transfer_id, item_id=item_id,
        )
        repeated = self.edge(
            source, target, edge_id=str(edge.edge_id), relationship_type="transfer",
            transfer_id=str(transfer_id), item_id=str(item_id),
        )
        self.assertEqual((repeated.pk, repeated.transfer_id, repeated.item_id), (edge.pk, transfer_id, item_id))
        for changes in ({"transfer_id": uuid4()}, {"item_id": uuid4()}, {"relationship_type": "other", "transfer_id": None, "item_id": None}):
            with self.subTest(changes=changes):
                self.assert_conflict_atomic(lambda: self.edge(source, target, **{
                    "edge_id": edge.edge_id, "relationship_type": "transfer",
                    "transfer_id": transfer_id, "item_id": item_id, **changes,
                }))

    def test_service_rejects_self_edges_unknown_types_and_invalid_provenance(self):
        lineage = self.lineage()
        source, target = self.node(self.second_box), self.node(self.box)
        invalid = [
            {"target_node": source}, {"relationship_type": "unknown"},
            {"relationship_type": "transfer"},
            {"relationship_type": "transfer", "transfer_id": uuid4()},
            {"relationship_type": "transfer", "item_id": uuid4()},
            {"relationship_type": "transfer", "transfer_id": uuid4(), "item_id": uuid4(), "local_lineage": lineage},
        ]
        for relationship_type in BoxLineage.RelationshipType.values:
            for provenance in ({"transfer_id": uuid4()}, {"item_id": uuid4()}, {"transfer_id": uuid4(), "item_id": uuid4()}):
                invalid.append({"relationship_type": relationship_type, **provenance})
        for changes in invalid:
            with self.subTest(changes=changes):
                self.assert_conflict_atomic(lambda: self.edge(source, target, **changes))

    def test_service_rejects_non_uuid_node_edge_and_provenance_without_writes(self):
        source, target = self.node(), self.node()
        for value in (None, True, 1, {}, [], "not-a-uuid"):
            with self.subTest(value=value):
                self.assert_conflict_atomic(lambda: assert_node(organization=self.source, node_id=value))
                self.assert_conflict_atomic(lambda: self.edge(source, target, edge_id=value))
        for field in ("transfer_id", "item_id"):
            for value in (True, 1, {}, [], "not-a-uuid"):
                with self.subTest(field=field, value=value):
                    self.assert_conflict_atomic(lambda: self.edge(source, target, **{
                        "relationship_type": "transfer", "transfer_id": uuid4(), "item_id": uuid4(), field: value,
                    }))

    def test_mutated_local_lineage_bridge_fails_closed_without_rewriting_edge(self):
        lineage = self.lineage()
        edge = project_local_lineage(organization=self.source, lineage=lineage)
        other = self.local_box("BRIDGE.003")
        for changes in (
            {"parent_box": other}, {"child_box": other}, {"relationship_type": "other"},
            {"parent_box": self.foreign_box},
        ):
            with self.subTest(changes=changes):
                BoxLineage.objects.filter(pk=lineage.pk).update(**changes)
                self.assert_conflict_atomic(lambda: project_local_lineage(organization=self.source, lineage=lineage))
                self.assert_conflict_atomic(lambda: self.edge(
                    edge.source_node, edge.target_node, edge_id=edge.edge_id,
                ))
                BoxLineage.objects.filter(pk=lineage.pk).update(
                    parent_box=self.second_box, child_box=self.box, relationship_type="subculture",
                )

    def test_changed_box_organization_invalidates_existing_bridge(self):
        node = self.node(self.box)
        Box.objects.filter(pk=self.box.pk).update(organization=self.foreign)
        self.assert_conflict_atomic(lambda: project_local_box(organization=self.source, box=self.box))
        self.assert_conflict_atomic(lambda: assert_node(organization=self.source, node_id=node.node_id))
        self.assert_conflict_atomic(self.ancestry)

    def test_same_uuids_in_two_tenants_have_independent_bridges_and_traversal(self):
        node_id, ancestor_id, edge_id = uuid4(), uuid4(), uuid4()
        root = assert_node(organization=self.source, node_id=node_id, local_box=self.box)
        ancestor = assert_node(organization=self.source, node_id=ancestor_id)
        local_edge = self.edge(ancestor, root, edge_id=edge_id)
        foreign_root = assert_node(organization=self.foreign, node_id=node_id, local_box=self.foreign_box)
        foreign_ancestor = assert_node(organization=self.foreign, node_id=ancestor_id)
        foreign_edge = assert_edge(
            organization=self.foreign, edge_id=edge_id, source_node=foreign_ancestor,
            target_node=foreign_root, relationship_type="other",
        )
        foreign_extra = assert_node(organization=self.foreign, node_id=uuid4())
        assert_edge(
            organization=self.foreign, edge_id=uuid4(), source_node=foreign_extra,
            target_node=foreign_ancestor, relationship_type="historical_import",
        )
        self.assertNotEqual(root.pk, foreign_root.pk)
        self.assertNotEqual(local_edge.pk, foreign_edge.pk)
        self.assert_graph(self.ancestry(), [root, ancestor], [local_edge], root)
        for operation in (
            lambda: assert_node(organization=self.source, node_id=uuid4(), local_box=self.foreign_box),
            lambda: project_local_box(organization=self.source, box=self.foreign_box),
            lambda: self.edge(foreign_ancestor, root),
            lambda: self.edge(ancestor, foreign_root),
        ):
            self.assert_conflict_atomic(operation)
        foreign_root.refresh_from_db()
        self.assertEqual(foreign_root.local_box_id, self.foreign_box.pk)

    def test_unbridged_foreign_identity_stays_knowledge_only_without_fake_box(self):
        foreign_node = assert_node(organization=self.foreign, node_id=uuid4(), local_box=self.foreign_box)
        before_boxes = list(Box.objects.order_by("pk").values())
        node = assert_node(organization=self.source, node_id=foreign_node.node_id)
        self.assertIsNone(node.local_box_id)
        self.assertEqual(list(Box.objects.order_by("pk").values()), before_boxes)
        self.assertEqual(assert_node(organization=self.source, node_id=node.node_id).pk, node.pk)
        self.assert_graph(self.ancestry(), [self.box.portable_lineage_node], [], self.box.portable_lineage_node)

    def test_explicit_multigeneration_graph_preserves_every_biological_relationship(self):
        ancestor = self.local_box("ANCESTOR.001")
        middle = self.local_box("ANCESTOR.002")
        other_parent = self.local_box("ANCESTOR.003")
        rows = [
            self.lineage(ancestor, middle, relationship_type="historical_import"),
            self.lineage(middle, self.second_box, relationship_type="sexual_reproduction"),
            self.lineage(self.second_box, self.box, relationship_type="subculture"),
            self.lineage(other_parent, self.box, relationship_type="other"),
        ]
        graph = self.ancestry()
        edges = [row.portable_lineage_edge for row in rows]
        nodes = [box.portable_lineage_node for box in (ancestor, middle, other_parent, self.second_box, self.box)]
        self.assert_graph(graph, nodes, edges, self.box.portable_lineage_node)
        self.assertEqual(self.ancestry(), graph)
        self.assertEqual(PortableLineageEdge.objects.count(), len(rows))

    def test_shared_subculture_event_produces_one_edge_per_box_lineage(self):
        event = SubcultureEvent.objects.create(parent_box=self.second_box, user=self.actor)
        sibling = self.local_box("SIBLING.003")
        first = self.lineage(subculture_event=event)
        second = self.lineage(self.second_box, sibling, subculture_event=event)
        self.ancestry()
        self.ancestry(sibling)
        first_edge, second_edge = first.portable_lineage_edge, second.portable_lineage_edge
        self.assertNotEqual(first_edge.edge_id, second_edge.edge_id)
        self.assertEqual(first_edge.source_node_id, second_edge.source_node_id)
        self.assertEqual(PortableLineageEdge.objects.count(), 2)
        self.assertEqual(SubcultureEvent.objects.count(), 1)
        self.assertEqual(event.lineages.count(), 2)

    def test_ancestry_excludes_siblings_descendants_and_disconnected_same_strain_codes(self):
        parent_row = self.lineage()
        sibling = self.local_box("SRC.001.SIBLING")
        descendant = self.local_box("SRC.001.CHILD")
        disconnected = self.local_box("SRC.001.UNRELATED")
        unrelated_child = self.local_box("SRC.001.UNRELATED.CHILD")
        excluded_rows = [
            self.lineage(self.second_box, sibling), self.lineage(self.box, descendant),
            self.lineage(disconnected, unrelated_child),
        ]
        for row in excluded_rows:
            project_local_lineage(organization=self.source, lineage=row)
        graph = self.ancestry()
        edge = parent_row.portable_lineage_edge
        self.assert_graph(graph, [edge.source_node, edge.target_node], [edge], edge.target_node)
        root_only = self.ancestry(disconnected)
        self.assert_graph(root_only, [disconnected.portable_lineage_node], [], disconnected.portable_lineage_node)
        self.assertEqual(disconnected.strain.global_identity_id, self.box.strain.global_identity_id)

    def test_foreign_ancestor_prior_transfer_and_local_descendant_need_no_foreign_operational_lookup(self):
        foreign_node = assert_node(organization=self.foreign, node_id=uuid4(), local_box=self.foreign_box)
        foreign_predecessor = assert_node(organization=self.foreign, node_id=uuid4())
        assert_edge(
            organization=self.foreign, edge_id=uuid4(), source_node=foreign_predecessor,
            target_node=foreign_node, relationship_type="other",
        )
        known_foreign = assert_node(organization=self.source, node_id=foreign_node.node_id)
        known_ancestor = self.node()
        historical = self.edge(known_ancestor, known_foreign, relationship_type="historical_import")
        local_parent = self.node(self.second_box)
        prior_transfer = self.edge(
            known_foreign, local_parent, relationship_type="transfer", transfer_id=uuid4(), item_id=uuid4(),
        )
        row = self.lineage()
        boxes_before = list(Box.objects.order_by("pk").values())
        original_lookup = portable_lineage._local_box
        lookups = []

        def scoped_lookup(organization, box_id):
            self.assertEqual(organization.pk, self.source.pk)
            self.assertIn(box_id, {self.box.pk, self.second_box.pk})
            lookups.append(box_id)
            return original_lookup(organization, box_id)

        with patch.object(portable_lineage, "_local_box", side_effect=scoped_lookup):
            with CaptureQueriesContext(connection) as queries:
                graph = self.ancestry()
        self.assertTrue(lookups)
        for query in queries:
            sql = query["sql"]
            if 'FROM "cultures_box"' in sql:
                self.assertIn('"cultures_box"."organization_id"', sql.split("WHERE", 1)[1])
                for field in ("global_code", "notes", "strain_id", "status", "thermal_zone_id"):
                    self.assertNotIn(f'"cultures_box"."{field}"', sql)
            self.assertNotIn('"measurements_', sql)
            self.assertNotIn('"taxonomy_', sql)
        local_edge = row.portable_lineage_edge
        self.assert_graph(
            graph, [known_ancestor, known_foreign, local_parent, local_edge.target_node],
            [historical, prior_transfer, local_edge], local_edge.target_node,
        )
        self.assertIsNone(known_foreign.local_box_id)
        self.assertEqual(list(Box.objects.order_by("pk").values()), boxes_before)

    def test_cross_organization_box_lineage_fails_atomically_in_both_directions(self):
        for parent, child in ((self.foreign_box, self.box), (self.box, self.foreign_box)):
            with self.subTest(parent=parent.pk, child=child.pk):
                row = self.lineage(parent, child)
                self.assert_conflict_atomic(lambda: project_local_lineage(organization=self.source, lineage=row))
                if child.pk == self.box.pk:
                    self.assert_conflict_atomic(self.ancestry)
                row.delete()
        self.assertFalse(PortableLineageNode.objects.exists())
        self.assertFalse(PortableLineageEdge.objects.exists())

    def test_malformed_foreign_node_or_edge_fk_fails_closed_and_rolls_back_new_projection(self):
        malformed_node = PortableLineageNode.objects.create(organization=self.source, local_box=self.foreign_box)
        root = self.node(self.box)
        PortableLineageEdge.objects.create(
            organization=self.source, source_node=malformed_node, target_node=root,
        )
        self.assert_conflict_atomic(self.ancestry)
        foreign_node = assert_node(organization=self.foreign, node_id=uuid4())
        PortableLineageEdge.objects.create(
            organization=self.source, source_node=foreign_node, target_node=root,
        )
        # Remove the first corruption to exercise the cross-tenant endpoint independently.
        PortableLineageEdge.objects.filter(source_node=malformed_node).delete()
        self.assert_conflict_atomic(self.ancestry)

    def test_local_cycle_fails_atomically_instead_of_returning_partial_graph(self):
        self.lineage()
        self.lineage(self.box, self.second_box)
        error = self.assert_conflict_atomic(self.ancestry)
        self.assertIn("Cycles", str(error.detail))
        self.assertFalse(PortableLineageNode.objects.exists())
        self.assertFalse(PortableLineageEdge.objects.exists())

    def test_cycle_in_union_of_local_and_portable_edges_fails_atomically(self):
        self.lineage()
        root, parent = self.node(self.box), self.node(self.second_box)
        self.edge(root, parent, relationship_type="other")
        error = self.assert_conflict_atomic(self.ancestry)
        self.assertIn("Cycles", str(error.detail))
        self.assertFalse(PortableLineageEdge.objects.filter(local_lineage__isnull=False).exists())

    def test_node_and_edge_bounds_fail_without_truncation_or_partial_bridges(self):
        self.lineage()
        third = self.local_box("LIMIT.003")
        self.lineage(third, self.second_box)
        for limits in ({"max_nodes": 2}, {"max_edges": 1}, {"max_nodes": 0}, {"max_edges": 0}):
            with self.subTest(limits=limits):
                error = self.assert_conflict_atomic(lambda: self.ancestry(**limits))
                self.assertIn("limit", str(error.detail))
        graph = self.ancestry(max_nodes=3, max_edges=2)
        self.assertEqual(len(graph["nodes"]), 3)
        self.assertEqual(len(graph["edges"]), 2)

    def test_default_node_limit_rejects_251_nodes_atomically(self):
        parents = Box.objects.bulk_create([
            Box(organization=self.source, strain=self.strain, global_code=f"LIMIT.{index:03}", box_number=str(index))
            for index in range(250)
        ])
        BoxLineage.objects.bulk_create([BoxLineage(parent_box=parent, child_box=self.box) for parent in parents])
        error = self.assert_conflict_atomic(self.ancestry)
        self.assertIn("limit", str(error.detail))
        self.assertFalse(PortableLineageNode.objects.exists())
        self.assertFalse(PortableLineageEdge.objects.exists())

    def test_default_edge_limit_rejects_1001_edges_instead_of_slicing_snapshot(self):
        root, ancestor = self.node(self.box), self.node()
        PortableLineageEdge.objects.bulk_create([
            PortableLineageEdge(organization=self.source, source_node=ancestor, target_node=root)
            for _ in range(1001)
        ])
        error = self.assert_conflict_atomic(self.ancestry)
        self.assertIn("limit", str(error.detail))
        self.assertEqual(PortableLineageEdge.objects.count(), 1001)
