import json
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import (
    BiologicalMeasurement, Observation, Probe, TemperatureMeasurement,
)
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, LocalStrainIdentity, Species, Strain

from .models import (
    Box, BoxLineage, BoxLocation, BoxMovement, BoxTransfer, BoxTransferImport,
    PortableLineageEdge, PortableLineageNode, SubcultureEvent, ThermalZone,
    TransferEnvelope, TransferItem,
)
from .portable_lineage import assert_node
from .test_portable_lineage import PortableLineageFixtures
from .test_transfer_v2 import ITEM_FIELDS, TOP_FIELDS
from .transfer_v2 import create_source_package
from .transfer_v2_protocol import parse_transfer_envelope, serialize_transfer_envelope


class TransferV21SourcePackageTests(PortableLineageFixtures):
    def lineage_package(self, selections=None, **kwargs):
        return self.package(selections, protocol_version=(2, 1), **kwargs)

    def assert_no_new_package_or_projection(self, before):
        self.assert_no_package()
        self.assertEqual(self.projection_state(), before)
        self.assertFalse(connection.needs_rollback)

    def private_laboratory_data(self):
        Box.objects.filter(pk=self.box.pk).update(
            local_code="PRIVATE LOCAL CODE", notes="PRIVATE BOX NOTES", volume_liters="1.25",
        )
        Box.objects.filter(pk=self.second_box.pk).update(notes="PRIVATE ANCESTOR NOTES")
        Strain.objects.filter(pk=self.strain.pk).update(notes="PRIVATE STRAIN NOTES")
        Species.objects.filter(pk=self.species.pk).update(notes="PRIVATE SPECIES NOTES")
        ThermalZone.objects.filter(pk=self.zone.pk).update(notes="PRIVATE ZONE NOTES")
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone, notes="PRIVATE LOCATION NOTES")
        BoxMovement.objects.create(
            box=self.box, to_thermal_zone=self.zone, user=self.actor, notes="PRIVATE MOVEMENT NOTES",
        )
        measurement = BiologicalMeasurement.objects.create(
            box=self.box, measured_on=timezone.localdate(), polyp_count=0, ephyrae_count=0,
            strobila_count=0, salinity_psu="0.00", notes="PRIVATE MEASUREMENT NOTES", user=self.actor,
        )
        Observation.objects.create(box=self.box, notes="PRIVATE OBSERVATION NOTES", user=self.actor)
        probe = Probe.objects.create(
            organization=self.source, thermal_zone=self.zone, code="PRIVATE PROBE", notes="PRIVATE PROBE NOTES",
        )
        TemperatureMeasurement.objects.create(
            probe=probe, measured_at=timezone.now(), temperature_c="0.00",
            raw_data={"private": "PRIVATE SENSOR DATA"}, user=self.actor,
        )
        return measurement

    def test_default_and_explicit_20_remain_lineage_free_and_never_project(self):
        self.lineage()
        with patch("apps.cultures.transfer_v2.build_known_ancestry", side_effect=AssertionError("2.0 must not project")):
            for kwargs in ({}, {"protocol_version": (2, 0)}):
                with self.subTest(kwargs=kwargs):
                    envelope = self.package(**kwargs)
                    self.assertEqual((envelope.protocol_major, envelope.protocol_minor), (2, 0))
                    self.assertIsNone(envelope.items.get().lineage_snapshot)
                    data = serialize_transfer_envelope(envelope)
                    self.assertEqual(set(data["items"][0]), ITEM_FIELDS)
                    self.assertNotIn("lineage", data["items"][0])
        self.assertFalse(PortableLineageNode.objects.exists())
        self.assertFalse(PortableLineageEdge.objects.exists())

    def test_source_api_requires_an_exact_supported_integer_version_tuple(self):
        before = self.projection_state()
        invalid_versions = (
            None, "2.1", [2, 1], {2, 1}, (), (2,), (2, 1, 0), (1, 1), (3, 0),
            (2, -1), (2, 2), (2, True), (2, False), (True, 1), (2.0, 1), (2, 1.0), ("2", 1),
        )
        with patch("apps.cultures.transfer_v2.build_known_ancestry") as builder:
            for version in invalid_versions:
                with self.subTest(version=version):
                    with CaptureQueriesContext(connection) as queries:
                        with self.assertRaises(ValidationError) as caught:
                            self.package(protocol_version=version)
                    self.assertIn("protocol_version", caught.exception.detail)
                    self.assertEqual(len(queries), 0)
                    self.assert_no_new_package_or_projection(before)
            builder.assert_not_called()
        with self.assertRaises(TypeError):
            create_source_package(self.actor, self.source, [], protocol_version=(2, 1))
        self.assert_no_new_package_or_projection(before)

    def test_21_requires_active_source_admin_before_any_lineage_projection(self):
        before = self.projection_state()
        with patch("apps.cultures.transfer_v2.build_known_ancestry") as builder:
            for role in (OrganizationMembership.Role.VIEWER, OrganizationMembership.Role.LAB_TECHNICIAN):
                with self.subTest(role=role):
                    OrganizationMembership.objects.filter(pk=self.membership.pk).update(role=role)
                    with self.assertRaises(PermissionDenied):
                        self.lineage_package()
                    self.assert_no_new_package_or_projection(before)
            OrganizationMembership.objects.filter(pk=self.membership.pk).update(role="admin", is_active=False)
            with self.assertRaises(PermissionDenied):
                self.lineage_package()
            OrganizationMembership.objects.filter(pk=self.membership.pk).delete()
            OrganizationMembership.objects.create(user=self.actor, organization=self.foreign, role="admin")
            with self.assertRaises(PermissionDenied):
                self.lineage_package()
            builder.assert_not_called()
        self.assert_no_new_package_or_projection(before)

    def test_21_denies_anonymous_inactive_actors_and_wrong_organization_context(self):
        before = self.projection_state()
        for actor in (None, AnonymousUser()):
            with self.subTest(actor=actor):
                with self.assertRaises(PermissionDenied):
                    self.lineage_package(actor=actor)
                self.assert_no_new_package_or_projection(before)
        with self.assertRaises(PermissionDenied):
            self.lineage_package(source_organization=self.foreign)
        for superuser in (False, True):
            with self.subTest(superuser=superuser):
                self.actor.is_active = False
                self.actor.is_superuser = superuser
                self.actor.save(update_fields=["is_active", "is_superuser"])
                with self.assertRaises(PermissionDenied):
                    self.lineage_package()
                self.assert_no_new_package_or_projection(before)

    def test_21_allows_active_superuser_but_rechecks_source_organization_activity(self):
        OrganizationMembership.objects.filter(pk=self.membership.pk).delete()
        self.actor.is_superuser = True
        self.actor.save(update_fields=["is_superuser"])
        envelope = self.lineage_package()
        self.assertEqual(envelope.created_by_id, self.actor.pk)
        before = self.projection_state()
        package_ids = list(TransferEnvelope.objects.values_list("pk", flat=True))
        Organization.objects.filter(pk=self.source.pk).update(is_active=False)
        self.assertTrue(self.source.is_active)
        with self.assertRaises(PermissionDenied):
            self.lineage_package()
        self.assertEqual(self.projection_state(), before)
        self.assertEqual(list(TransferEnvelope.objects.values_list("pk", flat=True)), package_ids)
        self.assertEqual(AuditLog.objects.count(), 1)

    def test_21_strict_selection_rejects_injected_lineage_ids_and_invalid_quantities(self):
        before = self.projection_state()
        valid = {"source_box_id": self.box.pk, "declared_polyp_quantity": 0}
        invalid = [[], {}, "boxes", [None], [{"source_box_id": self.box.pk}]]
        for field in ("source_box_id", "declared_polyp_quantity"):
            for value in (None, True, "1", 1.0, -1, {}, []):
                invalid.append([{**valid, field: value}])
        invalid.append([{**valid, "declared_polyp_quantity": 2147483648}])
        for field in ("lineage", "node_id", "edge_id", "transfer_id", "item_id", "global_strain_id"):
            invalid.append([{**valid, field: str(uuid4())}])
        for selections in invalid:
            with self.subTest(selections=selections):
                with self.assertRaises(ValidationError):
                    self.lineage_package(selections)
                self.assert_no_new_package_or_projection(before)

    def test_21_rejects_foreign_or_stale_source_boxes_even_for_superuser(self):
        self.actor.is_superuser = True
        self.actor.save(update_fields=["is_superuser"])
        before = self.projection_state()
        for box_id in (self.foreign_box.pk, 999999):
            with self.subTest(box_id=box_id):
                with self.assertRaises(ValidationError):
                    self.lineage_package([
                        {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                        {"source_box_id": box_id, "declared_polyp_quantity": 1},
                    ])
                self.assert_no_new_package_or_projection(before)
        Box.objects.filter(pk=self.box.pk).update(organization=self.foreign)
        self.assertEqual(self.box.organization_id, self.source.pk)
        with self.assertRaises(ValidationError):
            self.lineage_package()
        self.assert_no_new_package_or_projection(before)

    def test_foreign_owned_source_strain_is_rejected_only_for_21(self):
        Strain.objects.filter(pk=self.strain.pk).update(organization=self.foreign)
        self.assertEqual(self.strain.organization_id, self.source.pk)
        before = self.projection_state()
        with self.assertRaises(ValidationError) as caught:
            self.lineage_package()
        self.assertIn("Source Strain", str(caught.exception.detail))
        self.assert_no_new_package_or_projection(before)
        for kwargs in ({}, {"protocol_version": (2, 0)}):
            with self.subTest(kwargs=kwargs):
                envelope = self.package(**kwargs)
                self.assertEqual(envelope.items.get().global_strain_id, self.identity.global_id)
                self.assertIsNone(envelope.items.get().lineage_snapshot)
        self.assertEqual(self.projection_state(), before)

    def test_legacy_unowned_strain_already_used_by_source_is_eligible_for_21(self):
        Strain.objects.filter(pk=self.strain.pk).update(organization=None)
        envelope = self.lineage_package()
        item = envelope.items.get()
        self.assertEqual(item.global_strain_id, self.identity.global_id)
        self.assertEqual(item.declared_polyp_quantity, 0)
        self.assertIsNotNone(item.lineage_snapshot)
        self.strain.refresh_from_db()
        self.assertIsNone(self.strain.organization_id)

    def test_late_identityless_item_fails_without_attaching_identity_or_projection(self):
        legacy = Strain.objects.create(species=self.species, organization=self.source, code="NO-IDENTITY")
        Box.objects.filter(pk=self.second_box.pk).update(strain=legacy)
        before = self.projection_state()
        identities_before = list(GlobalStrainIdentity.objects.order_by("pk").values())
        with self.assertRaises(ValidationError):
            self.lineage_package([
                {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 1},
            ])
        self.assert_no_new_package_or_projection(before)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.global_identity_id)
        self.assertEqual(list(GlobalStrainIdentity.objects.order_by("pk").values()), identities_before)

    def test_21_privacy_allowlist_zero_measurement_and_attributed_audit_are_exact(self):
        measurement = self.private_laboratory_data()
        event = SubcultureEvent.objects.create(
            parent_box=self.second_box, user=self.actor, reason="PRIVATE EVENT REASON", notes="PRIVATE EVENT NOTES",
        )
        row = self.lineage(subculture_event=event, notes="PRIVATE LINEAGE NOTES")
        destination_id = uuid4()
        envelope = self.lineage_package(
            destination_institution_id=destination_id, destination_institution_name="Destination laboratory",
        )
        envelope.refresh_from_db()
        item = envelope.items.get()
        data = serialize_transfer_envelope(envelope)
        self.assertEqual(set(data), TOP_FIELDS)
        self.assertEqual((data["protocol_major"], data["protocol_minor"]), (2, 1))
        self.assertEqual(data["destination_institution_id"], str(destination_id))
        self.assertEqual(data["destination_institution_name"], "Destination laboratory")
        self.assertEqual(set(data["items"][0]), ITEM_FIELDS | {"lineage"})
        self.assertEqual(data["items"][0]["declared_polyp_quantity"], 0)
        self.assertEqual(item.declared_polyp_quantity, 0)
        self.assertEqual(item.lineage_snapshot, data["items"][0]["lineage"])
        edge = row.portable_lineage_edge
        self.assert_graph(item.lineage_snapshot, [edge.source_node, edge.target_node], [edge], edge.target_node)
        self.assertEqual(set(item.lineage_snapshot), {"root_node_id", "nodes", "edges"})
        for node in item.lineage_snapshot["nodes"]:
            self.assertEqual(set(node), {"node_id"})
        for portable_edge in item.lineage_snapshot["edges"]:
            self.assertEqual(set(portable_edge), {"edge_id", "source_node_id", "target_node_id", "relationship_type"})
        self.assertNotIn("PRIVATE", json.dumps(data))
        self.assertNotIn(self.second_box.global_code, json.dumps(item.lineage_snapshot))
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 0)
        self.assertEqual(measurement.salinity_psu, 0)
        audit = AuditLog.objects.get()
        self.assertEqual(audit.organization_id, self.source.pk)
        self.assertEqual(audit.user_id, self.actor.pk)
        self.assertEqual(audit.action, AuditLog.Action.TRANSFER)
        self.assertEqual(audit.object_type, "transfer_envelope")
        self.assertEqual(audit.object_id, str(envelope.transfer_id))
        self.assertEqual(audit.metadata, {
            "protocol_major": 2, "protocol_minor": 1, "item_ids": [str(item.item_id)],
        })
        parsed = parse_transfer_envelope(json.loads(json.dumps(data)))
        self.assertEqual(parsed["transfer_id"], envelope.transfer_id)
        self.assertEqual(parsed["items"][0]["declared_polyp_quantity"], 0)

    def test_saved_package_is_frozen_after_lineage_labels_status_codes_and_identity_change(self):
        row = self.lineage()
        envelope = self.lineage_package()
        before = serialize_transfer_envelope(envelope)
        replacement = GlobalStrainIdentity.objects.create()
        new_parent = self.local_box("NEW-PARENT.003")
        BoxLineage.objects.filter(pk=row.pk).update(relationship_type="other", notes="Changed relation")
        self.lineage(new_parent, self.box, relationship_type="historical_import")
        Organization.objects.filter(pk=self.source.pk).update(name="Changed source", portable_id=uuid4())
        ThermalZone.objects.filter(pk=self.zone.pk).update(name="Changed zone")
        Box.objects.filter(pk=self.box.pk).update(global_code="CHANGED.001", local_code="Changed label", status="inactive")
        Box.objects.filter(pk=self.second_box.pk).update(global_code="CHANGED.002", status="active")
        Strain.objects.filter(pk=self.strain.pk).update(code="CHANGED", global_identity=replacement)
        Species.objects.filter(pk=self.species.pk).update(scientific_name="Changed species")
        envelope = TransferEnvelope.objects.get(pk=envelope.pk)
        with CaptureQueriesContext(connection) as queries:
            after = serialize_transfer_envelope(envelope)
        self.assertEqual(after, before)
        self.assertEqual(envelope.items.get().lineage_snapshot, before["items"][0]["lineage"])
        for query in queries:
            for table in ("cultures_box", "cultures_boxlineage", "cultures_portablelineagenode", "cultures_portablelineageedge", "taxonomy_strain", "taxonomy_species", "organizations_organization"):
                self.assertNotIn(f'"{table}"', query["sql"])

    def test_repeated_packages_keep_graph_ids_but_generate_new_transfer_and_item_ids(self):
        self.lineage()
        first = self.lineage_package()
        first_item = first.items.get()
        state = self.projection_state()
        second = self.lineage_package()
        second_item = second.items.get()
        self.assertNotEqual(first.transfer_id, second.transfer_id)
        self.assertNotEqual(first_item.item_id, second_item.item_id)
        self.assertEqual(first_item.lineage_snapshot, second_item.lineage_snapshot)
        self.assertEqual(self.projection_state(), state)
        self.assertEqual(TransferEnvelope.objects.count(), 2)
        self.assertEqual(TransferItem.objects.count(), 2)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_onward_package_exports_prior_transfer_but_no_current_outgoing_transfer_edge(self):
        foreign = assert_node(organization=self.foreign, node_id=uuid4(), local_box=self.foreign_box)
        known_foreign = assert_node(organization=self.source, node_id=foreign.node_id)
        parent = self.node(self.second_box)
        prior_transfer = self.edge(
            known_foreign, parent, relationship_type="transfer", transfer_id=uuid4(), item_id=uuid4(),
        )
        row = self.lineage()
        envelope = self.lineage_package(destination_institution_id=self.foreign.portable_id)
        item = envelope.items.get()
        local_edge = row.portable_lineage_edge
        self.assert_graph(
            item.lineage_snapshot, [known_foreign, parent, local_edge.target_node],
            [prior_transfer, local_edge], local_edge.target_node,
        )
        transfer_edges = [edge for edge in item.lineage_snapshot["edges"] if edge["relationship_type"] == "transfer"]
        self.assertEqual(len(transfer_edges), 1)
        self.assertEqual(set(transfer_edges[0]), {
            "edge_id", "source_node_id", "target_node_id", "relationship_type", "transfer_id", "item_id",
        })
        self.assertEqual(transfer_edges[0]["transfer_id"], str(prior_transfer.transfer_id))
        self.assertEqual(transfer_edges[0]["item_id"], str(prior_transfer.item_id))
        self.assertNotEqual(transfer_edges[0]["transfer_id"], str(envelope.transfer_id))
        self.assertNotEqual(transfer_edges[0]["item_id"], str(item.item_id))
        self.assertFalse(PortableLineageEdge.objects.filter(transfer_id=envelope.transfer_id).exists())
        self.assertFalse(PortableLineageEdge.objects.filter(source_node=local_edge.target_node).exists())
        self.assertEqual(Box.objects.count(), 3)
        self.assertIsNone(known_foreign.local_box_id)

    def test_late_lineage_failure_rolls_back_earlier_item_bridges(self):
        parent = self.local_box("VALID-PARENT.003")
        self.lineage(parent, self.box)
        self.lineage(self.foreign_box, self.second_box)
        before = self.projection_state()
        with self.assertRaises(ValidationError):
            self.lineage_package([
                {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 1},
            ])
        self.assert_no_new_package_or_projection(before)

    def test_cycle_failure_rolls_back_package_and_all_new_bridges(self):
        self.lineage()
        self.lineage(self.box, self.second_box)
        before = self.projection_state()
        with self.assertRaises(ValidationError) as caught:
            self.lineage_package()
        self.assertIn("Cycles", str(caught.exception.detail))
        self.assert_no_new_package_or_projection(before)

    def test_late_item_constraint_failure_rolls_back_new_bridges_envelope_items_and_audit(self):
        self.lineage()
        self.node(self.second_box)
        before = self.projection_state()
        original_save = TransferItem.save
        saved_ids = []

        def fail_second_save(item, *args, **kwargs):
            self.assertEqual(PortableLineageNode.objects.count(), 2)
            self.assertEqual(PortableLineageEdge.objects.filter(local_lineage__isnull=False).count(), 1)
            if saved_ids:
                self.assertTrue(TransferEnvelope.objects.filter(pk=item.envelope_id).exists())
                self.assertTrue(TransferItem.objects.filter(pk=saved_ids[0]).exists())
                item.global_strain_id = None
            result = original_save(item, *args, **kwargs)
            saved_ids.append(item.pk)
            return result

        with patch.object(TransferItem, "save", autospec=True, side_effect=fail_second_save):
            with self.assertRaises(IntegrityError):
                self.lineage_package([
                    {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                    {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 1},
                ])
        self.assertEqual(len(saved_ids), 1)
        self.assert_no_new_package_or_projection(before)

    def test_late_audit_failure_rolls_back_audit_as_well_as_bridges_envelope_and_items(self):
        self.lineage()
        self.node(self.second_box)
        before = self.projection_state()
        original_create = AuditLog.objects.create
        written_audits = []

        def fail_after_audit_insert(**kwargs):
            self.assertEqual(TransferEnvelope.objects.count(), 1)
            self.assertEqual(TransferItem.objects.count(), 2)
            self.assertEqual(PortableLineageNode.objects.count(), 2)
            self.assertEqual(PortableLineageEdge.objects.count(), 1)
            audit = original_create(**kwargs)
            written_audits.append(audit.pk)
            self.assertTrue(AuditLog.objects.filter(pk=audit.pk).exists())
            raise RuntimeError("late audit failure")

        with patch("apps.cultures.transfer_v2.AuditLog.objects.create", side_effect=fail_after_audit_insert):
            with self.assertRaisesMessage(RuntimeError, "late audit failure"):
                self.lineage_package([
                    {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                    {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 1},
                ])
        self.assertEqual(len(written_audits), 1)
        self.assert_no_new_package_or_projection(before)

    def test_21_package_has_no_culture_location_stock_or_measurement_lifecycle_effects(self):
        measurement = self.private_laboratory_data()
        event = SubcultureEvent.objects.create(parent_box=self.second_box, user=self.actor)
        self.lineage(subculture_event=event)
        models = (
            Organization, OrganizationMembership, Box, Strain, Species, GlobalStrainIdentity, LocalStrainIdentity,
            ThermalZone, BoxLocation, BoxMovement, BoxLineage, SubcultureEvent,
            BoxTransfer, BoxTransferImport, BiologicalMeasurement, Observation, Probe, TemperatureMeasurement,
        )
        before = {model: list(model.objects.order_by("pk").values()) for model in models}
        envelope = self.lineage_package([
            {"source_box_id": self.box.pk, "declared_polyp_quantity": 300},
            {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 0},
        ])
        for model in models:
            with self.subTest(model=model.__name__):
                self.assertEqual(list(model.objects.order_by("pk").values()), before[model])
        measurement.refresh_from_db()
        self.assertEqual(measurement.polyp_count, 0)
        self.assertEqual(envelope.items.get(source_box=self.box).declared_polyp_quantity, 300)
        self.assertEqual(AuditLog.objects.count(), 1)
        self.assertEqual(PortableLineageNode.objects.count(), 2)
        self.assertEqual(PortableLineageEdge.objects.count(), 1)
