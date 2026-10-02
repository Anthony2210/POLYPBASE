import json
from copy import deepcopy
from datetime import datetime
from unittest.mock import patch
from uuid import UUID, uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement, Observation, TemperatureMeasurement
from apps.organizations.models import Organization
from apps.taxonomy.models import GlobalStrainIdentity, LocalStrainIdentity, Species, Strain

from .models import (
    Box, BoxLineage, BoxLocation, BoxMovement, BoxTransfer, BoxTransferImport,
    SubcultureEvent, ThermalZone, TransferEnvelope, TransferItem,
)
from .transfer_v2 import create_source_package
from .transfer_v2_protocol import parse_transfer_envelope, serialize_transfer_envelope


TOP_FIELDS = {
    "protocol", "protocol_major", "protocol_minor", "transfer_id", "created_at",
    "source_institution_id", "source_institution_name", "destination_institution_id",
    "destination_institution_name", "items",
}
ITEM_FIELDS = {
    "item_id", "source_box_code", "source_strain_code", "species_scientific_name",
    "global_strain_id", "declared_polyp_quantity",
}


def remote_payload(item_count=1):
    return {
        "protocol": "polypbase.transfer",
        "protocol_major": 2,
        "protocol_minor": 0,
        "transfer_id": str(uuid4()),
        "created_at": "2026-09-01T12:34:56+00:00",
        "source_institution_id": str(uuid4()),
        "source_institution_name": "Remote laboratory",
        "destination_institution_id": None,
        "destination_institution_name": "",
        "items": [
            {
                "item_id": str(uuid4()),
                "source_box_code": f"REMOTE.{index + 1:03}",
                "source_strain_code": "AAU-REMOTE",
                "species_scientific_name": "Aurelia aurita",
                "global_strain_id": str(uuid4()),
                "declared_polyp_quantity": index,
            }
            for index in range(item_count)
        ],
    }


class TransferV2Fixtures(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.source = Organization.objects.create(name="Source laboratory", slug="v2-source")
        cls.foreign = Organization.objects.create(name="Foreign laboratory", slug="v2-foreign")
        cls.actor = get_user_model().objects.create_user(
            username="v2-admin", email="v2-admin@example.test",
        )
        cls.membership = OrganizationMembership.objects.create(
            user=cls.actor, organization=cls.source, role=OrganizationMembership.Role.ADMIN,
        )
        cls.species = Species.objects.create(scientific_name="Aurelia aurita")
        cls.identity = GlobalStrainIdentity.objects.create()
        cls.strain = Strain.objects.create(
            species=cls.species, organization=cls.source, code="AAU-SRC",
            global_identity=cls.identity,
        )
        cls.zone = ThermalZone.objects.create(organization=cls.source, name="Source tank")
        cls.box = Box.objects.create(
            organization=cls.source, strain=cls.strain, global_code="SRC.001",
            box_number="1", thermal_zone=cls.zone, status=Box.Status.ACTIVE,
        )
        cls.second_box = Box.objects.create(
            organization=cls.source, strain=cls.strain, global_code="SRC.002",
            box_number="2", status=Box.Status.PENDING_REVIEW,
        )
        cls.foreign_box = Box.objects.create(
            organization=cls.foreign, strain=cls.strain, global_code="FOREIGN.001",
            box_number="1",
        )

    def package(self, selections=None, **kwargs):
        return create_source_package(
            actor=kwargs.pop("actor", self.actor),
            source_organization=kwargs.pop("source_organization", self.source),
            selections=selections if selections is not None else [
                {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
            ],
            **kwargs,
        )

    def envelope(self, **kwargs):
        return TransferEnvelope.objects.create(
            **{
                "source_organization": self.source,
                "source_institution_id": self.source.portable_id,
                "source_institution_name": self.source.name,
                "created_by": self.actor,
                **kwargs,
            }
        )

    def item(self, envelope, **kwargs):
        return TransferItem.objects.create(
            **{
                "envelope": envelope,
                "source_box": self.box,
                "source_box_code": self.box.global_code,
                "source_strain_code": self.strain.code,
                "species_scientific_name": self.species.scientific_name,
                "global_strain_id": self.identity.global_id,
                "declared_polyp_quantity": 0,
                **kwargs,
            }
        )

    def assert_no_package(self):
        self.assertFalse(TransferEnvelope.objects.exists())
        self.assertFalse(TransferItem.objects.exists())
        self.assertFalse(AuditLog.objects.exists())


class TransferV2ModelTests(TransferV2Fixtures):
    def test_defaults_and_unique_transfer_id(self):
        before = timezone.now()
        first = self.envelope()
        second = self.envelope()
        self.assertIsInstance(first.transfer_id, UUID)
        self.assertEqual(first.transfer_id.version, 4)
        self.assertNotEqual(first.transfer_id, second.transfer_id)
        self.assertEqual((first.protocol_major, first.protocol_minor), (2, 0))
        self.assertIsNone(first.destination_institution_id)
        self.assertEqual(first.destination_institution_name, "")
        self.assertLessEqual(before, first.created_at)
        self.assertLessEqual(first.created_at, timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.envelope(transfer_id=first.transfer_id)

    def test_item_ids_are_unique_within_envelope_not_globally(self):
        first = self.envelope()
        item = self.item(first)
        self.assertIsInstance(item.item_id, UUID)
        self.assertEqual(item.item_id.version, 4)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.item(first, item_id=item.item_id)
        second = self.envelope()
        self.item(second, item_id=item.item_id)
        self.assertEqual(first.items.count(), 1)
        self.assertEqual(second.items.count(), 1)

    def test_quantity_zero_is_real_and_negative_or_null_is_rejected(self):
        envelope = self.envelope()
        item = self.item(envelope, declared_polyp_quantity=0)
        item.refresh_from_db()
        self.assertEqual(item.declared_polyp_quantity, 0)
        for value in (-1, None):
            with self.subTest(value=value):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.item(envelope, declared_polyp_quantity=value)

    def test_required_columns_reject_null(self):
        for field in (
            "transfer_id", "source_organization", "source_institution_id",
            "source_institution_name", "protocol_major", "protocol_minor", "created_at",
        ):
            with self.subTest(envelope_field=field):
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.envelope(**{field: None})
        envelope = self.envelope()
        for field in (
            "envelope", "item_id", "source_box", "source_box_code", "source_strain_code",
            "species_scientific_name", "global_strain_id", "declared_polyp_quantity",
        ):
            with self.subTest(item_field=field):
                values = {field: None}
                # Pass the relation positionally to keep the helper's call unambiguous.
                target = values.pop("envelope", envelope)
                with self.assertRaises(IntegrityError), transaction.atomic():
                    self.item(target, **values)

    def test_required_identity_and_quantity_have_no_implicit_defaults(self):
        envelope = self.envelope()
        values = {
            "envelope": envelope,
            "source_box": self.box,
            "source_box_code": self.box.global_code,
            "source_strain_code": self.strain.code,
            "species_scientific_name": self.species.scientific_name,
            "global_strain_id": self.identity.global_id,
            "declared_polyp_quantity": 0,
        }
        for field in ("global_strain_id", "declared_polyp_quantity"):
            with self.subTest(field=field):
                missing = {key: value for key, value in values.items() if key != field}
                with self.assertRaises(IntegrityError), transaction.atomic():
                    TransferItem.objects.create(**missing)

    def test_source_organization_is_protected_without_source_boxes(self):
        organization = Organization.objects.create(name="Envelope-only source")
        envelope = self.envelope(
            source_organization=organization,
            source_institution_id=organization.portable_id,
            source_institution_name=organization.name,
            created_by=None,
        )
        with self.assertRaises(ProtectedError):
            organization.delete()
        envelope.refresh_from_db()
        self.assertIsNone(envelope.created_by_id)
        self.assertEqual(envelope.source_organization_id, organization.pk)

    def test_protected_relations_and_nullable_author(self):
        envelope = self.envelope()
        self.item(envelope)
        for obj in (envelope, self.box, self.source):
            with self.subTest(model=type(obj).__name__):
                with self.assertRaises(ProtectedError):
                    obj.delete()
        self.actor.delete()
        envelope.refresh_from_db()
        self.assertIsNone(envelope.created_by_id)
        self.assertEqual(envelope.items.count(), 1)

    def test_snapshot_fields_are_not_editable_and_lengths_match_contract(self):
        for model, lengths in (
            (TransferEnvelope, {"source_institution_name": 150, "destination_institution_name": 150}),
            (TransferItem, {"source_box_code": 100, "source_strain_code": 80, "species_scientific_name": 150}),
        ):
            for name, length in lengths.items():
                with self.subTest(model=model.__name__, field=name):
                    field = model._meta.get_field(name)
                    self.assertEqual(field.max_length, length)
                    self.assertFalse(field.editable)
        for model, names in (
            (TransferEnvelope, ("source_institution_id", "destination_institution_id")),
            (TransferItem, ("global_strain_id", "declared_polyp_quantity")),
        ):
            for name in names:
                self.assertFalse(model._meta.get_field(name).editable, name)


class TransferV2ProtocolTests(TestCase):
    def test_valid_one_and_multiple_items_use_typed_values_without_queries(self):
        for count in (1, 3):
            with self.subTest(count=count):
                data = remote_payload(count)
                original = deepcopy(data)
                with self.assertNumQueries(0):
                    parsed = parse_transfer_envelope(data)
                self.assertEqual(data, original)
                self.assertEqual(set(parsed), TOP_FIELDS)
                self.assertIsInstance(parsed["transfer_id"], UUID)
                self.assertIsInstance(parsed["source_institution_id"], UUID)
                self.assertIsInstance(parsed["created_at"], datetime)
                self.assertIsNone(parsed["destination_institution_id"])
                self.assertEqual(len(parsed["items"]), count)
                for index, item in enumerate(parsed["items"]):
                    self.assertEqual(set(item), ITEM_FIELDS)
                    self.assertIsInstance(item["item_id"], UUID)
                    self.assertIsInstance(item["global_strain_id"], UUID)
                    self.assertEqual(item["declared_polyp_quantity"], index)

    def test_uuid_strings_are_normalized_independently_of_remote_database_objects(self):
        data = remote_payload(2)
        data["destination_institution_id"] = str(uuid4()).upper()
        for field in ("transfer_id", "source_institution_id"):
            data[field] = data[field].replace("-", "").upper()
        for item in data["items"]:
            for field in ("item_id", "global_strain_id"):
                item[field] = item[field].replace("-", "").upper()
        with self.assertNumQueries(0):
            parsed = parse_transfer_envelope(data)
        for field in ("transfer_id", "source_institution_id", "destination_institution_id"):
            self.assertEqual(parsed[field], UUID(data[field]))
        for original, item in zip(data["items"], parsed["items"]):
            for field in ("item_id", "global_strain_id"):
                self.assertEqual(item[field], UUID(original[field]))

    def test_optional_destination_defaults_and_uuid_objects(self):
        data = remote_payload()
        del data["destination_institution_id"]
        del data["destination_institution_name"]
        parsed = parse_transfer_envelope(data)
        self.assertIsNone(parsed["destination_institution_id"])
        self.assertEqual(parsed["destination_institution_name"], "")
        data["destination_institution_id"] = uuid4()
        for field in ("transfer_id", "source_institution_id"):
            data[field] = UUID(data[field])
        for field in ("item_id", "global_strain_id"):
            data["items"][0][field] = UUID(data["items"][0][field])
        parsed = parse_transfer_envelope(data)
        self.assertEqual(parsed["destination_institution_id"], data["destination_institution_id"])

    def test_rejects_invalid_protocol_and_strict_versions(self):
        cases = [("protocol", value) for value in ("polypbase.box_transfer.v1", "", None, 2)]
        cases += [("protocol_major", value) for value in (1, 3, -1, None, True, "2", 2.0)]
        cases += [("protocol_minor", value) for value in (1, -1, None, False, "0", 0.0)]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                data = remote_payload()
                data[field] = value
                with self.assertRaises(ValidationError), self.assertNumQueries(0):
                    parse_transfer_envelope(data)

    def test_rejects_missing_required_fields(self):
        for field in TOP_FIELDS - {"destination_institution_id", "destination_institution_name"}:
            with self.subTest(top=field):
                data = remote_payload()
                del data[field]
                with self.assertRaises(ValidationError):
                    parse_transfer_envelope(data)
        for field in ITEM_FIELDS:
            with self.subTest(item=field):
                data = remote_payload()
                del data["items"][0][field]
                with self.assertRaises(ValidationError):
                    parse_transfer_envelope(data)

    def test_rejects_invalid_uuid_types_and_null_global_identity(self):
        for scope, fields in (
            ("top", ("transfer_id", "source_institution_id", "destination_institution_id")),
            ("item", ("item_id", "global_strain_id")),
        ):
            for field in fields:
                invalid = ("not-a-uuid", "", 123, True, 1.5, {}, [])
                if field != "destination_institution_id":
                    invalid += (None,)
                for value in invalid:
                    with self.subTest(scope=scope, field=field, value=value):
                        data = remote_payload()
                        target = data if scope == "top" else data["items"][0]
                        target[field] = value
                        with self.assertRaises(ValidationError), self.assertNumQueries(0):
                            parse_transfer_envelope(data)

    def test_rejects_unknown_keys_at_both_levels(self):
        for scope, keys in (
            ("top", ("source_organization", "created_by", "metadata", "box_id")),
            ("item", ("source_box_id", "strain_id", "notes", "global_identity")),
        ):
            for key in keys:
                with self.subTest(scope=scope, key=key):
                    data = remote_payload()
                    target = data if scope == "top" else data["items"][0]
                    target[key] = {"nested": "value"}
                    with self.assertRaises(ValidationError):
                        parse_transfer_envelope(data)

    def test_rejects_invalid_containers_empty_items_and_duplicate_ids(self):
        for value in (None, [], "payload", 1):
            with self.subTest(payload=value):
                with self.assertRaises(ValidationError):
                    parse_transfer_envelope(value)
        for value in ([], None, {}, "items", [None], [1], [[]]):
            with self.subTest(items=value):
                data = remote_payload()
                data["items"] = value
                with self.assertRaises(ValidationError):
                    parse_transfer_envelope(data)
        data = remote_payload(2)
        data["items"][1]["item_id"] = UUID(data["items"][0]["item_id"])
        with self.assertRaises(ValidationError):
            parse_transfer_envelope(data)

    def test_rejects_noninteger_quantities_and_nonstring_snapshot_values(self):
        for value in (-1, None, True, False, "0", 0.0, [], {}):
            with self.subTest(quantity=value):
                data = remote_payload()
                data["items"][0]["declared_polyp_quantity"] = value
                with self.assertRaises(ValidationError):
                    parse_transfer_envelope(data)
        for scope, fields in (
            ("top", ("source_institution_name", "destination_institution_name")),
            ("item", ("source_box_code", "source_strain_code", "species_scientific_name")),
        ):
            for field in fields:
                for value in (None, 123, False, [], {}):
                    with self.subTest(scope=scope, field=field, value=value):
                        data = remote_payload()
                        target = data if scope == "top" else data["items"][0]
                        target[field] = value
                        with self.assertRaises(ValidationError):
                            parse_transfer_envelope(data)

    def test_quantity_integer_storage_boundary(self):
        data = remote_payload()
        data["items"][0]["declared_polyp_quantity"] = 2147483647
        with self.assertNumQueries(0):
            parsed = parse_transfer_envelope(data)
        self.assertEqual(parsed["items"][0]["declared_polyp_quantity"], 2147483647)
        for quantity in (2147483648, 2**63):
            with self.subTest(quantity=quantity):
                data["items"][0]["declared_polyp_quantity"] = quantity
                with self.assertRaises(ValidationError), self.assertNumQueries(0):
                    parse_transfer_envelope(data)

    def test_rejects_invalid_creation_time(self):
        for value in (None, "", "not-a-date", 123, True, {}, []):
            with self.subTest(value=value):
                data = remote_payload()
                data["created_at"] = value
                with self.assertRaises(ValidationError):
                    parse_transfer_envelope(data)


class TransferV2SourcePackageTests(TransferV2Fixtures):
    def test_persists_server_ids_snapshots_zero_and_attributed_allowlisted_audit(self):
        destination_id = uuid4()
        envelope = self.package(
            [
                {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 12},
            ],
            destination_institution_id=destination_id,
            destination_institution_name="Remote destination",
        )
        envelope.refresh_from_db()
        self.assertEqual(envelope.source_organization_id, self.source.pk)
        self.assertEqual(envelope.created_by_id, self.actor.pk)
        self.assertEqual(envelope.source_institution_id, self.source.portable_id)
        self.assertEqual(envelope.source_institution_name, self.source.name)
        self.assertEqual(envelope.destination_institution_id, destination_id)
        self.assertEqual(envelope.destination_institution_name, "Remote destination")
        self.assertEqual(envelope.transfer_id.version, 4)
        items = list(envelope.items.all())
        self.assertEqual(len(items), 2)
        self.assertEqual(len({item.item_id for item in items}), 2)
        by_box = {item.source_box_id: item for item in items}
        for box, quantity in ((self.box, 0), (self.second_box, 12)):
            item = by_box[box.pk]
            self.assertEqual(item.item_id.version, 4)
            self.assertEqual(item.source_box_code, box.global_code)
            self.assertEqual(item.source_strain_code, self.strain.code)
            self.assertEqual(item.species_scientific_name, self.species.scientific_name)
            self.assertEqual(item.global_strain_id, self.identity.global_id)
            self.assertEqual(item.declared_polyp_quantity, quantity)
        audit = AuditLog.objects.get()
        self.assertEqual(audit.organization_id, self.source.pk)
        self.assertEqual(audit.user_id, self.actor.pk)
        self.assertEqual(audit.action, AuditLog.Action.TRANSFER)
        self.assertEqual(audit.object_type, "transfer_envelope")
        self.assertEqual(audit.object_id, str(envelope.transfer_id))
        self.assertEqual(audit.metadata, {
            "protocol_major": 2, "protocol_minor": 0,
            "item_ids": [str(item.item_id) for item in items],
        })
        data = serialize_transfer_envelope(envelope)
        self.assertEqual(set(data), TOP_FIELDS)
        self.assertEqual(data["protocol"], "polypbase.transfer")
        self.assertEqual((data["protocol_major"], data["protocol_minor"]), (2, 0))
        self.assertEqual(data["transfer_id"], str(envelope.transfer_id))
        self.assertEqual(data["source_institution_id"], str(self.source.portable_id))
        self.assertEqual(data["destination_institution_id"], str(destination_id))
        for item in data["items"]:
            self.assertEqual(set(item), ITEM_FIELDS)
            for field in ("item_id", "global_strain_id"):
                self.assertEqual(item[field], str(UUID(item[field])))
        parsed = parse_transfer_envelope(json.loads(json.dumps(data)))
        self.assertEqual(parsed["created_at"], envelope.created_at)
        self.assertEqual(parsed["transfer_id"], envelope.transfer_id)

    def test_default_destination_serializes_as_null_and_empty_name(self):
        envelope = self.package()
        data = serialize_transfer_envelope(envelope)
        self.assertEqual(set(data), TOP_FIELDS)
        self.assertIsNone(data["destination_institution_id"])
        self.assertEqual(data["destination_institution_name"], "")
        self.assertEqual(data["items"][0]["declared_polyp_quantity"], 0)
        self.assertEqual(parse_transfer_envelope(data)["transfer_id"], envelope.transfer_id)

    def test_source_snapshots_are_refetched_before_creation(self):
        new_id = uuid4()
        Organization.objects.filter(pk=self.source.pk).update(name="Updated source", portable_id=new_id)
        Box.objects.filter(pk=self.box.pk).update(global_code="UPDATED.001")
        replacement = GlobalStrainIdentity.objects.create()
        Strain.objects.filter(pk=self.strain.pk).update(code="UPDATED", global_identity=replacement)
        Species.objects.filter(pk=self.species.pk).update(scientific_name="Updated species")
        envelope = self.package()
        item = envelope.items.get()
        self.assertEqual(envelope.source_institution_name, "Updated source")
        self.assertEqual(envelope.source_institution_id, new_id)
        self.assertEqual(item.source_box_code, "UPDATED.001")
        self.assertEqual(item.source_strain_code, "UPDATED")
        self.assertEqual(item.species_scientific_name, "Updated species")
        self.assertEqual(item.global_strain_id, replacement.global_id)

    def test_same_source_box_can_be_selected_in_separate_packages(self):
        first = self.package()
        second = self.package()
        self.assertNotEqual(first.transfer_id, second.transfer_id)
        self.assertNotEqual(first.items.get().item_id, second.items.get().item_id)
        self.assertEqual(first.items.get().source_box_id, second.items.get().source_box_id)
        self.assertEqual(AuditLog.objects.count(), 2)

    def test_only_active_source_admin_or_active_superuser_is_authorized(self):
        for role in (OrganizationMembership.Role.VIEWER, OrganizationMembership.Role.LAB_TECHNICIAN):
            with self.subTest(role=role):
                OrganizationMembership.objects.filter(pk=self.membership.pk).update(role=role)
                with self.assertRaises(PermissionDenied):
                    self.package()
                self.assert_no_package()
        OrganizationMembership.objects.filter(pk=self.membership.pk).update(role="admin", is_active=False)
        with self.assertRaises(PermissionDenied):
            self.package()
        self.assert_no_package()
        OrganizationMembership.objects.filter(pk=self.membership.pk).delete()
        OrganizationMembership.objects.create(user=self.actor, organization=self.foreign, role="admin")
        with self.assertRaises(PermissionDenied):
            self.package()
        self.assert_no_package()
        self.actor.is_superuser = True
        self.actor.save(update_fields=["is_superuser"])
        self.assertEqual(self.package().created_by_id, self.actor.pk)

    def test_anonymous_and_inactive_actors_are_denied_even_if_superuser(self):
        with self.assertRaises(PermissionDenied):
            self.package(actor=AnonymousUser())
        for superuser in (False, True):
            with self.subTest(superuser=superuser):
                self.actor.is_active = False
                self.actor.is_superuser = superuser
                self.actor.save(update_fields=["is_active", "is_superuser"])
                with self.assertRaises(PermissionDenied):
                    self.package()
                self.assert_no_package()

    def test_source_organization_is_refetched_before_permission_check(self):
        Organization.objects.filter(pk=self.source.pk).update(is_active=False)
        self.assertTrue(self.source.is_active)
        with self.assertRaises(PermissionDenied):
            self.package()
        self.assert_no_package()

    def test_foreign_context_is_not_authorized_by_source_membership(self):
        with self.assertRaises(PermissionDenied):
            self.package(source_organization=self.foreign)
        self.assert_no_package()

    def assert_invalid_selection(self, selections, index=None):
        with CaptureQueriesContext(connection) as queries:
            with self.assertRaises(ValidationError) as caught:
                self.package(selections)
        if index is not None:
            detail = caught.exception.detail
            if isinstance(detail, dict):
                detail = detail.get("items", detail)
                self.assertTrue(index in detail or str(index) in detail, detail)
            else:
                self.assertIsInstance(detail, list)
                self.assertLess(index, len(detail))
                self.assertTrue(detail[index], detail)
        writes = [query["sql"] for query in queries if query["sql"].lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))]
        self.assertEqual(writes, [], "All selections must be validated before any writes")
        self.assert_no_package()

    def test_cross_organization_missing_and_mixed_boxes_are_rejected_before_writes(self):
        valid = {"source_box_id": self.box.pk, "declared_polyp_quantity": 0}
        for box_id in (self.foreign_box.pk, 999999):
            invalid = {"source_box_id": box_id, "declared_polyp_quantity": 1}
            with self.subTest(box_id=box_id):
                self.assert_invalid_selection([invalid], index=0)
                self.assert_invalid_selection([valid, invalid], index=1)

    def test_boxes_are_refetched_not_trusted_from_cached_source_data(self):
        Box.objects.filter(pk=self.box.pk).update(organization=self.foreign)
        self.assertEqual(self.box.organization_id, self.source.pk)
        self.assert_invalid_selection([
            {"source_box_id": self.box.pk, "declared_polyp_quantity": 1},
        ], index=0)

    def test_identityless_strain_is_rejected_without_creating_or_attaching_identity(self):
        Strain.objects.filter(pk=self.strain.pk).update(global_identity=None)
        identities_before = list(GlobalStrainIdentity.objects.values())
        self.assert_invalid_selection([
            {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
        ], index=0)
        self.strain.refresh_from_db()
        self.assertIsNone(self.strain.global_identity_id)
        self.assertEqual(list(GlobalStrainIdentity.objects.values()), identities_before)

    def test_late_identityless_item_is_all_or_nothing(self):
        legacy = Strain.objects.create(species=self.species, organization=self.source, code="LEGACY")
        Box.objects.filter(pk=self.second_box.pk).update(strain=legacy)
        self.assert_invalid_selection([
            {"source_box_id": self.box.pk, "declared_polyp_quantity": 10},
            {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 0},
        ], index=1)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.global_identity_id)

    def test_strict_selection_allowlist_types_and_required_quantity(self):
        for selections in ([], {}, "boxes", [None], [1]):
            with self.subTest(selections=selections):
                self.assert_invalid_selection(selections)
        with self.assertRaises(ValidationError):
            create_source_package(actor=self.actor, source_organization=self.source, selections=None)
        self.assert_no_package()
        for field in ("source_box_id", "declared_polyp_quantity"):
            for value in (None, True, False, "1", 1.0, -1, {}, []):
                with self.subTest(field=field, value=value):
                    selection = {"source_box_id": self.box.pk, "declared_polyp_quantity": 0}
                    selection[field] = value
                    self.assert_invalid_selection([selection], index=0)
            selection = {"source_box_id": self.box.pk, "declared_polyp_quantity": 0}
            del selection[field]
            self.assert_invalid_selection([selection], index=0)
        for field in ("item_id", "transfer_id", "source_box_code", "global_strain_id", "created_by", "notes"):
            with self.subTest(injected=field):
                self.assert_invalid_selection([
                    {"source_box_id": self.box.pk, "declared_polyp_quantity": 0, field: str(uuid4())},
                ], index=0)

    def test_quantity_overflow_is_rejected_before_writes(self):
        for quantity in (2147483648, 2**63):
            with self.subTest(quantity=quantity):
                self.assert_invalid_selection([
                    {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                    {"source_box_id": self.second_box.pk, "declared_polyp_quantity": quantity},
                ], index=1)
        envelope = self.package([
            {"source_box_id": self.box.pk, "declared_polyp_quantity": 2147483647},
        ])
        self.assertEqual(envelope.items.get().declared_polyp_quantity, 2147483647)

    def test_second_item_persistence_failure_rolls_back_parent_and_first_item(self):
        original_save = TransferItem.save
        saved_ids = []

        def save_with_late_constraint_failure(item, *args, **kwargs):
            if saved_ids:
                self.assertTrue(TransferEnvelope.objects.filter(pk=item.envelope_id).exists())
                self.assertTrue(TransferItem.objects.filter(pk=saved_ids[0]).exists())
                # Force a real NOT NULL failure after the first item was inserted.
                item.global_strain_id = None
            result = original_save(item, *args, **kwargs)
            saved_ids.append(item.pk)
            return result

        with patch.object(TransferItem, "save", autospec=True, side_effect=save_with_late_constraint_failure):
            with self.assertRaises(IntegrityError):
                self.package([
                    {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                    {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 4},
                ])
        self.assertEqual(len(saved_ids), 1)
        self.assert_no_package()
        self.assertFalse(connection.needs_rollback)

    def test_audit_failure_rolls_back_parent_and_all_items(self):
        with patch("apps.cultures.transfer_v2.AuditLog.objects.create", side_effect=RuntimeError("audit failed")):
            with self.assertRaisesMessage(RuntimeError, "audit failed"):
                self.package([
                    {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
                    {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 4},
                ])
        self.assert_no_package()
        self.assertFalse(connection.needs_rollback)

    def test_serialization_uses_persisted_snapshots_after_source_changes(self):
        envelope = self.package()
        before = serialize_transfer_envelope(envelope)
        replacement = GlobalStrainIdentity.objects.create()
        Organization.objects.filter(pk=self.source.pk).update(name="Renamed source", portable_id=uuid4())
        Box.objects.filter(pk=self.box.pk).update(global_code="RENAMED.001")
        Strain.objects.filter(pk=self.strain.pk).update(code="RENAMED", global_identity=replacement)
        Species.objects.filter(pk=self.species.pk).update(scientific_name="Renamed species")
        envelope = TransferEnvelope.objects.get(pk=envelope.pk)
        self.assertEqual(serialize_transfer_envelope(envelope), before)
        item = envelope.items.get()
        self.assertEqual(item.global_strain_id, self.identity.global_id)
        self.assertEqual(item.source_box_id, self.box.pk)

    def test_package_does_not_mutate_cultures_locations_lifecycle_or_measurements(self):
        BoxLocation.objects.create(box=self.box, thermal_zone=self.zone)
        BiologicalMeasurement.objects.create(
            box=self.box, measured_on=timezone.localdate(), polyp_count=17, user=self.actor,
        )
        Observation.objects.create(box=self.box, notes="Keep this observation", user=self.actor)
        models = (
            Organization, Box, Strain, Species, GlobalStrainIdentity, LocalStrainIdentity,
            ThermalZone, BoxLocation, BoxMovement, BoxLineage, SubcultureEvent,
            BoxTransfer, BoxTransferImport, BiologicalMeasurement, Observation, TemperatureMeasurement,
        )
        before = {model: list(model.objects.order_by("pk").values()) for model in models}
        self.package([
            {"source_box_id": self.box.pk, "declared_polyp_quantity": 0},
            {"source_box_id": self.second_box.pk, "declared_polyp_quantity": 300},
        ])
        for model in models:
            with self.subTest(model=model.__name__):
                self.assertEqual(list(model.objects.order_by("pk").values()), before[model])
