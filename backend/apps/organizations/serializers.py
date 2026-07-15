from rest_framework import serializers

from .models import Organization


class OrganizationSummarySerializer(serializers.ModelSerializer):
    """Small organization payload reused by API endpoints."""

    class Meta:
        model = Organization
        fields = ["id", "name", "slug", "city", "country", "contact_email", "notes"]


class OrganizationCreateSerializer(serializers.ModelSerializer):
    """Write serializer used by superusers to create a new organization (tenant)."""

    class Meta:
        model = Organization
        fields = ["id", "name", "city", "country", "contact_email", "notes"]

    def validate_name(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Le nom de la structure est requis.")
        queryset = Organization.objects.filter(name__iexact=value)
        if self.instance is not None:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("Une structure porte déjà ce nom.")
        return value
