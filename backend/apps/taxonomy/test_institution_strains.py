"""Strain creation from an institutional Species: AAA-BBB-X built by the server.

Within one institution a country (ISO 3166-1 alpha-2) has exactly one BBB and a
BBB exactly one country.
"""

import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from importlib import import_module
from threading import Barrier, Event
from unittest import skipUnless
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import DatabaseError, IntegrityError, close_old_connections, connection, transaction
from django.db.migrations.exceptions import IrreversibleError
from django.urls import reverse

from config.migration_test_utils import MigrationExecutor

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.box_identity import USABLE, strain_box_identity
from apps.cultures.models import Box
from apps.organizations.models import Organization

from . import institution_strains
from .countries import ISO_3166_ALPHA2_CODES, normalize_country_code
from .models import (
    BiologicalProvenance,
    InstitutionSpecies,
    InstitutionStrainCreationRequest,
    LocalStrainIdentity,
    LocalStrainNumberCounter,
    OrganizationProvenanceCode,
    OrganizationSpeciesCode,
    Origin,
    Species,
    Strain,
    StrainTranslation,
)
from .test_institution_species_api import InstitutionSpeciesApiBase, _snapshot
from .test_species_review_regressions import SpeciesReviewTransactionTestCase

STRAINS_URL = "api_admin_institution_species_strains"
LOCATIONS_URL = "api_admin_collection_locations"
FRANCE = {"country": "FR", "country_name": "France", "bbb_code": "FRA"}


class InstitutionStrainBase(InstitutionSpeciesApiBase):
    def setUp(self):
        super().setUp()
        self.species = Species.objects.create(scientific_name="Aurelia aurita")
        self.aaa = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.species, code="AAU")
        self.profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.species, scientific_name="Aurelia aurita",
        )
        self.other_species = Species.objects.create(scientific_name="Cassiopea andromeda")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=self.other_species, code="CAN")
        self.other_profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.other_species, scientific_name="Cassiopea andromeda",
        )

    def create(self, payload, *, organization=None, profile=None, user=None, request_id=None):
        organization = organization or self.a
        self.client.force_login(user or (self.admin if organization == self.a else self.b_admin))
        # A fresh key per call unless the test replays one deliberately.
        body = {"request_id": str(request_id or uuid.uuid4()), **payload}
        return self.client.post(
            reverse(STRAINS_URL, args=[(profile or self.profile).pk]), data=json.dumps(body),
            content_type="application/json", **self.headers(organization),
        )

    def france(self, **kwargs):
        extra = kwargs.pop("extra", {})
        return self.create({**FRANCE, **extra}, **kwargs)

    def location(self, organization, name, code, country=None):
        provenance = BiologicalProvenance.objects.create(name=name)
        return OrganizationProvenanceCode.objects.create(
            organization=organization, biological_provenance=provenance, code=code, country_code=country,
        )


class CountryCodesTests(InstitutionStrainBase):
    def test_iso_list_is_complete_and_canonical(self):
        self.assertEqual(len(ISO_3166_ALPHA2_CODES), 249)
        self.assertEqual(len(set(ISO_3166_ALPHA2_CODES)), 249)
        self.assertEqual(normalize_country_code(" fr "), "FR")
        for value in ("FRA", "France", "XX", "", None, 33):
            self.assertIsNone(normalize_country_code(value))


class InstitutionStrainCreationTests(InstitutionStrainBase):
    def test_species_registration_creates_no_strain_then_a_strain_can_follow(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("api_admin_institution_species"), data=json.dumps({"scientific_name": "Chrysaora fuscescens"}),
            content_type="application/json", **self.headers(self.a),
        )
        self.assertEqual(response.status_code, 201, response.content)
        profile = InstitutionSpecies.objects.get(pk=response.json()["id"])
        code = response.json()["species_code"]
        self.assertRegex(code, r"^[A-Z]{3}$")
        # Registering a Species never creates a Strain implicitly.
        self.assertFalse(Strain.objects.filter(species=profile.species).exists())
        created = self.france(profile=profile)
        self.assertEqual(created.status_code, 201, created.content)
        self.assertEqual(created.json()["strain"]["code"], f"{code}-FRA-1")
        self.assertEqual(Strain.objects.filter(species=profile.species).count(), 1)

    def test_new_country_creates_coded_location_strain_identity_and_audit(self):
        before_boxes = list(Box.objects.values())
        response = self.create({"country": "fr", "country_name": "  France ", "bbb_code": "fra"})
        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        strain = Strain.objects.get()
        self.assertEqual((strain.code, strain.number, strain.origin_code), ("AAU-FRA-1", 1, "FRA"))
        self.assertEqual((strain.species_id, strain.organization_id, strain.origin_id), (self.species.pk, self.a.pk, None))
        location = OrganizationProvenanceCode.objects.get()
        self.assertEqual((location.organization_id, location.code, location.country_code,
                          location.biological_provenance.name), (self.a.pk, "FRA", "FR", "France"))
        identity = LocalStrainIdentity.objects.get(strain=strain)
        self.assertEqual((identity.species_code_assignment_id, identity.provenance_code_assignment_id),
                         (self.aaa.pk, location.pk))
        self.assertEqual(strain_box_identity(strain, self.a)["status"], USABLE)
        self.assertEqual(body["strain"], {
            "id": strain.pk, "code": "AAU-FRA-1", "name": "", "strain_number": "1", "active_box_count": 0,
            "is_box_usable": True, "recorded_provenance_name": "France", "recorded_country_code": "FR",
            "is_owned": True,
        })
        self.assertEqual([item["code"] for item in body["strains"]], ["AAU-FRA-1"])
        audits = list(AuditLog.objects.order_by("pk").values_list("object_type", "action", "organization_id"))
        self.assertEqual(audits, [
            ("biological_provenance", "creation", self.a.pk),
            ("organization_provenance_code", "creation", self.a.pk),
            ("strain", "creation", self.a.pk),
        ])
        strain_audit = AuditLog.objects.get(object_type="strain")
        self.assertEqual(strain_audit.description, "Strain created: AAU-FRA-1")
        self.assertEqual(strain_audit.metadata, {
            "institution_species_id": self.profile.pk, "species_id": self.species.pk, "code": "AAU-FRA-1",
            "species_code": "AAU", "collection_location_code": "FRA",
            "collection_location_name": "France", "strain_number": 1, "country_code": "FR",
            "acquisition_type": None, "donor_institution": None, "latitude": None, "longitude": None,
            "name": None, "name_language": None,
        })
        self.assertEqual(list(Box.objects.values()), before_boxes)

    def test_country_is_required(self):
        for payload, expected in (
            ({}, ["required"]), ({"country": ""}, ["required"]), ({"country": "France"}, ["invalid"]),
            ({"country": "XX"}, ["invalid"]), ({"country": 33}, ["invalid"]),
        ):
            with self.subTest(payload=payload):
                response = self.create({**payload, "bbb_code": "FRA"})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json()["country"], expected)
        self.assertFalse(Strain.objects.exists())

    def test_same_country_reuses_its_bbb_and_x_increments_within_species(self):
        self.assertEqual(self.france().status_code, 201)
        # Once coded, the country alone is enough; a matching BBB is accepted.
        codes = [self.create({"country": "FR"}).json()["strain"]["code"],
                 self.create({"country": "FR", "bbb_code": "FRA"}).json()["strain"]["code"]]
        self.assertEqual(codes, ["AAU-FRA-2", "AAU-FRA-3"])
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 1)
        self.assertEqual(BiologicalProvenance.objects.count(), 1)

    def test_same_country_cannot_receive_a_second_bbb(self):
        france = self.location(self.a, "France", "FRA", "FR")
        response = self.create({"country": "FR", "bbb_code": "FRX"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json(), {"code": "country_already_coded", "collection_location": {
            "id": france.pk, "code": "FRA", "name": "France", "country": "FR"}})
        other = self.location(self.a, "Legacy", "LEG")
        linked = self.create({"country": "FR", "link_collection_location": other.pk})
        self.assertEqual(linked.json()["code"], "country_already_coded")
        self.assertFalse(Strain.objects.exists())
        self.assertIsNone(OrganizationProvenanceCode.objects.get(pk=other.pk).country_code)

    def test_same_bbb_cannot_represent_two_countries(self):
        france = self.location(self.a, "France", "FRA", "FR")
        response = self.create({"country": "JP", "country_name": "Japon", "bbb_code": "FRA"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "collection_location_code_taken")
        self.assertEqual(response.json()["collection_location"]["country"], "FR")
        # Linking a location that already has a country never replaces it.
        response = self.create({"country": "JP", "link_collection_location": france.pk})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["code"], "collection_location_country_conflict")
        france.refresh_from_db()
        self.assertEqual((france.code, france.country_code), ("FRA", "FR"))
        self.assertFalse(Strain.objects.exists())

    def test_database_enforces_one_bbb_per_country_and_valid_codes(self):
        self.location(self.a, "France", "FRA", "FR")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.location(self.a, "France bis", "FRB", "FR")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.location(self.a, "Bad", "BAD", "fr")
        # Each institution keeps its own correspondence.
        self.location(self.b, "France", "FRX", "FR")
        self.assertEqual(OrganizationProvenanceCode.objects.filter(country_code="FR").count(), 2)

    def test_different_countries_get_distinct_mappings(self):
        self.assertEqual(self.france().status_code, 201)
        japan = self.create({"country": "JP", "country_name": "Japon", "bbb_code": "JPN"})
        self.assertEqual(japan.json()["strain"]["code"], "AAU-JPN-1")
        self.assertEqual(sorted(OrganizationProvenanceCode.objects.values_list("country_code", "code")),
                         [("FR", "FRA"), ("JP", "JPN")])

    def test_multiple_species_share_the_country_mapping_with_independent_x(self):
        self.france()
        self.create({"country": "FR"})
        response = self.create({"country": "FR"}, profile=self.other_profile)
        self.assertEqual(response.json()["strain"]["code"], "CAN-FRA-1")
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 2)
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 1)
        self.assertEqual(set(Strain.objects.values_list("origin_code", flat=True)), {"FRA"})

    def test_legacy_location_without_country_is_linked_only_on_request(self):
        legacy = self.location(self.a, "France", "FRA")
        # A new mapping never duplicates a recorded name or code silently.
        same_name = self.create({"country": "FR", "country_name": "france", "bbb_code": "FRX"})
        self.assertEqual(same_name.status_code, 409)
        self.assertEqual(same_name.json()["code"], "collection_location_name_taken")
        self.assertEqual(same_name.json()["collection_location"], {
            "id": legacy.pk, "code": "FRA", "name": "France", "country": None})
        same_code = self.create({"country": "FR", "country_name": "Hexagone", "bbb_code": "FRA"})
        self.assertEqual(same_code.json()["code"], "collection_location_code_taken")
        self.assertIsNone(OrganizationProvenanceCode.objects.get().country_code)
        self.assertFalse(AuditLog.objects.exists())
        # Explicit link: the existing BBB becomes this country's BBB, nothing is recoded.
        legacy_strain = Strain.objects.create(species=self.species, organization=self.a, code="AAU-FRA-1",
                                              number=1, origin_code="FRA")
        response = self.create({"country": "FR", "link_collection_location": legacy.pk})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["strain"]["code"], "AAU-FRA-2")
        legacy.refresh_from_db()
        self.assertEqual((legacy.code, legacy.country_code), ("FRA", "FR"))
        legacy_strain.refresh_from_db()
        self.assertEqual(legacy_strain.code, "AAU-FRA-1")
        link_audit = AuditLog.objects.get(object_type="organization_provenance_code")
        self.assertEqual((link_audit.action, link_audit.metadata["before"], link_audit.metadata["after"]),
                         ("update", {"country_code": None}, {"country_code": "FR"}))
        self.assertEqual(self.create({"country": "FR"}).json()["strain"]["code"], "AAU-FRA-3")

    def test_bbb_is_required_for_a_new_country(self):
        response = self.create({"country": "FR"})
        self.assertEqual((response.status_code, response.json()), (400, {"bbb_code": ["required"]}))
        self.assertFalse(OrganizationProvenanceCode.objects.exists())

    def test_existing_identifiers_are_never_reused_or_rewritten(self):
        self.location(self.a, "France", "FRA", "FR")
        # Historical identifiers anywhere: unowned legacy code, zero-padded X, a
        # Box Local ID of another Species, and another institution's Strain.
        legacy = Strain.objects.create(species=self.species, code="AAU-FRA-01")
        legacy_box_species = Species.objects.create(scientific_name="Legacy box species")
        old = Strain.objects.create(species=legacy_box_species, code="OLD-STRAIN")
        Box.objects.create(organization=self.b, strain=old, global_code="AAU-FRA-2.001", box_number="001")
        Strain.objects.create(species=self.other_species, organization=self.b, code="AAU-FRA-3")
        before = _snapshot()
        response = self.create({"country": "FR"})
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["strain"]["code"], "AAU-FRA-4")
        after = _snapshot()
        self.assertEqual([row for row in after["strains"] if row["pk"] != response.json()["strain"]["id"]],
                         before["strains"])
        self.assertEqual(after["boxes"], before["boxes"])
        legacy.refresh_from_db()
        self.assertEqual((legacy.code, legacy.organization_id), ("AAU-FRA-01", None))

    def test_acquisition_donor_and_gps_are_recorded_on_the_strain(self):
        response = self.france(extra={
            "acquisition_type": "donation", "donor_institution": "  Aquarium   externe ",
            "latitude": "48.390001", "longitude": -4.486,
        })
        self.assertEqual(response.status_code, 201, response.content)
        strain = Strain.objects.get()
        origin = strain.origin
        self.assertEqual((origin.source_type, origin.origin_institution_name, origin.latitude, origin.longitude),
                         ("donation", "Aquarium externe", Decimal("48.390001"), Decimal("-4.486000")))
        # The same country can hold Strains with distinct coordinates.
        second = self.create({"country": "FR", "acquisition_type": "field_collection",
                              "latitude": 0, "longitude": 0})
        self.assertEqual(second.status_code, 201, second.content)
        zero = Strain.objects.get(pk=second.json()["strain"]["id"]).origin
        self.assertEqual((zero.source_type, zero.latitude, zero.longitude, zero.origin_institution_name),
                         ("field_collection", Decimal("0"), Decimal("0"), ""))
        for kind in ("artificial_fertilization_wild", "artificial_fertilization_captive"):
            created = self.create({"country": "FR", "acquisition_type": kind})
            self.assertEqual(Strain.objects.get(pk=created.json()["strain"]["id"]).origin.source_type, kind)
        detail = self.client.get(reverse("api_admin_institution_species_detail", args=[self.profile.pk]),
                                 **self.headers(self.a)).json()
        first = next(item for item in detail["strains"] if item["id"] == strain.pk)
        self.assertEqual(first["origin"]["origin_institution_name"], "Aquarium externe")
        self.assertEqual((first["origin"]["latitude"], first["recorded_country_code"]), ("48.390001", "FR"))

    def test_no_acquisition_information_creates_no_origin(self):
        self.france()
        self.assertIsNone(Strain.objects.get().origin_id)
        self.assertFalse(Origin.objects.exists())

    def test_acquisition_donor_and_gps_validation(self):
        cases = [
            ({"acquisition_type": "exchange"}, {"acquisition_type": ["invalid"]}),
            ({"acquisition_type": "reproduction"}, {"acquisition_type": ["invalid"]}),
            ({"donor_institution": "Aquarium"}, {"donor_institution": ["donation_only"]}),
            ({"acquisition_type": "field_collection", "donor_institution": "Aquarium"},
             {"donor_institution": ["donation_only"]}),
            ({"acquisition_type": "donation", "donor_institution": "x" * 151}, {"donor_institution": ["too_long"]}),
            ({"latitude": "48.1"}, {"longitude": ["required_pair"]}),
            ({"longitude": 2}, {"latitude": ["required_pair"]}),
            ({"latitude": 90.5, "longitude": 0}, {"latitude": ["out_of_range"]}),
            ({"latitude": 0, "longitude": -180.01}, {"longitude": ["out_of_range"]}),
            ({"latitude": "1.1234567", "longitude": 0}, {"latitude": ["too_precise"]}),
            ({"latitude": "nord", "longitude": 0}, {"latitude": ["invalid"]}),
            ({"latitude": "NaN", "longitude": 0}, {"latitude": ["invalid"]}),
            ({"latitude": True, "longitude": 0}, {"latitude": ["invalid"]}),
            ({"name": "Souche A"}, {"name_language": ["invalid"]}),
            ({"name": "Souche A", "name_language": "de"}, {"name_language": ["invalid"]}),
            ({"name": "x" * 151, "name_language": "fr"}, {"name": ["too_long"]}),
            ({"code": "AAU-FRA-9"}, {"code": ["not_allowed"]}),
            ({"number": 9}, {"number": ["not_allowed"]}),
            ({"collection_location": 1}, {"collection_location": ["not_allowed"]}),
            ({"bbb_code": "FR1"}, {"bbb_code": ["invalid_format"]}),
            ({"link_collection_location": 1}, {"link_collection_location": ["exclusive_with_bbb_code"]}),
            ({"request_id": "not-a-uuid"}, {"request_id": ["invalid"]}),
            ({"request_id": None}, {"request_id": ["invalid"]}),
        ]
        for extra, expected in cases:
            with self.subTest(extra=extra):
                response = self.create({**FRANCE, **extra})
                self.assertEqual(response.status_code, 400, response.content)
                self.assertEqual(response.json(), expected)
        for value in ("0", True, 0):
            response = self.create({"country": "FR", "link_collection_location": value})
            self.assertEqual(response.json(), {"link_collection_location": ["invalid"]})
        self.assertFalse(Strain.objects.exists())
        self.assertFalse(OrganizationProvenanceCode.objects.exists())

    def test_optional_name_is_stored_once_and_fallback_is_never_persisted(self):
        named = self.france(extra={"name": "  Lignée   Brest ", "name_language": "fr"})
        self.assertEqual(named.status_code, 201, named.content)
        strain = Strain.objects.get(pk=named.json()["strain"]["id"])
        self.assertEqual(list(strain.translations.values_list("language_code", "name")), [("fr", "Lignée Brest")])
        self.assertEqual(named.json()["strain"]["name"], "Lignée Brest")
        unnamed = self.create({"country": "FR", "name": "   ", "name_language": "fr"})
        self.assertEqual(unnamed.status_code, 201, unnamed.content)
        self.assertEqual(unnamed.json()["strain"]["name"], "")
        self.assertEqual(unnamed.json()["strain"]["strain_number"], "2")
        self.assertFalse(StrainTranslation.objects.filter(strain_id=unnamed.json()["strain"]["id"]).exists())
        self.assertEqual(StrainTranslation.objects.count(), 1)

    def test_creation_preserves_existing_translations_of_other_strains(self):
        legacy = Strain.objects.create(species=self.species, organization=self.a, code="LEGACY")
        StrainTranslation.objects.create(strain=legacy, language_code="fr", name="Ancienne", description="d")
        StrainTranslation.objects.create(strain=legacy, language_code="ja", name="古い")
        before = list(StrainTranslation.objects.values())
        self.france(extra={"name": "Nouvelle", "name_language": "en"})
        self.assertEqual(list(StrainTranslation.objects.filter(strain=legacy).values()), before)

    def test_unusable_identifiers_are_refused_without_writes(self):
        self.location(self.a, "Legacy location", "BR", "BE")
        response = self.create({"country": "BE"})
        self.assertEqual((response.status_code, response.json()), (409, {"code": "collection_location_code_unusable"}))
        self.aaa.code = "AU"
        self.aaa.save()
        self.location(self.a, "France", "FRA", "FR")
        response = self.create({"country": "FR"})
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_unusable"}))
        self.aaa.delete()
        response = self.create({"country": "FR"})
        self.assertEqual((response.status_code, response.json()), (409, {"code": "species_code_missing"}))
        self.assertFalse(Strain.objects.exists())
        self.assertFalse(LocalStrainNumberCounter.objects.exists())
        self.assertFalse(AuditLog.objects.exists())

    def test_permissions_and_institution_isolation(self):
        self.location(self.a, "France", "FRA", "FR")
        b_location = self.location(self.b, "Private B location", "PRB", "JP")
        for user in (self.tech, self.viewer, self.b_admin):
            with self.subTest(user=user.username):
                self.assertEqual(self.create({"country": "FR"}, user=user).status_code, 403)
        # B's admin cannot reach A's profile, even with its own context.
        self.assertEqual(self.create({"country": "JP"}, organization=self.b).status_code, 404)
        # Another institution's country mapping is never visible or reusable.
        foreign = self.create({"country": "JP"})
        self.assertEqual((foreign.status_code, foreign.json()), (400, {"bbb_code": ["required"]}))
        foreign_link = self.create({"country": "JP", "link_collection_location": b_location.pk})
        self.assertEqual((foreign_link.status_code, foreign_link.json()),
                         (400, {"link_collection_location": ["not_found"]}))
        # The same BBB stays available to A for another country.
        self.assertEqual(self.create({"country": "JP", "country_name": "Japon", "bbb_code": "PRB"}).status_code, 201)
        self.assertEqual(OrganizationProvenanceCode.objects.get(pk=b_location.pk).organization_id, self.b.pk)
        self.client.force_login(self.admin)
        missing_context = self.client.post(reverse(STRAINS_URL, args=[self.profile.pk]),
                                           data=json.dumps({"country": "FR"}),
                                           content_type="application/json")
        self.assertEqual(missing_context.status_code, 403)

    def test_locations_list_is_scoped_admin_only_and_lists_countries(self):
        self.location(self.a, "France", "FRA", "FR")
        self.location(self.a, "Lagune", "LAG")
        self.location(self.b, "Private B location", "PRB", "JP")
        self.client.force_login(self.admin)
        response = self.client.get(reverse(LOCATIONS_URL), **self.headers(self.a))
        self.assertEqual(response.status_code, 200)
        self.assertEqual([(item["code"], item["name"], item["country"])
                          for item in response.json()["collection_locations"]],
                         [("FRA", "France", "FR"), ("LAG", "Lagune", None)])
        self.assertEqual(response.json()["countries"], list(ISO_3166_ALPHA2_CODES))
        self.assertNotIn("Private B", response.content.decode())
        for user in (self.tech, self.viewer):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse(LOCATIONS_URL), **self.headers(self.a)).status_code, 403)

    def test_failure_rolls_back_location_counter_strain_origin_name_and_audit(self):
        with patch.object(institution_strains, "create_local_strain_identity",
                          side_effect=ValidationError("boom", code="identity_exists")):
            response = self.france(extra={"acquisition_type": "donation", "name": "N", "name_language": "fr"})
        self.assertEqual((response.status_code, response.json()), (409, {"code": "strain_identity_invalid"}))
        for model in (Strain, OrganizationProvenanceCode, BiologicalProvenance, LocalStrainNumberCounter, AuditLog,
                      Origin, StrainTranslation):
            self.assertFalse(model.objects.exists(), model.__name__)

    def test_failed_link_rolls_back_the_country(self):
        legacy = self.location(self.a, "France", "FRA")
        with patch.object(institution_strains, "create_local_strain_identity",
                          side_effect=ValidationError("boom", code="identity_exists")):
            self.create({"country": "FR", "link_collection_location": legacy.pk})
        self.assertIsNone(OrganizationProvenanceCode.objects.get().country_code)
        self.assertFalse(AuditLog.objects.exists())

    def test_manual_legacy_creation_is_closed(self):
        self.client.force_login(self.admin)
        payload = {"species": self.species.pk, "code": "AAU-BRE-7", "number": 7,
                   "translations": {"fr": {"name": "Souche"}}}
        response = self.client.post(reverse("api_taxonomy_strains"), data=json.dumps(payload),
                                    content_type="application/json", **self.headers(self.a))
        self.assertEqual((response.status_code, response.json()),
                         (410, {"code": "superseded_by_institution_species_strains"}))
        self.client.force_login(self.tech)
        response = self.client.post(reverse("api_taxonomy_strains"), data=json.dumps(payload),
                                    content_type="application/json", **self.headers(self.a))
        self.assertEqual(response.status_code, 403)
        self.assertFalse(Strain.objects.exists())


class InstitutionStrainIdempotencyTests(InstitutionStrainBase):
    """Durable request idempotency for institutional Strain creation."""

    def test_replay_returns_the_recorded_strain_without_allocating_again(self):
        self.location(self.a, "France", "FRA", "FR")
        key = uuid.uuid4()
        payload = {"country": "FR", "acquisition_type": "donation", "donor_institution": "Aquarium",
                   "latitude": "1.5", "longitude": "2", "name": "Nom", "name_language": "fr"}
        first = self.create(payload, request_id=key)
        self.assertEqual(first.status_code, 201, first.content)
        before = _snapshot()
        counts = (Origin.objects.count(), StrainTranslation.objects.count(), AuditLog.objects.count())
        # Equivalent normalized values are the same intent.
        replay = self.create({**payload, "country": " fr ", "latitude": 1.5, "longitude": "2.000000",
                              "name": " Nom "}, request_id=key)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json()["strain"]["id"], first.json()["strain"]["id"])
        self.assertEqual(Strain.objects.count(), 1)
        self.assertEqual(LocalStrainNumberCounter.objects.count(), 1)
        self.assertEqual(InstitutionStrainCreationRequest.objects.count(), 1)
        self.assertEqual((Origin.objects.count(), StrainTranslation.objects.count(), AuditLog.objects.count()), counts)
        self.assertEqual(_snapshot(), before)

    def test_replay_of_a_new_country_does_not_create_a_second_location(self):
        key = uuid.uuid4()
        first = self.france(request_id=key)
        self.assertEqual(first.status_code, 201, first.content)
        replay = self.france(request_id=key)
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json()["strain"]["code"], first.json()["strain"]["code"])
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 1)
        self.assertEqual(BiologicalProvenance.objects.count(), 1)
        self.assertEqual(Strain.objects.count(), 1)
        self.assertEqual(AuditLog.objects.count(), 3)

    def test_same_key_with_a_different_intent_is_a_conflict(self):
        self.location(self.a, "France", "FRA", "FR")
        key = uuid.uuid4()
        self.assertEqual(self.create({"country": "FR"}, request_id=key).status_code, 201)
        for change in ({"country": "JP", "bbb_code": "JPN"}, {"country": "FR", "name": "N", "name_language": "fr"},
                       {"country": "FR", "acquisition_type": "field_collection"},
                       {"country": "FR", "latitude": 1, "longitude": 1}):
            with self.subTest(change=change):
                conflict = self.create(change, request_id=key)
                self.assertEqual((conflict.status_code, conflict.json()),
                                 (409, {"code": "strain_creation_request_conflict"}))
        # Another profile with the same key is also a materially different intent.
        other = self.create({"country": "FR"}, profile=self.other_profile, request_id=key)
        self.assertEqual(other.status_code, 409)
        self.assertEqual(Strain.objects.count(), 1)
        self.assertEqual(OrganizationProvenanceCode.objects.count(), 1)

    def test_distinct_keys_allow_intentional_separate_strains(self):
        self.location(self.a, "France", "FRA", "FR")
        first = self.create({"country": "FR"}, request_id=uuid.uuid4())
        second = self.create({"country": "FR"}, request_id=uuid.uuid4())
        self.assertEqual(first.json()["strain"]["code"], "AAU-FRA-1")
        self.assertEqual(second.json()["strain"]["code"], "AAU-FRA-2")
        self.assertEqual(Strain.objects.count(), 2)

    def test_keys_do_not_cross_institution_boundaries(self):
        b_profile = InstitutionSpecies.objects.create(
            organization=self.b, species=self.species, scientific_name="Aurelia aurita",
        )
        OrganizationSpeciesCode.objects.create(organization=self.b, species=self.species, code="AUB")
        self.location(self.a, "France", "FRA", "FR")
        self.location(self.b, "France", "PRB", "FR")
        key = uuid.uuid4()
        self.assertEqual(self.create({"country": "FR"}, request_id=key).status_code, 201)
        # The same key in another institution is a different scope, never a replay.
        response = self.create({"country": "FR"}, organization=self.b, profile=b_profile, request_id=key)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()["strain"]["code"], "AUB-PRB-1")
        self.assertEqual(Strain.objects.count(), 2)

    def test_rollback_leaves_no_idempotency_receipt(self):
        key = uuid.uuid4()
        with patch.object(institution_strains, "create_local_strain_identity",
                          side_effect=ValidationError("boom", code="identity_exists")):
            response = self.france(request_id=key)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(InstitutionStrainCreationRequest.objects.exists())
        # The same key can be retried cleanly after the failure.
        retry = self.france(request_id=key)
        self.assertEqual(retry.status_code, 201, retry.content)
        self.assertEqual(InstitutionStrainCreationRequest.objects.count(), 1)


@skipUnless(connection.vendor == "postgresql", "Requires a new verified disposable PostgreSQL instance.")
class InstitutionStrainConcurrencyTests(SpeciesReviewTransactionTestCase):
    """True concurrent creations with PostgreSQL row locks."""

    def setUp(self):
        users = get_user_model().objects
        self.a = Organization.objects.create(name="Strain race A")
        self.b = Organization.objects.create(name="Strain race B")
        self.admins = {}
        for organization in (self.a, self.b):
            admin = users.create_user(username=f"sr-{organization.pk}", email=f"sr{organization.pk}@example.org")
            OrganizationMembership.objects.create(user=admin, organization=organization,
                                                  role=OrganizationMembership.Role.ADMIN)
            self.admins[organization.pk] = admin
        self.species = Species.objects.create(scientific_name="Race species")
        self.b_species = Species.objects.create(scientific_name="Other race species")
        self.profiles = {}
        for organization, species in ((self.a, self.species), (self.b, self.b_species)):
            # Two institutions may legitimately hold the same AAA for different Species.
            OrganizationSpeciesCode.objects.create(organization=organization, species=species, code="RAC")
            self.profiles[organization.pk] = InstitutionSpecies.objects.create(
                organization=organization, species=species, scientific_name=species.scientific_name,
            )

    def run_parallel(self, calls):
        barrier = Barrier(len(calls))

        def worker(call):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                organization, data = call
                try:
                    strain, created = institution_strains.create_institution_strain(
                        user=self.admins[organization.pk], organization=organization,
                        profile_pk=self.profiles[organization.pk].pk, data=data,
                    )
                except institution_strains.InstitutionSpeciesConflict as error:
                    return ("conflict", error.code)
                return (strain.code, created)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            return [future.result(timeout=30) for future in [pool.submit(worker, call) for call in calls]]

    def lagoon(self):
        return {"request_id": str(uuid.uuid4()), "country": "NC", "country_name": "Lagoon", "bbb_code": "LAG"}

    def test_concurrent_first_use_of_a_country_creates_one_bbb(self):
        results = self.run_parallel([(self.a, self.lagoon()) for _ in range(3)])
        self.assertEqual(sorted(code for code, _created in results), ["RAC-LAG-1", "RAC-LAG-2", "RAC-LAG-3"])
        self.assertEqual(OrganizationProvenanceCode.objects.filter(organization=self.a).count(), 1)
        self.assertEqual(LocalStrainIdentity.objects.count(), 3)
        self.assertEqual(AuditLog.objects.filter(object_type="strain").count(), 3)

    def test_concurrent_conflicting_bbbs_for_one_country_never_create_two(self):
        results = self.run_parallel([
            (self.a, {"request_id": str(uuid.uuid4()), "country": "NC", "country_name": "Lagoon", "bbb_code": code})
            for code in ("LAG", "LGN")
        ])
        conflicts = [value for kind, value in results if kind == "conflict"]
        self.assertEqual(conflicts, ["country_already_coded"])
        self.assertIn(next(kind for kind, _value in results if kind != "conflict"), ("RAC-LAG-1", "RAC-LGN-1"))
        self.assertEqual(OrganizationProvenanceCode.objects.filter(organization=self.a).count(), 1)

    def test_identical_prefixes_across_institutions_never_share_a_code(self):
        results = self.run_parallel([(self.a, self.lagoon()), (self.b, self.lagoon())])
        self.assertEqual(sorted(code for code, _created in results), ["RAC-LAG-1", "RAC-LAG-2"])
        self.assertEqual(len(set(Strain.objects.values_list("code", flat=True))), 2)

    def test_concurrent_identical_submissions_create_exactly_one_strain(self):
        payload = self.lagoon()
        results = self.run_parallel([(self.a, payload) for _ in range(3)])
        self.assertEqual(len({code for code, _created in results}), 1)
        self.assertEqual(sum(1 for _code, created in results if created), 1)
        self.assertEqual(Strain.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="strain").count(), 1)


class InstitutionStrainReceiptMigrationTests(SpeciesReviewTransactionTestCase):
    """taxonomy.0013 rollback never drops Strain creation receipts."""

    old = ("taxonomy", "0012_institution_species_foundation")
    new = ("taxonomy", "0013_institutionstraincreationrequest")

    def setUp(self):
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        self.addCleanup(lambda: MigrationExecutor(connection).migrate(leaves))
        self.organization = Organization.objects.create(name="Strain receipt rollback")
        self.admin = get_user_model().objects.create_user(username="receipt-admin", email="receipt@example.org")
        species = Species.objects.create(scientific_name="Receipt species")
        # No collection country: 0015's own refusal must not pre-empt 0013's.
        self.strain = Strain.objects.create(organization=self.organization, species=species, code="REC-LAB-1")

    def receipt(self):
        return InstitutionStrainCreationRequest.objects.create(
            organization=self.organization, actor=self.admin, request_id=uuid.uuid4(),
            intent={"profile": 1}, strain=self.strain,
        )

    def receipts(self):
        return list(InstitutionStrainCreationRequest.objects.order_by("pk").values())

    def test_empty_reverse_drops_table_but_populated_reverse_is_refused(self):
        table = InstitutionStrainCreationRequest._meta.db_table
        leaves = MigrationExecutor(connection).loader.graph.leaf_nodes()
        MigrationExecutor(connection).migrate([self.old])
        self.assertNotIn(table, connection.introspection.table_names())
        self.assertNotIn(self.new, MigrationExecutor(connection).loader.applied_migrations)
        self.strain.refresh_from_db()
        MigrationExecutor(connection).migrate(leaves)
        self.assertIn(table, connection.introspection.table_names())

        receipt = self.receipt()
        before = self.receipts()
        with self.assertRaisesRegex(IrreversibleError, "Strain creation receipts must be retained"):
            MigrationExecutor(connection).migrate([self.old])
        self.assertIn(table, connection.introspection.table_names())
        self.assertIn(self.new, MigrationExecutor(connection).loader.applied_migrations)
        self.assertEqual(self.receipts(), before)
        MigrationExecutor(connection).migrate(leaves)
        self.assertEqual(self.receipts(), before)
        self.assertEqual(InstitutionStrainCreationRequest.objects.get(pk=receipt.pk).strain_id, self.strain.pk)

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL table locks")
    def test_rollback_preflight_fences_writers_and_refuses_busy_table(self):
        migration = import_module("apps.taxonomy.migrations.0013_institutionstraincreationrequest")

        def preflight():
            with connection.schema_editor(atomic=False) as editor:
                migration.refuse_receipt_loss(django_apps, editor)

        def blocked():
            close_old_connections()
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute("SET LOCAL lock_timeout = '200ms'")
                    self.receipt()
            except DatabaseError as error:
                cause = error.__cause__
                return getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
            finally:
                connection.close()
            return "committed"

        with transaction.atomic():
            preflight()
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(blocked).result(timeout=15), "55P03")
        self.assertEqual(self.receipts(), [])

        holding, release = Event(), Event()

        def hold_receipt():
            close_old_connections()
            try:
                with transaction.atomic():
                    self.receipt()
                    holding.set()
                    if not release.wait(timeout=10):
                        raise AssertionError("Competing receipt was not released")
            finally:
                connection.close()

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(hold_receipt)
            try:
                self.assertTrue(holding.wait(timeout=10))
                with self.assertRaises(DatabaseError) as caught, transaction.atomic():
                    preflight()
                cause = caught.exception.__cause__
                self.assertEqual(getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None), "55P03")
            finally:
                release.set()
            future.result(timeout=15)
        self.assertEqual(len(self.receipts()), 1)
        with self.assertRaisesRegex(IrreversibleError, "must be retained"), transaction.atomic():
            preflight()
