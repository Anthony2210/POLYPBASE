from uuid import UUID, uuid4

from django.db import IntegrityError, connection, models, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.organizations.models import Organization
from apps.taxonomy.models import Species, Strain

from .management.commands.import_bdd_csv import Command as ImportBddCsvCommand
from .models import (
    Box, BoxLineage, PortableLineageEdge, PortableLineageNode,
    SubcultureEvent, TransferEnvelope, TransferItem,
)


class PortableLineageModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.organization = Organization.objects.create(name="Lineage laboratory")
        cls.foreign = Organization.objects.create(name="Other lineage laboratory")
        cls.species = Species.objects.create(scientific_name="Lineage species")
        cls.strain = Strain.objects.create(
            organization=cls.organization, species=cls.species, code="LINEAGE",
        )
        cls.parent_box = Box.objects.create(
            organization=cls.organization, strain=cls.strain,
            global_code="LINEAGE.001", box_number="1",
        )
        cls.child_box = Box.objects.create(
            organization=cls.organization, strain=cls.strain,
            global_code="LINEAGE.002", box_number="2",
        )
        cls.lineage = BoxLineage.objects.create(parent_box=cls.parent_box, child_box=cls.child_box)
        cls.source = PortableLineageNode.objects.create(organization=cls.organization)
        cls.target = PortableLineageNode.objects.create(organization=cls.organization)

    def edge(self, **kwargs):
        return PortableLineageEdge.objects.create(**{
            "organization": self.organization,
            "source_node": self.source,
            "target_node": self.target,
            **kwargs,
        })

    def item(self, **kwargs):
        envelope = TransferEnvelope.objects.create(
            source_organization=self.organization,
            source_institution_id=self.organization.portable_id,
            source_institution_name=self.organization.name,
        )
        return TransferItem.objects.create(**{
            "envelope": envelope,
            "source_box": self.parent_box,
            "source_box_code": self.parent_box.global_code,
            "source_strain_code": self.strain.code,
            "species_scientific_name": self.species.scientific_name,
            "global_strain_id": uuid4(),
            "declared_polyp_quantity": 0,
            **kwargs,
        })

    def test_uuid4_defaults_are_not_globally_unique_fields(self):
        edge = self.edge()
        for model, name, objects in (
            (PortableLineageNode, "node_id", (self.source, self.target)),
            (PortableLineageEdge, "edge_id", (edge, self.edge())),
        ):
            field = model._meta.get_field(name)
            self.assertIs(field.default, uuid4)
            self.assertFalse(field.editable)
            self.assertFalse(field.unique)
            identities = [getattr(obj, name) for obj in objects]
            self.assertEqual(len(set(identities)), len(identities))
            for identity in identities:
                self.assertIsInstance(identity, UUID)
                self.assertEqual(identity.version, 4)

    def test_node_identity_is_unique_only_within_organization(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PortableLineageNode.objects.create(
                organization=self.organization, node_id=self.source.node_id,
            )
        foreign = PortableLineageNode.objects.create(
            organization=self.foreign, node_id=self.source.node_id,
        )
        self.assertEqual(foreign.node_id, self.source.node_id)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PortableLineageNode.objects.filter(pk=self.target.pk).update(node_id=self.source.node_id)

    def test_edge_identity_is_unique_only_within_organization(self):
        edge = self.edge()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.edge(edge_id=edge.edge_id)
        foreign_source = PortableLineageNode.objects.create(organization=self.foreign)
        foreign_target = PortableLineageNode.objects.create(organization=self.foreign)
        foreign = self.edge(
            organization=self.foreign, source_node=foreign_source,
            target_node=foreign_target, edge_id=edge.edge_id,
        )
        self.assertEqual(foreign.edge_id, edge.edge_id)
        other = self.edge()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PortableLineageEdge.objects.filter(pk=other.pk).update(edge_id=edge.edge_id)

    def test_local_bindings_are_nullable_one_to_one(self):
        self.assertIsNone(self.source.local_box)
        self.source.local_box = self.parent_box
        self.source.save(update_fields=["local_box"])
        edge = self.edge(local_lineage=self.lineage)
        self.assertIsNone(self.edge().local_lineage)
        with self.assertRaises(IntegrityError), transaction.atomic():
            PortableLineageNode.objects.create(organization=self.foreign, local_box=self.parent_box)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.edge(local_lineage=self.lineage)
        self.parent_box.refresh_from_db()
        self.lineage.refresh_from_db()
        self.assertEqual(self.parent_box.portable_lineage_node, self.source)
        self.assertEqual(self.lineage.portable_lineage_edge, edge)
        self.assertFalse(PortableLineageNode._meta.get_field("local_box").editable)

    def test_relation_contracts_and_reverse_managers(self):
        edge = self.edge()
        for model, fields in (
            (PortableLineageNode, {"organization": "portable_lineage_nodes", "local_box": "portable_lineage_node"}),
            (PortableLineageEdge, {
                "organization": "portable_lineage_edges", "source_node": "outgoing_edges",
                "target_node": "incoming_edges", "local_lineage": "portable_lineage_edge",
            }),
        ):
            for name, related_name in fields.items():
                with self.subTest(model=model.__name__, field=name):
                    field = model._meta.get_field(name)
                    self.assertIs(field.remote_field.on_delete, models.PROTECT)
                    self.assertEqual(field.remote_field.related_name, related_name)
        self.assertEqual(list(self.organization.portable_lineage_nodes.order_by("pk")), [self.source, self.target])
        self.assertEqual(list(self.organization.portable_lineage_edges.all()), [edge])
        self.assertEqual(list(self.source.outgoing_edges.all()), [edge])
        self.assertEqual(list(self.target.incoming_edges.all()), [edge])

    def test_relationship_choices_extend_box_lineage_without_changing_it(self):
        biological_types = {"subculture", "sexual_reproduction", "historical_import", "other"}
        self.assertEqual(set(BoxLineage.RelationshipType.values), biological_types)
        self.assertEqual(set(PortableLineageEdge.RelationshipType.values), biological_types | {"transfer"})
        for relationship_type in biological_types:
            edge = self.edge(relationship_type=relationship_type)
            self.assertIsNone(edge.transfer_id)
            self.assertIsNone(edge.item_id)
        self.assertEqual(self.edge().relationship_type, "subculture")

    def test_invalid_relationship_types_are_rejected_by_database(self):
        for value in ("", "unknown", "TRANSFER", None):
            with self.subTest(value=value):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.edge(relationship_type=value)

    def test_self_edges_are_rejected_on_create_and_update(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.edge(target_node=self.source)
        edge = self.edge()
        with self.assertRaises(IntegrityError), transaction.atomic():
            PortableLineageEdge.objects.filter(pk=edge.pk).update(target_node=self.source)
        edge.refresh_from_db()
        self.assertEqual(edge.target_node, self.target)

    def test_transfer_requires_both_provenance_ids(self):
        for transfer_id, item_id in ((None, None), (uuid4(), None), (None, uuid4())):
            with self.subTest(transfer_id=transfer_id, item_id=item_id):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.edge(relationship_type="transfer", transfer_id=transfer_id, item_id=item_id)
        transfer_id, item_id = uuid4(), uuid4()
        edge = self.edge(relationship_type="transfer", transfer_id=transfer_id, item_id=item_id)
        edge.refresh_from_db()
        self.assertEqual((edge.transfer_id, edge.item_id), (transfer_id, item_id))
        self.assertIsNone(edge.local_lineage)

    def test_nontransfer_rejects_any_provenance_ids(self):
        for relationship_type in BoxLineage.RelationshipType.values:
            for transfer_id, item_id in ((uuid4(), None), (None, uuid4()), (uuid4(), uuid4())):
                with self.subTest(relationship_type=relationship_type, transfer_id=transfer_id, item_id=item_id):
                    with self.assertRaises(IntegrityError), transaction.atomic():
                        self.edge(relationship_type=relationship_type, transfer_id=transfer_id, item_id=item_id)

    def test_transfer_cannot_bind_local_lineage(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.edge(
                relationship_type="transfer", transfer_id=uuid4(), item_id=uuid4(),
                local_lineage=self.lineage,
            )

    def test_provenance_and_local_lineage_checks_apply_to_updates(self):
        biological = self.edge(local_lineage=self.lineage)
        transfer = self.edge(relationship_type="transfer", transfer_id=uuid4(), item_id=uuid4())
        for edge, changes in (
            (biological, {"relationship_type": "invalid"}),
            (biological, {"relationship_type": "transfer"}),
            (biological, {"transfer_id": uuid4()}),
            (biological, {"relationship_type": "transfer", "transfer_id": uuid4(), "item_id": uuid4()}),
            (transfer, {"item_id": None}),
            (transfer, {"transfer_id": None}),
            (transfer, {"relationship_type": "other"}),
            (transfer, {"local_lineage": self.lineage}),
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    PortableLineageEdge.objects.filter(pk=edge.pk).update(**changes)

    def test_no_extra_cycle_endpoint_or_provenance_uniqueness_constraints(self):
        self.edge()
        self.edge()
        self.edge(source_node=self.target, target_node=self.source)
        provenance = {"relationship_type": "transfer", "transfer_id": uuid4(), "item_id": uuid4()}
        self.edge(**provenance)
        self.edge(**provenance)
        self.assertEqual(PortableLineageEdge.objects.count(), 5)

    def test_required_columns_reject_null(self):
        for name in ("organization", "node_id"):
            with self.subTest(model="node", field=name):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    PortableLineageNode.objects.create(**{"organization": self.organization, name: None})
        for name in ("organization", "edge_id", "source_node", "target_node", "relationship_type"):
            with self.subTest(model="edge", field=name):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.edge(**{name: None})

    def test_box_deletion_is_protected_without_other_references(self):
        box = Box.objects.create(
            organization=self.organization, strain=self.strain, global_code="LINEAGE.003", box_number="3",
        )
        node = PortableLineageNode.objects.create(organization=self.organization, local_box=box)
        with self.assertRaises(ProtectedError) as caught:
            box.delete()
        self.assertIn(node, caught.exception.protected_objects)
        self.assertTrue(Box.objects.filter(pk=box.pk).exists())
        self.assertTrue(PortableLineageNode.objects.filter(pk=node.pk).exists())

    def test_lineage_and_both_endpoints_are_protected(self):
        edge = self.edge(local_lineage=self.lineage)
        for obj in (self.lineage, self.source, self.target):
            with self.subTest(model=type(obj).__name__, pk=obj.pk):
                with self.assertRaises(ProtectedError) as caught:
                    type(obj).objects.filter(pk=obj.pk).delete()
                self.assertIn(edge, caught.exception.protected_objects)
                self.assertTrue(type(obj).objects.filter(pk=obj.pk).exists())
        self.assertTrue(PortableLineageEdge.objects.filter(pk=edge.pk).exists())

    def test_organization_is_protected_by_node_and_edge_independently(self):
        node_owner = Organization.objects.create(name="Node owner")
        node = PortableLineageNode.objects.create(organization=node_owner)
        edge_owner = Organization.objects.create(name="Edge owner")
        edge = self.edge(organization=edge_owner)
        for organization, protected in ((node_owner, node), (edge_owner, edge)):
            with self.subTest(organization=organization.name):
                with self.assertRaises(ProtectedError) as caught:
                    organization.delete()
                self.assertIn(protected, caught.exception.protected_objects)
                self.assertTrue(Organization.objects.filter(pk=organization.pk).exists())

    def assert_import_reset_is_protected(self, protected):
        event = SubcultureEvent.objects.create(
            parent_box=self.parent_box, reason="Original reason", notes="Original event",
        )
        BoxLineage.objects.filter(pk=self.lineage.pk).update(
            subculture_event=event, notes="Original lineage",
        )
        preserved_models = (
            Box, BoxLineage, SubcultureEvent, PortableLineageNode, PortableLineageEdge,
        )
        before = {
            model: list(model.objects.order_by("pk").values())
            for model in preserved_models
        }
        command = ImportBddCsvCommand()
        command.counts = {}
        with self.assertRaises(ProtectedError) as caught:
            # The command's handle method supplies this transaction around the reset.
            with transaction.atomic():
                command._reset_boxes(self.organization)
        self.assertIn(protected, caught.exception.protected_objects)
        for model, rows in before.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(list(model.objects.order_by("pk").values()), rows)
        return command.counts

    def test_import_reset_rolls_back_when_local_box_bridge_protects_box(self):
        self.source.local_box = self.parent_box
        self.source.save(update_fields=["local_box"])
        edge = self.edge()
        self.assertIsNone(self.target.local_box)
        self.assertIsNone(edge.local_lineage)
        counts = self.assert_import_reset_is_protected(self.source)
        self.assertEqual(counts["lineages_deleted"], 1)
        self.assertEqual(counts["subcultures_deleted"], 1)
        self.assertNotIn("boxes_deleted", counts)

    def test_import_reset_is_protected_by_local_lineage_bridge_with_unbound_nodes(self):
        edge = self.edge(local_lineage=self.lineage)
        self.assertFalse(PortableLineageNode.objects.filter(local_box__isnull=False).exists())
        counts = self.assert_import_reset_is_protected(edge)
        self.assertEqual(counts, {})

    def test_snapshot_defaults_to_null_and_preserves_json_and_zero(self):
        field = TransferItem._meta.get_field("lineage_snapshot")
        self.assertIsInstance(field, models.JSONField)
        self.assertTrue(field.null)
        self.assertFalse(field.editable)
        self.assertIsNone(field.default)
        snapshot = {"nodes": [], "edges": [], "quantity": 0, "unknown": None}
        for value in (None, snapshot, {}):
            with self.subTest(value=value):
                item = self.item() if value is None else self.item(lineage_snapshot=value)
                item.refresh_from_db()
                self.assertEqual(item.lineage_snapshot, value)
                self.assertEqual(item.declared_polyp_quantity, 0)
        self.assertEqual(PortableLineageNode.objects.count(), 2)
        self.assertFalse(PortableLineageEdge.objects.exists())

    def test_named_constraints_exist_in_database(self):
        for model, names in (
            (PortableLineageNode, {"portable_node_identity_unique"}),
            (PortableLineageEdge, {
                "portable_edge_identity_unique", "portable_edge_not_self", "portable_edge_type_valid",
                "portable_edge_provenance_valid", "portable_transfer_no_local_lineage",
            }),
        ):
            with connection.cursor() as cursor:
                constraints = connection.introspection.get_constraints(cursor, model._meta.db_table)
            self.assertTrue(names.issubset(constraints), names - constraints.keys())
            for name in names:
                self.assertTrue(constraints[name]["unique"] if "identity" in name else constraints[name]["check"])
