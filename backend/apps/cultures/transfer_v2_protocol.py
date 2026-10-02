"""Transport-neutral v2 contract. Parsing establishes structure, not trust or access."""

from collections.abc import Mapping
from datetime import timezone
import uuid

from rest_framework import serializers


PROTOCOL = "polypbase.transfer"
PROTOCOL_MAJOR = 2
PROTOCOL_MINOR = 0
MAX_QUANTITY = 2147483647


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


class TransferItemSerializer(StrictSerializer):
    item_id = StrictUUIDField()
    source_box_code = StrictStringField(max_length=100, trim_whitespace=False)
    source_strain_code = StrictStringField(max_length=80, trim_whitespace=False)
    species_scientific_name = StrictStringField(max_length=150, trim_whitespace=False)
    global_strain_id = StrictUUIDField()
    declared_polyp_quantity = StrictIntegerField(min_value=0, max_value=MAX_QUANTITY)


class TransferEnvelopeSerializer(StrictSerializer):
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
        if attrs["protocol_minor"] != PROTOCOL_MINOR:
            raise serializers.ValidationError({"protocol_minor": "Unsupported minor version."})
        item_ids = [item["item_id"] for item in attrs["items"]]
        if len(set(item_ids)) != len(item_ids):
            raise serializers.ValidationError({"items": "Duplicate item identity."})
        return attrs


def parse_transfer_envelope(data):
    """Validate a plain mapping without database lookup, mutation or authorization."""
    serializer = TransferEnvelopeSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def serialize_transfer_envelope(envelope):
    """Reconstruct the portable allowlist using persisted snapshots only.

    This internal helper does not authorize reads. Callers must already hold an
    authorized source package; portable UUIDs never grant access to local records.
    """
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
            }
            for item in envelope.items.order_by("pk")
        ],
    }
    validated = parse_transfer_envelope(data)
    return dict(TransferEnvelopeSerializer(validated).data)
