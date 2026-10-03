"""Transport-neutral v2 contract. Parsing establishes structure, not trust or access."""

from collections import deque
from collections.abc import Mapping
from datetime import timezone
import uuid

from rest_framework import serializers


PROTOCOL = "polypbase.transfer"
PROTOCOL_MAJOR = 2
PROTOCOL_MINOR = 0
MAX_QUANTITY = 2147483647
SUPPORTED_VERSIONS = {(2, 0), (2, 1)}
LINEAGE_MAX_NODES = 250
LINEAGE_MAX_EDGES = 1000
LINEAGE_RELATIONSHIP_TYPES = (
    "subculture", "sexual_reproduction", "historical_import", "other", "transfer",
)


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, Mapping):
            raise serializers.ValidationError({"non_field_errors": ["Expected an object."]})
        unexpected = set(data) - set(self.fields)
        if unexpected:
            raise serializers.ValidationError({str(key): "Unknown field." for key in unexpected})
        return super().to_internal_value(data)


class StrictIntegerField(serializers.IntegerField):
    def to_internal_value(self, data):
        if type(data) is not int:
            raise serializers.ValidationError("Expected an integer.")
        return super().to_internal_value(data)


class StrictStringField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            raise serializers.ValidationError("Expected a string.")
        return super().to_internal_value(data)


class StrictUUIDField(serializers.UUIDField):
    def to_internal_value(self, data):
        if not isinstance(data, (str, uuid.UUID)):
            raise serializers.ValidationError("Expected a UUID.")
        return super().to_internal_value(data)


class LineageListSerializer(serializers.ListSerializer):
    def to_internal_value(self, data):
        if not isinstance(data, list):
            raise serializers.ValidationError("Expected a list.")
        return super().to_internal_value(data)


class LineageNodeSerializer(StrictSerializer):
    node_id = StrictUUIDField()

    class Meta:
        list_serializer_class = LineageListSerializer


class LineageEdgeSerializer(StrictSerializer):
    edge_id = StrictUUIDField()
    source_node_id = StrictUUIDField()
    target_node_id = StrictUUIDField()
    relationship_type = serializers.ChoiceField(choices=LINEAGE_RELATIONSHIP_TYPES)
    transfer_id = StrictUUIDField(required=False)
    item_id = StrictUUIDField(required=False)

    class Meta:
        list_serializer_class = LineageListSerializer

    def validate(self, attrs):
        provenance = {"transfer_id", "item_id"}
        present = provenance & attrs.keys()
        if attrs["relationship_type"] == "transfer":
            missing = provenance - present
            if missing:
                raise serializers.ValidationError({key: "This field is required." for key in missing})
        elif present:
            raise serializers.ValidationError({
                key: "Provenance is only allowed on transfer edges." for key in present
            })
        return attrs


class LineageSnapshotSerializer(StrictSerializer):
    """Minimal portable ancestor DAG; each node must have a directed path to root."""

    root_node_id = StrictUUIDField()
    nodes = LineageNodeSerializer(many=True, allow_empty=False, max_length=LINEAGE_MAX_NODES)
    edges = LineageEdgeSerializer(many=True, max_length=LINEAGE_MAX_EDGES)

    def validate(self, attrs):
        node_ids = [node["node_id"] for node in attrs["nodes"]]
        node_set = set(node_ids)
        if len(node_set) != len(node_ids):
            raise serializers.ValidationError({"nodes": "Duplicate node identity."})
        if attrs["root_node_id"] not in node_set:
            raise serializers.ValidationError({"root_node_id": "Root node must exist."})
        edge_ids = [edge["edge_id"] for edge in attrs["edges"]]
        if len(set(edge_ids)) != len(edge_ids):
            raise serializers.ValidationError({"edges": "Duplicate edge identity."})

        successors = {node_id: [] for node_id in node_set}
        predecessors = {node_id: [] for node_id in node_set}
        indegrees = dict.fromkeys(node_set, 0)
        for edge in attrs["edges"]:
            source, target = edge["source_node_id"], edge["target_node_id"]
            if source not in node_set or target not in node_set:
                raise serializers.ValidationError({"edges": "Edge endpoints must exist."})
            if source == target:
                raise serializers.ValidationError({"edges": "Self edges are not allowed."})
            successors[source].append(target)
            predecessors[target].append(source)
            indegrees[target] += 1

        # Kahn's algorithm checks all components without recursive traversal.
        pending = deque(node_id for node_id, degree in indegrees.items() if degree == 0)
        visited_count = 0
        while pending:
            node_id = pending.popleft()
            visited_count += 1
            for target in successors[node_id]:
                indegrees[target] -= 1
                if indegrees[target] == 0:
                    pending.append(target)
        if visited_count != len(node_set):
            raise serializers.ValidationError({"edges": "Cycles are not allowed."})

        reachable = {attrs["root_node_id"]}
        pending = deque(reachable)
        while pending:
            for source in predecessors[pending.popleft()]:
                if source not in reachable:
                    reachable.add(source)
                    pending.append(source)
        if reachable != node_set:
            raise serializers.ValidationError({"nodes": "Every node must reach the root."})

        attrs["nodes"] = sorted(attrs["nodes"], key=lambda node: str(node["node_id"]))
        attrs["edges"] = sorted(attrs["edges"], key=lambda edge: str(edge["edge_id"]))
        return attrs


def normalize_lineage_snapshot(data):
    """Validate without database access and return canonical minimal JSON data."""
    serializer = LineageSnapshotSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.data)


class TransferItemSerializer(StrictSerializer):
    item_id = StrictUUIDField()
    source_box_code = StrictStringField(max_length=100, trim_whitespace=False)
    source_strain_code = StrictStringField(max_length=80, trim_whitespace=False)
    species_scientific_name = StrictStringField(max_length=150, trim_whitespace=False)
    global_strain_id = StrictUUIDField()
    declared_polyp_quantity = StrictIntegerField(min_value=0, max_value=MAX_QUANTITY)


class TransferEnvelopeSerializer(StrictSerializer):
    expected_protocol_minor = PROTOCOL_MINOR

    protocol = StrictStringField(trim_whitespace=False)
    protocol_major = StrictIntegerField()
    protocol_minor = StrictIntegerField()
    transfer_id = StrictUUIDField()
    created_at = serializers.DateTimeField(default_timezone=timezone.utc)
    source_institution_id = StrictUUIDField()
    source_institution_name = StrictStringField(max_length=150, trim_whitespace=False)
    destination_institution_id = StrictUUIDField(allow_null=True, required=False, default=None)
    destination_institution_name = StrictStringField(
        max_length=150, allow_blank=True, trim_whitespace=False, required=False, default="",
    )
    items = TransferItemSerializer(many=True, allow_empty=False)

    def validate(self, attrs):
        # Exact version support only: a future minor needs an explicit contract.
        if attrs["protocol"] != PROTOCOL:
            raise serializers.ValidationError({"protocol": "Unsupported protocol."})
        if attrs["protocol_major"] != PROTOCOL_MAJOR:
            raise serializers.ValidationError({"protocol_major": "Unsupported major version."})
        if attrs["protocol_minor"] != self.expected_protocol_minor:
            raise serializers.ValidationError({"protocol_minor": "Unsupported minor version."})
        item_ids = [item["item_id"] for item in attrs["items"]]
        if len(set(item_ids)) != len(item_ids):
            raise serializers.ValidationError({"items": "Duplicate item identity."})
        return attrs


class TransferItemV21Serializer(TransferItemSerializer):
    lineage = LineageSnapshotSerializer()


class TransferEnvelopeV21Serializer(TransferEnvelopeSerializer):
    expected_protocol_minor = 1
    items = TransferItemV21Serializer(many=True, allow_empty=False)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        node_ids = set()
        edges_by_id = {}
        for item in attrs["items"]:
            lineage = item["lineage"]
            node_ids.update(node["node_id"] for node in lineage["nodes"])
            for edge in lineage["edges"]:
                edge_id = edge["edge_id"]
                if edge_id in edges_by_id and edges_by_id[edge_id] != edge:
                    raise serializers.ValidationError({
                        "items": f"Conflicting lineage edge identity: {edge_id}.",
                    })
                edges_by_id[edge_id] = edge

        # Check the deduplicated union without changing per-item roots or bounds.
        successors = {node_id: [] for node_id in node_ids}
        indegrees = dict.fromkeys(node_ids, 0)
        for edge in edges_by_id.values():
            source, target = edge["source_node_id"], edge["target_node_id"]
            successors[source].append(target)
            indegrees[target] += 1
        pending = deque(node_id for node_id, degree in indegrees.items() if degree == 0)
        visited_count = 0
        while pending:
            node_id = pending.popleft()
            visited_count += 1
            for target in successors[node_id]:
                indegrees[target] -= 1
                if indegrees[target] == 0:
                    pending.append(target)
        if visited_count != len(node_ids):
            raise serializers.ValidationError({"items": "Combined lineage graph contains a cycle."})
        return attrs


_ENVELOPE_SERIALIZERS = {
    (2, 0): TransferEnvelopeSerializer,
    (2, 1): TransferEnvelopeV21Serializer,
}


def _envelope_serializer_class(data):
    if not isinstance(data, Mapping):
        raise serializers.ValidationError({"non_field_errors": ["Expected an object."]})
    version = []
    for field in ("protocol_major", "protocol_minor"):
        try:
            version.append(StrictIntegerField().run_validation(data.get(field, serializers.empty)))
        except serializers.ValidationError as error:
            raise serializers.ValidationError({field: error.detail}) from error
    version = tuple(version)
    if version not in SUPPORTED_VERSIONS:
        if version[0] != PROTOCOL_MAJOR:
            raise serializers.ValidationError({"protocol_major": "Unsupported major version."})
        raise serializers.ValidationError({"protocol_minor": "Unsupported minor version."})
    return _ENVELOPE_SERIALIZERS[version]


def parse_transfer_envelope(data):
    """Validate a plain mapping without database lookup, mutation or authorization."""
    serializer = _envelope_serializer_class(data)(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def serialize_transfer_envelope(envelope):
    """Reconstruct the portable allowlist using persisted snapshots only.

    This internal helper does not authorize reads. Callers must already hold an
    authorized source package; portable UUIDs never grant access to local records.
    """
    serializer_class = _envelope_serializer_class({
        "protocol_major": envelope.protocol_major,
        "protocol_minor": envelope.protocol_minor,
    })
    data = {
        "protocol": PROTOCOL,
        "protocol_major": envelope.protocol_major,
        "protocol_minor": envelope.protocol_minor,
        "transfer_id": envelope.transfer_id,
        "created_at": envelope.created_at,
        "source_institution_id": envelope.source_institution_id,
        "source_institution_name": envelope.source_institution_name,
        "destination_institution_id": envelope.destination_institution_id,
        "destination_institution_name": envelope.destination_institution_name,
        "items": [
            {
                "item_id": item.item_id,
                "source_box_code": item.source_box_code,
                "source_strain_code": item.source_strain_code,
                "species_scientific_name": item.species_scientific_name,
                "global_strain_id": item.global_strain_id,
                "declared_polyp_quantity": item.declared_polyp_quantity,
                **({"lineage": getattr(item, "lineage_snapshot", None)}
                   if envelope.protocol_minor == 1 else {}),
            }
            for item in envelope.items.order_by("pk")
        ],
    }
    validated = parse_transfer_envelope(data)
    return dict(serializer_class(validated).data)
