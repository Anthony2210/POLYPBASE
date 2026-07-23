from rest_framework import serializers

from apps.organizations.serializers import OrganizationSummarySerializer

from .models import UserPreference


def available_interface_languages():
    """Return the languages exposed in account settings."""
    return [
        {"code": code, "label": str(label)}
        for code, label in UserPreference.InterfaceLanguage.choices
    ]


class UserPreferenceSerializer(serializers.ModelSerializer):
    available_languages = serializers.SerializerMethodField()

    class Meta:
        model = UserPreference
        fields = ["interface_language", "available_languages"]

    def get_available_languages(self, obj):
        return available_interface_languages()


class UserProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    username = serializers.CharField()
    email = serializers.EmailField(allow_blank=True)
    first_name = serializers.CharField(allow_blank=True)
    last_name = serializers.CharField(allow_blank=True)
    is_superuser = serializers.BooleanField()
    interface_language = serializers.CharField()
    organizations = OrganizationSummarySerializer(many=True)
    active_organization = OrganizationSummarySerializer(allow_null=True)
    memberships = serializers.ListField()
    available_languages = serializers.ListField()
