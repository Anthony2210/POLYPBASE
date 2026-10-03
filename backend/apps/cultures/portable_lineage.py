"""Institution-local portable knowledge; no receipt, import or operational lookup by UUID."""

from contextvars import ContextVar
from functools import wraps
import time
import uuid

from django.db import DatabaseError, connection, transaction
from rest_framework.exceptions import ValidationError

from .models import Box, BoxLineage, PortableLineageEdge, PortableLineageNode
from .transfer_v2_protocol import (
    LINEAGE_MAX_EDGES, LINEAGE_MAX_NODES, LINEAGE_RELATIONSHIP_TYPES,
    normalize_lineage_snapshot,
)


_lineage_transaction = ContextVar("portable_lineage_transaction", default=False)
TRANSACTION_ATTEMPTS = 5


def _conflict(message):
    raise ValidationError({"lineage": message}, code="source_lineage_conflict")


def _retryable(error):
    cause = error.__cause__
    state = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    if state in {"40001", "40P01"}:
        return True
    # A concurrent insert can win a bridge unique index but be invisible to our
    # transaction snapshot. Retry the whole transaction, never just that query.
    name = getattr(getattr(cause, "diag", None), "constraint_name", "") or ""
    return state == "23505" and (
        name in {"portable_node_identity_unique", "portable_edge_identity_unique"}
        or name.startswith("cultures_portablelineage")
    )


def consistent_lineage_transaction(function):
    """Own a scoped PostgreSQL SERIALIZABLE transaction and retry it in full.

    Ordinary lineage writers need no new lock protocol: all reads, including
    predicates for absent predecessors, see one database snapshot. PostgreSQL
    may reject a conflicting serial order, in which case all writes are retried.
    A caller-owned PostgreSQL transaction cannot safely change its isolation or
    be retried here, so it is rejected before any source read or write.
    SQLite supports isolated functional tests only, not this consistency claim.
    """
    @wraps(function)
    def run(*args, **kwargs):
        if _lineage_transaction.get():
            return function(*args, **kwargs)
        if connection.vendor == "postgresql" and (
            connection.in_atomic_block or not connection.get_autocommit()
        ):
            _conflict("Portable lineage requires its own transaction.")
        for attempt in range(TRANSACTION_ATTEMPTS):
            try:
                with transaction.atomic():
                    if connection.vendor == "postgresql":
                        with connection.cursor() as cursor:
                            cursor.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                    token = _lineage_transaction.set(True)
                    try:
                        result = function(*args, **kwargs)
                    finally:
                        _lineage_transaction.reset(token)
                return result
            except DatabaseError as error:
                if connection.vendor != "postgresql" or not _retryable(error):
                    raise
                if attempt == TRANSACTION_ATTEMPTS - 1:
                    _conflict("Concurrent lineage changes prevented a consistent snapshot; retry.")
                time.sleep(0.01 * (2 ** attempt))
    return run


def _uuid(value):
    try:
        if not isinstance(value, (str, uuid.UUID)):
            raise ValueError
        return uuid.UUID(str(value))
    except (ValueError, AttributeError) as error:
        raise ValidationError({"lineage": "Expected an opaque UUID."}) from error


def _local_box(organization, box_id):
    # Never inspect foreign operational fields. UUIDs are not Box lookup keys.
    box = Box.objects.filter(pk=box_id, organization_id=organization.pk).only(
        "id", "organization_id",
    ).first()
    if box is None:
        _conflict("Local Box is not in the projection organization.")
    return box


def _node(organization, node_pk):
    node = PortableLineageNode.objects.filter(
        pk=node_pk, organization_id=organization.pk,
    ).first()
    if node is None:
        _conflict("Node is not in the projection organization.")
    if node.local_box_id is not None:
        _local_box(organization, node.local_box_id)
    return node


@consistent_lineage_transaction
def assert_node(*, organization, node_id, local_box=None):
    """Record an internal node assertion, or reuse identical established knowledge.

    This is not an incoming-package merge. An omitted bridge preserves any local
    association; an explicit Box must already be institution-scoped by callers.
    """
    node_id = _uuid(node_id)
    if local_box is not None:
        local_box = _local_box(organization, local_box.pk)
        if PortableLineageNode.objects.filter(local_box=local_box).exclude(
            organization=organization, node_id=node_id,
        ).exists():
            _conflict("Local Box already has a different portable identity.")
    node, _ = PortableLineageNode.objects.get_or_create(
        organization=organization, node_id=node_id,
        defaults={"local_box": local_box},
    )
    node = _node(organization, node.pk)
    if local_box is not None:
        if node.local_box_id not in (None, local_box.pk):
            _conflict("An established local Box bridge cannot be rebound.")
        other = PortableLineageNode.objects.filter(local_box=local_box).exclude(pk=node.pk)
        if other.exists():
            _conflict("Local Box already has a different portable identity.")
        if node.local_box_id is None:
            # Serialize attachment to an existing assertion as well as inserts.
            node = PortableLineageNode.objects.select_for_update().get(pk=node.pk)
            if node.local_box_id not in (None, local_box.pk):
                _conflict("An established local Box bridge cannot be rebound.")
            node.local_box = local_box
            node.save(update_fields=["local_box"])
    return node


@consistent_lineage_transaction
def project_local_box(*, organization, box):
    box = _local_box(organization, box.pk)
    node, _ = PortableLineageNode.objects.get_or_create(
        local_box=box, defaults={"organization": organization, "node_id": uuid.uuid4()},
    )
    return _node(organization, node.pk)


def _lineage(organization, lineage_pk):
    row = BoxLineage.objects.filter(pk=lineage_pk).values(
        "id", "parent_box_id", "child_box_id", "relationship_type",
    ).first()
    if row is None:
        _conflict("Local lineage bridge no longer exists.")
    _local_box(organization, row["parent_box_id"])
    _local_box(organization, row["child_box_id"])
    return row


def _validate_edge(organization, edge):
    if edge.organization_id != organization.pk:
        _conflict("Edge is not in the projection organization.")
    source = _node(organization, edge.source_node_id)
    target = _node(organization, edge.target_node_id)
    if source.pk == target.pk:
        _conflict("Self edges are not allowed.")
    if edge.relationship_type not in LINEAGE_RELATIONSHIP_TYPES:
        _conflict("Unknown lineage relationship.")
    if edge.relationship_type == "transfer":
        if edge.transfer_id is None or edge.item_id is None or edge.local_lineage_id is not None:
            _conflict("Transfer edges require provenance and cannot bridge BoxLineage.")
    elif edge.transfer_id is not None or edge.item_id is not None:
        _conflict("Non-transfer edges cannot carry transfer provenance.")
    if edge.local_lineage_id is not None:
        row = _lineage(organization, edge.local_lineage_id)
        if (
            source.local_box_id != row["parent_box_id"]
            or target.local_box_id != row["child_box_id"]
            or edge.relationship_type != row["relationship_type"]
        ):
            _conflict("Established edge disagrees with its current local lineage bridge.")
    return source, target


@consistent_lineage_transaction
def assert_edge(
    *, organization, edge_id, source_node, target_node, relationship_type,
    local_lineage=None, transfer_id=None, item_id=None,
):
    """Store an internal explicit assertion; never overwrite a same-ID assertion."""
    source_node = _node(organization, source_node.pk)
    target_node = _node(organization, target_node.pk)
    candidate = PortableLineageEdge(
        organization=organization, edge_id=_uuid(edge_id),
        source_node=source_node, target_node=target_node,
        relationship_type=relationship_type, local_lineage=local_lineage,
        transfer_id=_uuid(transfer_id) if transfer_id is not None else None,
        item_id=_uuid(item_id) if item_id is not None else None,
    )
    _validate_edge(organization, candidate)
    if local_lineage is not None:
        other = PortableLineageEdge.objects.filter(local_lineage=local_lineage).exclude(
            organization=organization, edge_id=candidate.edge_id,
        )
        if other.exists():
            _conflict("Local lineage already has a different portable identity.")
    edge, _ = PortableLineageEdge.objects.get_or_create(
        organization=organization, edge_id=candidate.edge_id,
        defaults={
            "source_node": source_node, "target_node": target_node,
            "relationship_type": relationship_type, "local_lineage": local_lineage,
            "transfer_id": candidate.transfer_id, "item_id": candidate.item_id,
        },
    )
    _validate_edge(organization, edge)
    for field in (
        "source_node_id", "target_node_id", "relationship_type", "transfer_id", "item_id",
    ):
        if getattr(edge, field) != getattr(candidate, field):
            _conflict("Same edge identity has contradictory assertions.")
    if local_lineage is not None:
        if edge.local_lineage_id not in (None, local_lineage.pk):
            _conflict("An established local lineage bridge cannot be rebound.")
        if edge.local_lineage_id is None:
            edge = PortableLineageEdge.objects.select_for_update().get(pk=edge.pk)
            if edge.local_lineage_id not in (None, local_lineage.pk):
                _conflict("An established local lineage bridge cannot be rebound.")
            edge.local_lineage = local_lineage
            edge.save(update_fields=["local_lineage"])
    return edge


@consistent_lineage_transaction
def project_local_lineage(*, organization, lineage):
    row = _lineage(organization, lineage.pk)
    if row["relationship_type"] not in LINEAGE_RELATIONSHIP_TYPES or row["relationship_type"] == "transfer":
        _conflict("Local lineage has an unsupported relationship type.")
    source = project_local_box(
        organization=organization, box=Box(pk=row["parent_box_id"]),
    )
    target = project_local_box(
        organization=organization, box=Box(pk=row["child_box_id"]),
    )
    edge, _ = PortableLineageEdge.objects.get_or_create(
        local_lineage_id=row["id"],
        defaults={
            "organization": organization, "edge_id": uuid.uuid4(),
            "source_node": source, "target_node": target,
            "relationship_type": row["relationship_type"],
        },
    )
    _validate_edge(organization, edge)
    return edge


@consistent_lineage_transaction
def build_known_ancestry(
    *, organization, source_box, max_nodes=LINEAGE_MAX_NODES, max_edges=LINEAGE_MAX_EDGES,
):
    """Freeze only explicit directed predecessors, not a connected component."""
    root = project_local_box(organization=organization, box=source_box)
    nodes = {root.pk: root}
    edges = {}
    pending = [root.pk]
    visited = set()

    def include(edge):
        source, target = _validate_edge(organization, edge)
        edges[edge.pk] = edge
        for node in (source, target):
            if node.pk not in nodes:
                nodes[node.pk] = node
                pending.append(node.pk)
        if len(nodes) > max_nodes or len(edges) > max_edges:
            _conflict("Known ancestry exceeds the portable graph limit.")

    while pending:
        node_pk = pending.pop()
        if node_pk in visited:
            continue
        visited.add(node_pk)
        node = _node(organization, node_pk)
        if len(nodes) > max_nodes:
            _conflict("Known ancestry exceeds the portable graph limit.")
        if node.local_box_id is not None:
            # Do not filter away foreign parent FKs: encounter and fail closed.
            local_rows = list(BoxLineage.objects.filter(
                child_box_id=node.local_box_id,
            ).order_by("pk").values_list("pk", flat=True)[:max_edges + 1])
            if len(local_rows) > max_edges:
                _conflict("Known ancestry exceeds the portable graph limit.")
            for lineage_pk in local_rows:
                include(project_local_lineage(
                    organization=organization, lineage=BoxLineage(pk=lineage_pk),
                ))
        incoming = list(PortableLineageEdge.objects.filter(
            organization=organization, target_node_id=node.pk,
        ).order_by("pk")[:max_edges + 1])
        if len(incoming) > max_edges:
            _conflict("Known ancestry exceeds the portable graph limit.")
        for edge in incoming:
            include(edge)

    snapshot = {
        "root_node_id": str(root.node_id),
        "nodes": [{"node_id": str(node.node_id)} for node in nodes.values()],
        "edges": [],
    }
    for edge in edges.values():
        data = {
            "edge_id": str(edge.edge_id),
            "source_node_id": str(nodes[edge.source_node_id].node_id),
            "target_node_id": str(nodes[edge.target_node_id].node_id),
            "relationship_type": edge.relationship_type,
        }
        if edge.relationship_type == "transfer":
            data.update(transfer_id=str(edge.transfer_id), item_id=str(edge.item_id))
        snapshot["edges"].append(data)
    # Validate the union, including cycles involving both local and portable facts.
    return normalize_lineage_snapshot(snapshot)
