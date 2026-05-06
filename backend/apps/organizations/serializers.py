from rest_framework import serializers

from .models import Organization


class OrganizationSummarySerializer(serializers.ModelSerializer):
    """Small organization payload reused by API endpoints."""

    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "city", "country"]
