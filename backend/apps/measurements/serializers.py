from rest_framework import serializers

from .models import BiologicalMeasurement


class BiologicalMeasurementCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = BiologicalMeasurement
        fields = [
            "measured_on",
            "polyp_count",
            "ephyrae_count",
            "strobila_count",
            "salinity_psu",
            "culture_status",
            "needs_attention",
            "notes",
        ]
        # Live entry never writes "not measured": NULL is reserved for
        # historical rows. An omitted value still defaults to a real 0.
        extra_kwargs = {"strobila_count": {"allow_null": False}}
