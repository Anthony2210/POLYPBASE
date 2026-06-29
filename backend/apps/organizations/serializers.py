from rest_framework import serializers

from .models import Organization


class OrganizationSummarySerializer(serializers.ModelSerializer):
    """Small organization payload reused by API endpoints."""

    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "city", "country"]


class OrganizationCreateSerializer(serializers.ModelSerializer):
    """Write serializer used by superusers to create a new organization (tenant)."""

    class Meta:
        model = Organization
        fields = ["id", "name", "city", "country", "contact_email", "notes"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Le nom de la structure est requis.")
        if Organization.objects.filter(name__iexact=value).exists():
            raise serializers.ValidationError("Une structure porte déjà ce nom.")
        return value
