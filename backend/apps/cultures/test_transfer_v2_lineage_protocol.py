import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from django.db import connections
from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from .transfer_v2_protocol import (
    LINEAGE_MAX_EDGES, LINEAGE_MAX_NODES, LINEAGE_RELATIONSHIP_TYPES,
    PROTOCOL_MAJOR, PROTOCOL_MINOR, SUPPORTED_VERSIONS,
    LineageSnapshotSerializer, TransferEnvelopeSerializer, TransferEnvelopeV21Serializer,
    TransferItemSerializer, TransferItemV21Serializer,
    normalize_lineage_snapshot, parse_transfer_envelope, serialize_transfer_envelope,
)


TOP_FIELDS = {
    "protocol", "protocol_major", "protocol_minor", "transfer_id", "created_at",
    "source_institution_id", "source_institution_name", "destination_institution_id",
    "destination_institution_name", "items",
}
ITEM_FIELDS = {
    "item_id", "source_box_code", "source_strain_code", "species_scientific_name",
    "global_strain_id", "declared_polyp_quantity",
}


def identity(number):
    return str(UUID(int=number))


def edge(number, source, target, relationship_type="subculture"):
    data = {
        "edge_id": identity(number),
        "source_node_id": identity(source),
        "target_node_id": identity(target),
        "relationship_type": relationship_type,
    }
    if relationship_type == "transfer":
        data.update(transfer_id=identity(5000), item_id=identity(5001))
    return data


def snapshot():
    return {
        "root_node_id": identity(3),
        "nodes": [{"node_id": identity(number)} for number in (3, 1, 2)],
        "edges": [edge(12, 2, 3, "transfer"), edge(11, 1, 2)],
    }


def payload(minor=1, item_count=1):
    return {
        "protocol": "polypbase.transfer",
        "protocol_major": 2,
        "protocol_minor": minor,
        "transfer_id": identity(6000),
        "created_at": "2026-09-01T12:34:56Z",
        "source_institution_id": identity(6001),
        "source_institution_name": "Remote laboratory",
        "destination_institution_id": None,
        "destination_institution_name": "",
        "items": [
            {
                "item_id": identity(7000 + index),
                "source_box_code": f"REMOTE.{index + 1:03}",
                "source_strain_code": "AAU-REMOTE",
                "species_scientific_name": "Aurelia aurita",
                "global_strain_id": identity(8000),
                "declared_polyp_quantity": index,
                **({"lineage": snapshot()} if minor == 1 else {}),
            }
            for index in range(item_count)
        ],
    }


class SnapshotItem(SimpleNamespace):
    @property
    def source_box(self):
        raise AssertionError("Serialization must not read live source records.")


class SnapshotItems:
    def __init__(self, items):
        self.items = items
        self.ordering = None

    def order_by(self, ordering):
        self.ordering = ordering
        return sorted(self.items, key=lambda item: item.pk)


def stored_envelope(minor=1, item_count=1):
    data = parse_transfer_envelope(payload(minor, item_count))
    items = []
    for index, item in enumerate(data.pop("items")):
        lineage = item.pop("lineage", None)
        items.append(SnapshotItem(
            **item, pk=index + 1,
            lineage_snapshot=normalize_lineage_snapshot(lineage) if lineage is not None else None,
        ))
    return SimpleNamespace(**data, items=SnapshotItems(list(reversed(items))))


class LineageSnapshotProtocolTests(SimpleTestCase):
    # SimpleTestCase forbids database queries and does not create a test database.
    def assert_invalid(self, data):
        with self.assertRaises(ValidationError):
            normalize_lineage_snapshot(data)

    def test_exposed_limits_and_relationship_literals(self):
        self.assertEqual(LINEAGE_MAX_NODES, 250)
        self.assertEqual(LINEAGE_MAX_EDGES, 1000)
        self.assertEqual(set(LINEAGE_RELATIONSHIP_TYPES), {
            "subculture", "sexual_reproduction", "historical_import", "other", "transfer",
        })

    def test_minimal_single_root_without_edges(self):
        data = {"root_node_id": identity(1), "nodes": [{"node_id": identity(1)}], "edges": []}
        self.assertEqual(normalize_lineage_snapshot(data), data)

    def test_typed_validation_and_normalized_minimal_json(self):
        data = snapshot()
        serializer = LineageSnapshotSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        attrs = serializer.validated_data
        self.assertIsInstance(attrs["root_node_id"], UUID)
        for node in attrs["nodes"]:
            self.assertIsInstance(node["node_id"], UUID)
        for item in attrs["edges"]:
            for field in ("edge_id", "source_node_id", "target_node_id", "transfer_id", "item_id"):
                if field in item:
                    self.assertIsInstance(item[field], UUID)
        normalized = normalize_lineage_snapshot(data)
        self.assertEqual(set(normalized), {"root_node_id", "nodes", "edges"})
        self.assertEqual([node["node_id"] for node in normalized["nodes"]],
                         [identity(number) for number in (1, 2, 3)])
        self.assertEqual([item["edge_id"] for item in normalized["edges"]],
                         [identity(number) for number in (11, 12)])
        self.assertEqual(set(normalized["nodes"][0]), {"node_id"})
        self.assertEqual(set(normalized["edges"][0]), {
            "edge_id", "source_node_id", "target_node_id", "relationship_type",
        })
        self.assertEqual(set(normalized["edges"][1]), {
            "edge_id", "source_node_id", "target_node_id", "relationship_type", "transfer_id", "item_id",
        })
        self.assertEqual(json.loads(json.dumps(normalized)), normalized)

    def test_normalization_is_deterministic_idempotent_and_does_not_mutate_input(self):
        data = snapshot()
        original = deepcopy(data)
        normalized = normalize_lineage_snapshot(data)
        alternate = deepcopy(data)
        alternate["nodes"].reverse()
        alternate["edges"].reverse()
        for target in [alternate, *alternate["nodes"], *alternate["edges"]]:
            for field, value in list(target.items()):
                if field.endswith("_id"):
                    target[field] = UUID(value) if field == "item_id" else value.replace("-", "").upper()
        self.assertEqual(normalize_lineage_snapshot(alternate), normalized)
        self.assertEqual(normalize_lineage_snapshot(normalized), normalized)
        self.assertEqual(data, original)

    def test_all_relationship_types(self):
        for relationship in LINEAGE_RELATIONSHIP_TYPES:
            with self.subTest(relationship=relationship):
                data = snapshot()
                data["edges"][0] = edge(12, 2, 3, relationship)
                result = normalize_lineage_snapshot(data)
                self.assertEqual(result["edges"][1]["relationship_type"], relationship)

    def test_rejects_unknown_fields_at_every_depth(self):
        for scope in ("snapshot", "node", "edge"):
            for field in ("box_id", "organization_id", "metadata", "notes", "truncated"):
                with self.subTest(scope=scope, field=field):
                    data = snapshot()
                    target = {"snapshot": data, "node": data["nodes"][0], "edge": data["edges"][0]}[scope]
                    target[field] = "private"
                    self.assert_invalid(data)

    def test_rejects_missing_fields_at_every_depth(self):
        for scope, fields in (
            ("snapshot", ("root_node_id", "nodes", "edges")),
            ("node", ("node_id",)),
            ("edge", ("edge_id", "source_node_id", "target_node_id", "relationship_type")),
        ):
            for field in fields:
                with self.subTest(scope=scope, field=field):
                    data = snapshot()
                    target = {"snapshot": data, "node": data["nodes"][0], "edge": data["edges"][0]}[scope]
                    del target[field]
                    self.assert_invalid(data)

    def test_rejects_invalid_containers(self):
        invalid_objects = (None, [], "object", 1, True)
        for value in invalid_objects:
            with self.subTest(snapshot=value):
                self.assert_invalid(value)
            for field in ("nodes", "edges"):
                with self.subTest(field=field, entry=value):
                    data = snapshot()
                    data[field] = [value]
                    self.assert_invalid(data)
        for field in ("nodes", "edges"):
            for value in (None, {}, "list", 1, True, ()):
                with self.subTest(field=field, collection=value):
                    data = snapshot()
                    data[field] = value
                    self.assert_invalid(data)
        data = snapshot()
        data["nodes"] = []
        self.assert_invalid(data)

    def test_rejects_invalid_uuid_values_in_all_identity_fields(self):
        for scope, fields in (
            ("snapshot", ("root_node_id",)),
            ("node", ("node_id",)),
            ("edge", ("edge_id", "source_node_id", "target_node_id", "transfer_id", "item_id")),
        ):
            for field in fields:
                for value in (None, "", "not-a-uuid", 1, True, False, 1.5, {}, []):
                    with self.subTest(scope=scope, field=field, value=value):
                        data = snapshot()
                        target = {"snapshot": data, "node": data["nodes"][0], "edge": data["edges"][0]}[scope]
                        target[field] = value
                        self.assert_invalid(data)

    def test_rejects_unknown_or_nonliteral_relationship_types(self):
        for value in (None, "", "parent", "TRANSFER", " transfer ", 1, True, [], {}):
            with self.subTest(value=value):
                data = snapshot()
                data["edges"][0]["relationship_type"] = value
                self.assert_invalid(data)

    def test_transfer_provenance_requires_both_nonnull_uuid_fields(self):
        for fields in (("transfer_id",), ("item_id",), ("transfer_id", "item_id")):
            with self.subTest(missing=fields):
                data = snapshot()
                for field in fields:
                    del data["edges"][0][field]
                self.assert_invalid(data)

    def test_nontransfer_provenance_must_be_absent_even_if_null(self):
        for relationship in set(LINEAGE_RELATIONSHIP_TYPES) - {"transfer"}:
            for fields in (("transfer_id",), ("item_id",), ("transfer_id", "item_id")):
                for value in (identity(5000), None):
                    with self.subTest(relationship=relationship, fields=fields, value=value):
                        data = snapshot()
                        data["edges"][0] = edge(12, 2, 3, relationship)
                        data["edges"][0].update(dict.fromkeys(fields, value))
                        self.assert_invalid(data)

    def test_rejects_duplicate_nodes_after_uuid_normalization(self):
        for value in (identity(1), UUID(identity(1)), identity(1).replace("-", "").upper()):
            with self.subTest(value=value):
                data = snapshot()
                data["nodes"].append({"node_id": value})
                self.assert_invalid(data)

    def test_rejects_duplicate_edges_after_uuid_normalization(self):
        for value in (identity(11), UUID(identity(11)), identity(11).replace("-", "").upper()):
            with self.subTest(value=value):
                data = snapshot()
                duplicate = edge(11, 1, 3)
                duplicate["edge_id"] = value
                data["edges"].append(duplicate)
                self.assert_invalid(data)

    def test_rejects_missing_root_and_endpoints(self):
        for scope, field in (("snapshot", "root_node_id"), ("edge", "source_node_id"),
                             ("edge", "target_node_id")):
            with self.subTest(scope=scope, field=field):
                data = snapshot()
                target = data if scope == "snapshot" else data["edges"][0]
                target[field] = identity(999)
                self.assert_invalid(data)

    def test_rejects_self_edges_and_cycles_including_disconnected_components(self):
        cases = [
            [edge(13, 1, 1)],
            [edge(13, 3, 1)],
            [edge(13, 2, 1)],
        ]
        for additions in cases:
            with self.subTest(additions=additions):
                data = snapshot()
                data["edges"].extend(additions)
                self.assert_invalid(data)
        data = snapshot()
        data["nodes"].extend({"node_id": identity(number)} for number in (4, 5))
        data["edges"].extend([edge(13, 4, 5), edge(14, 5, 4)])
        self.assert_invalid(data)

    def test_rejects_isolated_disconnected_and_wrong_direction_nodes(self):
        data = snapshot()
        data["nodes"].append({"node_id": identity(4)})
        self.assert_invalid(data)
        data["nodes"].append({"node_id": identity(5)})
        data["edges"].append(edge(13, 4, 5))
        self.assert_invalid(data)
        data = snapshot()
        data["edges"] = [edge(11, 3, 2), edge(12, 2, 1)]
        self.assert_invalid(data)
        data = snapshot()
        data["edges"] = []
        self.assert_invalid(data)

    def test_accepts_diamond_dag_and_distinct_parallel_edges(self):
        data = {
            "root_node_id": identity(4),
            "nodes": [{"node_id": identity(number)} for number in (1, 2, 3, 4)],
            "edges": [edge(11, 1, 2), edge(12, 1, 3), edge(13, 2, 4), edge(14, 3, 4),
                      edge(15, 1, 2, "other")],
        }
        self.assertEqual(len(normalize_lineage_snapshot(data)["edges"]), 5)

    def test_node_bound_accepts_full_chain_and_fails_without_truncation(self):
        def chain(count):
            return {
                "root_node_id": identity(count),
                "nodes": [{"node_id": identity(number)} for number in range(1, count + 1)],
                "edges": [edge(1000 + number, number, number + 1) for number in range(1, count)],
            }
        data = chain(LINEAGE_MAX_NODES)
        self.assertEqual(len(normalize_lineage_snapshot(data)["nodes"]), LINEAGE_MAX_NODES)
        oversized = chain(LINEAGE_MAX_NODES + 1)
        original = deepcopy(oversized)
        self.assert_invalid(oversized)
        self.assertEqual(oversized, original)

    def test_edge_bound_accepts_maximum_and_fails_without_truncation(self):
        data = snapshot()
        data["edges"] = [edge(1000 + number, 1, 2) for number in range(LINEAGE_MAX_EDGES - 1)]
        data["edges"].append(edge(3000, 2, 3))
        self.assertEqual(len(normalize_lineage_snapshot(data)["edges"]), LINEAGE_MAX_EDGES)
        data["edges"].append(edge(3001, 1, 3))
        original = deepcopy(data)
        self.assert_invalid(data)
        self.assertEqual(data, original)


class TransferV21ProtocolTests(SimpleTestCase):
    def test_exact_supported_versions_and_unchanged_default(self):
        self.assertEqual(SUPPORTED_VERSIONS, {(2, 0), (2, 1)})
        self.assertEqual((PROTOCOL_MAJOR, PROTOCOL_MINOR), (2, 0))

    def test_20_serializer_fields_defaults_and_output_are_unchanged(self):
        self.assertEqual(set(TransferEnvelopeSerializer().fields), TOP_FIELDS)
        self.assertEqual(set(TransferItemSerializer().fields), ITEM_FIELDS)
        self.assertEqual(set(TransferEnvelopeV21Serializer().fields), TOP_FIELDS)
        self.assertEqual(set(TransferItemV21Serializer().fields), ITEM_FIELDS | {"lineage"})
        data = payload(0)
        del data["destination_institution_id"]
        del data["destination_institution_name"]
        serializer = TransferEnvelopeSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertIsNone(serializer.validated_data["destination_institution_id"])
        self.assertEqual(serializer.validated_data["destination_institution_name"], "")
        self.assertEqual(dict(serializer.data), payload(0))
        self.assertEqual(parse_transfer_envelope(data), serializer.validated_data)
        self.assertEqual(serialize_transfer_envelope(stored_envelope(0)), payload(0))

    def test_21_dispatch_typed_values_zero_and_required_lineage_on_every_item(self):
        data = payload(1, 3)
        original = deepcopy(data)
        parsed = parse_transfer_envelope(data)
        self.assertEqual(data, original)
        self.assertEqual(parsed["protocol_minor"], 1)
        self.assertIsInstance(parsed["transfer_id"], UUID)
        self.assertEqual(parsed["items"][0]["declared_polyp_quantity"], 0)
        for item in parsed["items"]:
            self.assertEqual(set(item), ITEM_FIELDS | {"lineage"})
            self.assertIsInstance(item["lineage"]["root_node_id"], UUID)
        for index in range(3):
            for value in ("missing", None, {}):
                with self.subTest(index=index, value=value):
                    invalid = deepcopy(data)
                    if value == "missing":
                        del invalid["items"][index]["lineage"]
                    else:
                        invalid["items"][index]["lineage"] = value
                    with self.assertRaises(ValidationError):
                        parse_transfer_envelope(invalid)

    def test_20_rejects_lineage_even_if_null(self):
        for value in (snapshot(), None, {}):
            with self.subTest(value=value):
                data = payload(0)
                data["items"][0]["lineage"] = value
                with self.assertRaises(ValidationError):
                    parse_transfer_envelope(data)

    def test_each_serializer_class_only_validates_its_exact_version(self):
        for serializer_class, data in (
            (TransferEnvelopeSerializer, payload(1)),
            (TransferEnvelopeV21Serializer, payload(0)),
        ):
            with self.subTest(serializer=serializer_class.__name__):
                self.assertFalse(serializer_class(data=data).is_valid())

    def test_strict_version_integers_unsupported_versions_and_missing_versions(self):
        for field, values in (
            ("protocol_major", (True, False, "2", 2.0, None, -1, 0, 1, 3, [], {})),
            ("protocol_minor", (True, False, "1", "0", 1.0, 0.0, None, -1, 2, 999, [], {})),
        ):
            for value in values:
                with self.subTest(field=field, value=value):
                    data = payload()
                    data[field] = value
                    with self.assertRaises(ValidationError):
                        parse_transfer_envelope(data)
            data = payload()
            del data[field]
            with self.assertRaises(ValidationError):
                parse_transfer_envelope(data)
        for value in (None, [], 1, "envelope"):
            with self.subTest(envelope=value), self.assertRaises(ValidationError):
                parse_transfer_envelope(value)

    def test_21_preserves_existing_protocol_validation(self):
        for scope, field, value in (
            ("top", "protocol", "polypbase.box_transfer.v1"),
            ("top", "protocol", None),
            ("top", "metadata", {}),
            ("top", "items", []),
            ("top", "transfer_id", True),
            ("item", "source_box_id", 1),
            ("item", "declared_polyp_quantity", True),
            ("item", "declared_polyp_quantity", -1),
            ("item", "species_scientific_name", 1),
        ):
            with self.subTest(scope=scope, field=field, value=value):
                data = payload()
                target = data if scope == "top" else data["items"][0]
                target[field] = value
                with self.assertRaises(ValidationError):
                    parse_transfer_envelope(data)
        data = payload(1, 2)
        data["items"][1]["item_id"] = UUID(data["items"][0]["item_id"])
        with self.assertRaises(ValidationError):
            parse_transfer_envelope(data)
        data = payload()
        del data["destination_institution_id"]
        del data["destination_institution_name"]
        parsed = parse_transfer_envelope(data)
        self.assertIsNone(parsed["destination_institution_id"])
        self.assertEqual(parsed["destination_institution_name"], "")

    def test_21_nested_lineage_validation_is_not_bypassed(self):
        for mutation in ("cycle", "unknown", "duplicate", "provenance"):
            with self.subTest(mutation=mutation):
                data = payload()
                lineage = data["items"][0]["lineage"]
                if mutation == "cycle":
                    lineage["edges"].append(edge(13, 3, 1))
                elif mutation == "unknown":
                    lineage["nodes"][0]["box_id"] = 1
                elif mutation == "duplicate":
                    lineage["nodes"].append({"node_id": UUID(identity(1))})
                else:
                    del lineage["edges"][0]["transfer_id"]
                with self.assertRaises(ValidationError):
                    parse_transfer_envelope(data)

    def test_21_accepts_identical_shared_ancestry_with_normalized_ids_and_different_roots(self):
        data = payload(1, 2)
        second = data["items"][1]["lineage"]
        second["root_node_id"] = identity(4)
        second["nodes"].append({"node_id": identity(4)})
        second["edges"].append(edge(13, 3, 4))
        for shared_edge in second["edges"][:2]:
            for field, value in list(shared_edge.items()):
                if field.endswith("_id"):
                    shared_edge[field] = value.replace("-", "").upper()
        second["nodes"].reverse()
        original = deepcopy(data)
        parsed = parse_transfer_envelope(data)
        self.assertEqual(data, original)
        first_lineage, second_lineage = [item["lineage"] for item in parsed["items"]]
        self.assertNotEqual(first_lineage["root_node_id"], second_lineage["root_node_id"])
        self.assertEqual(first_lineage["edges"], second_lineage["edges"][:2])
        self.assertEqual(len(second_lineage["edges"]), 3)

    def test_21_rejects_same_edge_id_changes_to_each_endpoint_type_and_provenance_field(self):
        for field, value in (
            ("source_node_id", identity(1)),
            ("target_node_id", identity(1)),
            ("relationship_type", "other"),
            ("transfer_id", identity(5002)),
            ("item_id", identity(5003)),
        ):
            with self.subTest(field=field):
                data = payload(1, 2)
                for item in data["items"]:
                    item["lineage"]["edges"] = [edge(11, 1, 3), edge(12, 2, 3, "transfer")]
                second = data["items"][1]["lineage"]
                changed_edge = second["edges"][1]
                changed_edge["edge_id"] = identity(12).replace("-", "").upper()
                changed_edge[field] = value
                if field == "source_node_id":
                    second["edges"].append(edge(13, 2, 3))
                elif field == "relationship_type":
                    del changed_edge["transfer_id"]
                    del changed_edge["item_id"]
                for item in data["items"]:
                    normalize_lineage_snapshot(item["lineage"])
                with self.assertRaises(ValidationError) as error:
                    parse_transfer_envelope(data)
                self.assertIn("Conflicting lineage edge identity", str(error.exception.detail["items"]))

    def test_21_rejects_cyclic_union_of_valid_dags_including_disconnected_components(self):
        for cycle_size in (2, 3):
            with self.subTest(cycle_size=cycle_size):
                data = payload(1, cycle_size + 1)
                data["items"][0]["lineage"] = {
                    "root_node_id": identity(9), "nodes": [{"node_id": identity(9)}], "edges": [],
                }
                for index, item in enumerate(data["items"][1:]):
                    source, target = index + 1, (index + 1) % cycle_size + 1
                    item["lineage"] = {
                        "root_node_id": identity(target),
                        "nodes": [{"node_id": identity(source)}, {"node_id": identity(target)}],
                        "edges": [edge(20 + index, source, target)],
                    }
                for item in data["items"]:
                    normalize_lineage_snapshot(item["lineage"])
                with self.assertRaises(ValidationError) as error:
                    parse_transfer_envelope(data)
                self.assertIn("Combined lineage graph contains a cycle", str(error.exception.detail["items"]))

    def test_21_accepts_disjoint_isolated_roots(self):
        data = payload(1, 3)
        for index, item in enumerate(data["items"]):
            root = identity(10 + index)
            item["lineage"] = {"root_node_id": root, "nodes": [{"node_id": root}], "edges": []}
        parsed = parse_transfer_envelope(data)
        self.assertEqual(len({item["lineage"]["root_node_id"] for item in parsed["items"]}), 3)
        self.assertTrue(all(item["lineage"]["edges"] == [] for item in parsed["items"]))

    def test_21_union_has_no_combined_per_item_limits_or_recursive_depth_limit(self):
        data = payload(1, 5)
        for index, item in enumerate(data["items"]):
            start = 1 + index * (LINEAGE_MAX_NODES - 1)
            root = start + LINEAGE_MAX_NODES - 1
            item["lineage"] = {
                "root_node_id": identity(root),
                "nodes": [{"node_id": identity(number)} for number in range(start, root + 1)],
                "edges": [edge(10000 + number, number, number + 1) for number in range(start, root)],
            }
        parsed = parse_transfer_envelope(data)
        node_ids = {node["node_id"] for item in parsed["items"] for node in item["lineage"]["nodes"]}
        edge_ids = {item_edge["edge_id"] for item in parsed["items"] for item_edge in item["lineage"]["edges"]}
        self.assertEqual(len(node_ids), 1 + 5 * (LINEAGE_MAX_NODES - 1))
        self.assertGreater(len(node_ids), LINEAGE_MAX_NODES)
        self.assertGreater(len(edge_ids), LINEAGE_MAX_EDGES)

    def test_21_serialization_rejects_cross_item_edge_conflicts_and_union_cycles(self):
        for failure in ("conflict", "cycle"):
            with self.subTest(failure=failure):
                envelope = stored_envelope(1, 2)
                items = sorted(envelope.items.items, key=lambda item: item.pk)
                if failure == "conflict":
                    items[1].lineage_snapshot["edges"][1]["transfer_id"] = identity(5002)
                else:
                    for index, item in enumerate(items):
                        source, target = index + 1, 2 - index
                        item.lineage_snapshot = {
                            "root_node_id": identity(target),
                            "nodes": [{"node_id": identity(source)}, {"node_id": identity(target)}],
                            "edges": [edge(20 + index, source, target)],
                        }
                for item in items:
                    normalize_lineage_snapshot(item.lineage_snapshot)
                with self.assertRaises(ValidationError):
                    serialize_transfer_envelope(envelope)
                self.assertEqual(envelope.protocol_minor, 1)

    def test_21_serialization_uses_only_persisted_snapshots_and_preserves_item_order(self):
        envelope = stored_envelope(1, 2)
        original = deepcopy(envelope.items.items[0].lineage_snapshot)
        serialized = serialize_transfer_envelope(envelope)
        expected = payload(1, 2)
        for item in expected["items"]:
            item["lineage"] = normalize_lineage_snapshot(item["lineage"])
        self.assertEqual(serialized, expected)
        self.assertEqual(envelope.items.ordering, "pk")
        self.assertEqual(envelope.items.items[0].lineage_snapshot, original)
        self.assertEqual(parse_transfer_envelope(json.loads(json.dumps(serialized))),
                         parse_transfer_envelope(expected))

    def test_21_missing_null_or_invalid_snapshot_fails_without_downgrade(self):
        for value in ("missing", None, {}, {"root_node_id": identity(3), "nodes": [], "edges": []}):
            with self.subTest(value=value):
                envelope = stored_envelope()
                item = envelope.items.items[0]
                if value == "missing":
                    del item.lineage_snapshot
                else:
                    item.lineage_snapshot = value
                with self.assertRaises(ValidationError):
                    serialize_transfer_envelope(envelope)
                self.assertEqual(envelope.protocol_minor, 1)

    def test_20_null_absent_or_invalid_snapshot_never_changes_output(self):
        for value in ("missing", None, {}, snapshot()):
            with self.subTest(value=value):
                envelope = stored_envelope(0)
                item = envelope.items.items[0]
                if value == "missing":
                    del item.lineage_snapshot
                else:
                    item.lineage_snapshot = value
                self.assertEqual(serialize_transfer_envelope(envelope), payload(0))

    def test_20_does_not_even_access_lineage_snapshot(self):
        class LegacyItem(SnapshotItem):
            @property
            def lineage_snapshot(self):
                raise AssertionError("2.0 must not read lineage snapshots.")

        envelope = stored_envelope(0)
        item = envelope.items.items[0]
        fields = {key: value for key, value in vars(item).items() if key != "lineage_snapshot"}
        envelope.items = SnapshotItems([LegacyItem(**fields)])
        self.assertEqual(serialize_transfer_envelope(envelope), payload(0))

    def test_serialization_rejects_unsupported_or_coerced_versions(self):
        for major, minor in ((3, 0), (2, 2), (True, 0), (2, True), (2, 0.0), (2, "1")):
            with self.subTest(major=major, minor=minor):
                envelope = stored_envelope(0)
                envelope.protocol_major, envelope.protocol_minor = major, minor
                with self.assertRaises(ValidationError):
                    serialize_transfer_envelope(envelope)
                self.assertIsNone(envelope.items.ordering)

    def test_parser_normalizer_and_snapshot_serialization_open_no_database_cursor(self):
        envelopes = [stored_envelope(minor) for minor in (0, 1)]
        with patch.object(
            connections["default"], "cursor", side_effect=AssertionError("Unexpected database query"),
        ) as cursor:
            normalize_lineage_snapshot(snapshot())
            for minor, envelope in enumerate(envelopes):
                parse_transfer_envelope(payload(minor))
                serialize_transfer_envelope(envelope)
            with self.assertRaises(ValidationError):
                normalize_lineage_snapshot({})
            with self.assertRaises(ValidationError):
                parse_transfer_envelope(payload(2))
            envelopes[1].items.items[0].lineage_snapshot = None
            with self.assertRaises(ValidationError):
                serialize_transfer_envelope(envelopes[1])
            cursor.assert_not_called()
