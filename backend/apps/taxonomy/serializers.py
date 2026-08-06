from django.conf import settings
from django.db import transaction
from rest_framework import serializers

from .models import Species, SpeciesTranslation, Strain, StrainTranslation


def available_content_languages():
    """Return the languages configured for localized application content."""
    default_code = settings.LANGUAGE_CODE.split("-")[0]
    return [
        {
            "code": code,
            "label": str(label),
            "required": code == default_code,
        }
        for code, label in getattr(settings, "CONTENT_LANGUAGES", settings.LANGUAGES)
    ]


def _validate_translations(value, *, require_default):
    if not isinstance(value, dict):
        raise serializers.ValidationError("Translations must be an object keyed by language code.")

    languages = {item["code"] for item in available_content_languages()}
    default_code = settings.LANGUAGE_CODE.split("-")[0]
    cleaned = {}

    for language_code, content in value.items():
        if language_code not in languages:
            raise serializers.ValidationError(
                f"Unsupported language code: {language_code}."
            )
        if not isinstance(content, dict):
            raise serializers.ValidationError(
                {language_code: "The localized value must be an object."}
            )

        name = str(content.get("name", "")).strip()
        description = str(content.get("description", "")).strip()
        if name or description:
            cleaned[language_code] = {
                "name": name,
                "description": description,
            }

    if require_default and not cleaned.get(default_code, {}).get("name"):
        raise serializers.ValidationError(
            {default_code: "A name is required in the default language."}
        )

    return cleaned


def _serialize_translations(items):
    return {
        item.language_code: {
            "name": item.name,
            "description": item.description,
        }
        for item in items
    }


def _replace_translations(*, instance, model, relation_name, owner_field, translations):
    existing = {
        translation.language_code: translation
        for translation in getattr(instance, relation_name).all()
    }

    for language_code, content in translations.items():
        name = content["name"]
        description = content["description"]
        if not name and not description:
            continue
        model.objects.update_or_create(
            **{
                owner_field: instance,
                "language_code": language_code,
            },
            defaults={"name": name, "description": description},
        )

    removed_codes = set(existing) - set(translations)
    if removed_codes:
        getattr(instance, relation_name).filter(language_code__in=removed_codes).delete()


class SpeciesReferenceSerializer(serializers.ModelSerializer):
    translations = serializers.SerializerMethodField()
    strain_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = Species
        fields = [
            "id",
            "scientific_name",
            "genus_species_code",
            "worms_aphia_id",
            "is_described",
            "notes",
            "translations",
            "strain_count",
        ]

    def get_translations(self, obj):
        return _serialize_translations(obj.translations.all())


class StrainReferenceSerializer(serializers.ModelSerializer):
    translations = serializers.SerializerMethodField()
    species_scientific_name = serializers.CharField(
        source="species.scientific_name",
        read_only=True,
    )

    class Meta:
        model = Strain
        fields = [
            "id",
            "species",
            "species_scientific_name",
            "code",
            "number",
            "origin_code",
            "notes",
            "translations",
        ]

    def get_translations(self, obj):
        return _serialize_translations(obj.translations.all())


class SpeciesReferenceWriteSerializer(serializers.ModelSerializer):
    translations = serializers.JSONField(write_only=True)

    class Meta:
        model = Species
        fields = [
            "scientific_name",
            "genus_species_code",
            "worms_aphia_id",
            "is_described",
            "notes",
            "translations",
        ]

    def validate_genus_species_code(self, value):
        return value.strip().upper()

    def validate_translations(self, value):
        return _validate_translations(value, require_default=True)

    @transaction.atomic
    def create(self, validated_data):
        translations = validated_data.pop("translations")
        french_name = translations.get("fr", {}).get("name", "")
        species = Species.objects.create(common_name=french_name, **validated_data)
        _replace_translations(
            instance=species,
            model=SpeciesTranslation,
            relation_name="translations",
            owner_field="species",
            translations=translations,
        )
        return species

    @transaction.atomic
    def update(self, instance, validated_data):
        translations = validated_data.pop("translations", None)
        if translations is not None:
            french_name = translations.get("fr", {}).get("name", "")
            instance.common_name = french_name
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if translations is not None:
            _replace_translations(
                instance=instance,
                model=SpeciesTranslation,
                relation_name="translations",
                owner_field="species",
                translations=translations,
            )
        return instance


class StrainReferenceWriteSerializer(serializers.ModelSerializer):
    translations = serializers.JSONField(write_only=True)

    class Meta:
        model = Strain
        fields = [
            "species",
            "code",
            "number",
            "origin_code",
            "notes",
            "translations",
        ]

    def validate_code(self, value):
        return value.strip().upper()

    def validate_origin_code(self, value):
        return value.strip().upper()

    def validate_translations(self, value):
        return _validate_translations(value, require_default=True)

    @transaction.atomic
    def create(self, validated_data):
        translations = validated_data.pop("translations")
        strain = Strain.objects.create(**validated_data)
        _replace_translations(
            instance=strain,
            model=StrainTranslation,
            relation_name="translations",
            owner_field="strain",
            translations=translations,
        )
        return strain

    @transaction.atomic
    def update(self, instance, validated_data):
        translations = validated_data.pop("translations", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        if translations is not None:
            _replace_translations(
                instance=instance,
                model=StrainTranslation,
                relation_name="translations",
                owner_field="strain",
                translations=translations,
            )
        return instance
