from rest_framework import serializers


class CommaSeparatedIntegerListField(serializers.Field):
    """Parse a comma-separated list of positive integer identifiers."""

    def to_internal_value(self, data):
        if data in (None, ""):
            return []

        values = data if isinstance(data, (list, tuple)) else str(data).split(",")
        parsed_values = []

        for value in values:
            try:
                parsed_value = int(str(value).strip())
            except (TypeError, ValueError):
                self.fail("invalid")

            if parsed_value <= 0:
                self.fail("invalid")
            if parsed_value not in parsed_values:
                parsed_values.append(parsed_value)

        return parsed_values

    def to_representation(self, value):
        return value

    default_error_messages = {
        "invalid": "Expected a comma-separated list of positive integer identifiers.",
    }


class MeasurementExportFilterSerializer(serializers.Serializer):
    organizations = CommaSeparatedIntegerListField(required=False, default=list)
    species = CommaSeparatedIntegerListField(required=False, default=list)
    strains = CommaSeparatedIntegerListField(required=False, default=list)
    boxes = CommaSeparatedIntegerListField(required=False, default=list)
    zones = CommaSeparatedIntegerListField(required=False, default=list)
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        date_from = attrs.get("date_from")
        date_to = attrs.get("date_to")
        if date_from and date_to and date_from > date_to:
            raise serializers.ValidationError(
                {"date_to": "The end date must be after or equal to the start date."}
            )
        return attrs
