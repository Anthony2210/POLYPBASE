import io
import json
import socket
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.accounts.models import OrganizationMembership
from apps.audit.models import AuditLog
from apps.cultures.models import Box
from apps.organizations.models import Organization

from . import species_codes, worms
from .models import (
    InstitutionSpecies,
    LocalStrainIdentity,
    OrganizationSpeciesCode,
    Species,
    SpeciesTranslation,
    Strain,
    StrainTranslation,
)

CATALOG_URL = "api_admin_species_catalog"
CREATE_URL = "api_admin_species_create"
SEARCH_URL = "api_admin_worms_search"


def worms_record(aphia_id, name, *, status="accepted", valid_id=None, valid_name=None, **extra):
    record = {
        "AphiaID": aphia_id,
        "url": f"https://www.marinespecies.org/aphia.php?p=taxdetails&id={aphia_id}",
        "scientificname": name,
        "authority": "(Linnaeus, 1758)",
        "status": status,
        "unacceptreason": None if status == "accepted" else "synonym",
        "rank": "Species",
        "taxonRankID": 220,
        "valid_AphiaID": valid_id if valid_id is not None else aphia_id,
        "valid_name": valid_name if valid_name is not None else name,
        "valid_authority": "(Linnaeus, 1758)",
        "kingdom": "Animalia",
        "phylum": "Cnidaria",
        "class": "Scyphozoa",
        "order": "Semaeostomeae",
        "family": "Ulmaridae",
        "genus": name.split()[0],
    }
    record.update(extra)
    return record


class FakeResponse:
    def __init__(self, body, status=200):
        self.status = status
        self._body = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class AdminSpeciesCatalogTests(TestCase):
    def setUp(self):
        self.a = Organization.objects.create(name="Institution A")
        self.b = Organization.objects.create(name="Institution B")
        users = get_user_model().objects
        self.admin = users.create_user(username="catalog_admin", email="ca@example.org", password="secret")
        self.b_admin = users.create_user(username="catalog_b_admin", email="cb@example.org", password="secret")
        self.tech = users.create_user(username="catalog_tech", email="ct@example.org", password="secret")
        self.viewer = users.create_user(username="catalog_viewer", email="cv@example.org", password="secret")
        for user, organization, role in (
            (self.admin, self.a, OrganizationMembership.Role.ADMIN),
            (self.b_admin, self.b, OrganizationMembership.Role.ADMIN),
            (self.tech, self.a, OrganizationMembership.Role.LAB_TECHNICIAN),
            (self.viewer, self.a, OrganizationMembership.Role.VIEWER),
        ):
            OrganizationMembership.objects.create(user=user, organization=organization, role=role)

        self.aurelia = Species.objects.create(scientific_name="Aurelia aurita", worms_aphia_id=135306)
        SpeciesTranslation.objects.create(species=self.aurelia, language_code="fr", name="Méduse lune")
        self.cassiopea = Species.objects.create(scientific_name="Cassiopea andromeda")
        self.a_code = OrganizationSpeciesCode.objects.create(organization=self.a, species=self.aurelia, code="AUR")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=self.aurelia, code="BAU")

        self.a_strain = Strain.objects.create(
            species=self.aurelia, organization=self.a, code="AUR-FRA-1", number=1, origin_code="FRA",
        )
        StrainTranslation.objects.create(strain=self.a_strain, language_code="fr", name="Souche Paris")
        LocalStrainIdentity.objects.create(strain=self.a_strain, species_code_assignment=self.a_code)
        self.a_legacy = Strain.objects.create(species=self.aurelia, organization=self.a, code="legacy 7")
        self.b_strain = Strain.objects.create(species=self.aurelia, organization=self.b, code="BAU-JPN-1")
        for index, status in enumerate((Box.Status.ACTIVE, Box.Status.ACTIVE, Box.Status.INACTIVE), start=1):
            Box.objects.create(
                organization=self.a, strain=self.a_strain, status=status,
                global_code=f"AUR-FRA-1.{index:03d}", box_number=f"{index:03d}",
            )
        Box.objects.create(
            organization=self.b, strain=self.b_strain, status=Box.Status.ACTIVE,
            global_code="BAU-JPN-1.001", box_number="001",
        )

    def headers(self, organization):
        return {"HTTP_X_ORGANIZATION_ID": str(organization.pk)}

    def get_catalog(self, organization):
        return self.client.get(reverse(CATALOG_URL), **self.headers(organization))

    def post_species(self, organization, payload):
        return self.client.post(
            reverse(CREATE_URL), data=json.dumps(payload), content_type="application/json",
            **self.headers(organization),
        )

    def search(self, organization, query):
        return self.client.get(reverse(SEARCH_URL), {"q": query}, **self.headers(organization))

    # Catalog -----------------------------------------------------------------

    def test_catalog_groups_institution_strains_under_shared_species(self):
        self.client.force_login(self.admin)
        response = self.get_catalog(self.a)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual([item["code"] for item in data["languages"]][:2], ["fr", "en"])
        by_name = {item["scientific_name"]: item for item in data["species"]}
        self.assertEqual(set(by_name), {"Aurelia aurita", "Cassiopea andromeda"})
        aurelia = by_name["Aurelia aurita"]
        self.assertEqual(aurelia["species_code"], "AUR")
        self.assertEqual(aurelia["worms_aphia_id"], 135306)
        self.assertEqual(aurelia["translations"]["fr"]["name"], "Méduse lune")
        self.assertEqual(aurelia["strains"], [
            {
                "id": self.a_strain.pk, "code": "AUR-FRA-1", "name": "Souche Paris",
                "strain_number": "1", "active_box_count": 2, "is_box_usable": True,
            },
            {
                "id": self.a_legacy.pk, "code": "legacy 7", "name": "",
                "strain_number": None, "active_box_count": 0, "is_box_usable": False,
            },
        ])
        self.assertEqual(by_name["Cassiopea andromeda"]["strains"], [])
        self.assertIsNone(by_name["Cassiopea andromeda"]["species_code"])
        body = json.dumps(data)
        self.assertNotIn("BAU", body)
        self.assertNotIn("organization", body)

    def test_catalog_is_isolated_per_institution(self):
        self.client.force_login(self.b_admin)
        data = self.get_catalog(self.b).json()
        aurelia = next(item for item in data["species"] if item["scientific_name"] == "Aurelia aurita")
        self.assertEqual(aurelia["species_code"], "BAU")
        self.assertEqual([strain["code"] for strain in aurelia["strains"]], ["BAU-JPN-1"])
        self.assertEqual(aurelia["strains"][0]["active_box_count"], 1)
        self.assertNotIn("AUR-FRA-1", json.dumps(data))
        # A foreign context is refused rather than silently widened.
        self.assertEqual(self.get_catalog(self.a).status_code, 403)

    def test_catalog_requires_explicit_admin_context(self):
        self.assertEqual(self.client.get(reverse(CATALOG_URL), **self.headers(self.a)).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse(CATALOG_URL)).status_code, 403)
        for user in (self.tech, self.viewer):
            self.client.force_login(user)
            self.assertEqual(self.get_catalog(self.a).status_code, 403)

    # Closed shared-reference write paths ----------------------------------------

    def code_endpoint(self, species):
        return reverse("api_admin_species_code", args=[species.pk])

    def post_code(self, organization, species):
        return self.client.post(self.code_endpoint(species), content_type="application/json", **self.headers(organization))

    @patch("apps.taxonomy.worms.urlopen")
    def test_species_creation_and_first_code_paths_are_closed_without_writes(self, urlopen):
        species_before = list(Species.objects.order_by("pk").values())
        codes_before = list(OrganizationSpeciesCode.objects.order_by("pk").values())
        self.client.force_login(self.admin)
        for response in (
            self.post_species(self.a, {"scientific_name": "Chrysaora hysoscella"}),
            self.post_species(self.a, {"scientific_name": "Aurelia coerulea", "worms_aphia_id": 287206}),
            self.post_code(self.a, self.cassiopea),
            self.post_code(self.a, self.aurelia),
        ):
            self.assertEqual(response.status_code, 410, response.content)
            self.assertEqual(response.json(), {"code": "superseded_by_institution_species"})
        urlopen.assert_not_called()
        self.assertEqual(list(Species.objects.order_by("pk").values()), species_before)
        self.assertEqual(list(OrganizationSpeciesCode.objects.order_by("pk").values()), codes_before)
        self.assertFalse(AuditLog.objects.exists())

    def test_closed_paths_still_require_admin_of_explicit_context(self):
        for user in (self.tech, self.viewer, self.b_admin):
            self.client.force_login(user)
            self.assertEqual(self.post_species(self.a, {"scientific_name": "Chrysaora hysoscella"}).status_code, 403)
            self.assertEqual(self.post_code(self.a, self.cassiopea).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(self.code_endpoint(self.cassiopea)).status_code, 403)

    # WoRMS -------------------------------------------------------------------

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_search_parses_accepted_and_unaccepted_records(self, urlopen):
        urlopen.return_value = FakeResponse([
            worms_record(135306, "Aurelia aurita"),
            worms_record(1_000_001, "Medusa aurita", status="unaccepted", valid_id=135306, valid_name="Aurelia aurita"),
            worms_record(1_000_002, "Aurelia coerulea"),
        ])
        self.client.force_login(self.admin)
        response = self.search(self.a, "  Aurelia   ")
        self.assertEqual(response.status_code, 200, response.content)
        request = urlopen.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://www.marinespecies.org/rest/AphiaRecordsByName/Aurelia?like=true&marine_only=false&offset=1",
        )
        self.assertEqual(urlopen.call_args.kwargs["timeout"], worms.DEFAULT_TIMEOUT_SECONDS)
        data = response.json()
        self.assertEqual(data["query"], "Aurelia")
        accepted, synonym, other = data["results"]
        self.assertEqual(accepted["status"], "accepted")
        # A uses Aurelia historically (code and strain) without a profile.
        self.assertEqual(accepted["local_species"], {
            "institution_species_id": None, "species_id": self.aurelia.pk,
            "scientific_name": "Aurelia aurita", "requires_historical_adoption": True,
        })
        self.assertIsNone(accepted["valid_local_species"])
        self.assertEqual(accepted["class_name"], "Scyphozoa")
        # The unaccepted name is kept as returned; the accepted name is separate context.
        self.assertEqual(synonym["scientific_name"], "Medusa aurita")
        self.assertEqual(synonym["status"], "unaccepted")
        self.assertEqual((synonym["valid_aphia_id"], synonym["valid_name"]), (135306, "Aurelia aurita"))
        self.assertIsNone(synonym["local_species"])
        self.assertEqual(synonym["valid_local_species"]["species_id"], self.aurelia.pk)
        self.assertIsNone(other["local_species"])
        self.assertEqual(Species.objects.count(), 2)
        self.assertFalse(AuditLog.objects.exists())

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_exact_matches_come_first(self, urlopen):
        urlopen.return_value = FakeResponse([
            worms_record(1_000_010, "Aurelia aurita aurita"),
            worms_record(1_000_011, "Aurelia aurita", status="unaccepted", valid_id=135306, valid_name="Aurelia aurita"),
            worms_record(135306, "Aurelia aurita"),
            worms_record(1_000_012, "Aurelia auritoides"),
        ])
        self.client.force_login(self.admin)
        results = self.search(self.a, "aurelia AURITA").json()["results"]
        self.assertEqual([item["aphia_id"] for item in results], [1_000_011, 135306, 1_000_010, 1_000_012])

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_detects_local_species_by_name_without_aphia_id(self, urlopen):
        urlopen.return_value = FakeResponse([worms_record(287205, "Cassiopea andromeda")])
        self.client.force_login(self.admin)
        # A shared reference unused by this institution is not "already recorded".
        self.assertIsNone(self.search(self.a, "Cassiopea").json()["results"][0]["local_species"])
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.cassiopea, scientific_name="Cassiopea andromeda",
        )
        result = self.search(self.a, "Cassiopea").json()["results"][0]
        self.assertEqual(result["local_species"], {
            "institution_species_id": profile.pk, "species_id": self.cassiopea.pk,
            "scientific_name": "Cassiopea andromeda", "requires_historical_adoption": False,
        })
        self.cassiopea.refresh_from_db()
        self.assertIsNone(self.cassiopea.worms_aphia_id)

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_matches_use_local_information_and_never_another_institution(self, urlopen):
        # B's local name and AphiaID never mark a result as recorded for A.
        InstitutionSpecies.objects.create(
            organization=self.b, species=self.cassiopea, scientific_name="Cassiopea xamachana", worms_aphia_id=287210,
        )
        urlopen.return_value = FakeResponse([worms_record(287210, "Cassiopea xamachana")])
        self.client.force_login(self.admin)
        response = self.search(self.a, "Cassiopea")
        self.assertIsNone(response.json()["results"][0]["local_species"])
        # A's own local AphiaID matches even under a different local name.
        profile = InstitutionSpecies.objects.create(
            organization=self.a, species=self.cassiopea, scientific_name="Cassiopea local", worms_aphia_id=287210,
        )
        result = self.search(self.a, "Cassiopea").json()["results"][0]
        self.assertEqual(result["local_species"]["institution_species_id"], profile.pk)
        self.assertEqual(result["local_species"]["scientific_name"], "Cassiopea local")
        # B sees its own profile, not A's.
        self.client.force_login(self.b_admin)
        result = self.search(self.b, "Cassiopea").json()["results"][0]
        self.assertEqual(result["local_species"]["scientific_name"], "Cassiopea xamachana")
        self.assertFalse(AuditLog.objects.exists())

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_no_content_is_an_empty_result(self, urlopen):
        urlopen.return_value = FakeResponse(b"", status=204)
        self.client.force_login(self.admin)
        response = self.search(self.a, "Nonexistent name")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_unavailable_and_invalid_responses_are_reported(self, urlopen):
        self.client.force_login(self.admin)
        cases = [
            (socket.timeout("timed out"), "worms_unavailable"),
            (URLError("offline"), "worms_unavailable"),
            (HTTPError("u", 500, "error", {}, io.BytesIO(b"")), "worms_unavailable"),
            (FakeResponse(b"<html>maintenance</html>"), "worms_invalid_response"),
            (FakeResponse({"AphiaID": 1}), "worms_invalid_response"),
            (FakeResponse([{"AphiaID": "1", "scientificname": "Aurelia"}]), "worms_invalid_response"),
            (FakeResponse([{"AphiaID": True, "scientificname": "Aurelia"}]), "worms_invalid_response"),
            (FakeResponse([{"AphiaID": 5, "scientificname": ["Aurelia"]}]), "worms_invalid_response"),
            (FakeResponse(b"[" + b" " * (worms.MAX_RESPONSE_BYTES + 1) + b"]"), "worms_invalid_response"),
        ]
        for outcome, code in cases:
            with self.subTest(code=code, outcome=repr(outcome)[:40]):
                if isinstance(outcome, Exception):
                    urlopen.side_effect = outcome
                else:
                    urlopen.side_effect = None
                    urlopen.return_value = outcome
                response = self.search(self.a, "Aurelia")
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"code": code})

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_results_are_bounded_and_truncation_is_reported(self, urlopen):
        urlopen.return_value = FakeResponse([
            worms_record(2_000_000 + index, f"Aurelia sp{chr(97 + index % 26)}") for index in range(50)
        ])
        self.client.force_login(self.admin)
        data = self.search(self.a, "Aurelia").json()
        self.assertEqual(len(data["results"]), worms.MAX_RESULTS)
        self.assertIs(data["truncated"], True)
        self.assertEqual(data["limit"], worms.MAX_RESULTS)
        # One upstream page only: no follow-up requests for the hidden records.
        self.assertEqual(urlopen.call_count, 1)

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_results_at_or_below_the_limit_are_not_truncated(self, urlopen):
        self.client.force_login(self.admin)
        for count in (1, worms.MAX_RESULTS):
            with self.subTest(count=count):
                urlopen.return_value = FakeResponse([
                    worms_record(3_000_000 + index, f"Aurelia sp{chr(97 + index)}") for index in range(count)
                ])
                data = self.search(self.a, "Aurelia").json()
                self.assertEqual(len(data["results"]), count)
                self.assertIs(data["truncated"], False)
        urlopen.return_value = FakeResponse(b"", status=204)
        self.assertIs(self.search(self.a, "Nonexistent name").json()["truncated"], False)

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_rank_is_reported_and_genus_is_not_species_level(self, urlopen):
        urlopen.return_value = FakeResponse([
            worms_record(135263, "Aurelia", rank="Genus", taxonRankID=180),
            worms_record(1_000_020, "Aurelia (Aurelissa)", rank="Subgenus", taxonRankID=190),
            worms_record(135306, "Aurelia aurita"),
            worms_record(1_000_021, "Aurelia aurita var. x", rank="Variety", taxonRankID=240),
            # Both rank fields are required; incomplete data stays unverified.
            worms_record(1_000_022, "Aurelia limbata", taxonRankID=None),
            worms_record(1_000_023, "Aureliidae", rank="Family", taxonRankID=None),
            worms_record(1_000_024, "Aurelia odd", rank="", taxonRankID=None),
        ])
        self.client.force_login(self.admin)
        results = self.search(self.a, "Aurelia").json()["results"]
        self.assertEqual(
            [(item["scientific_name"], item["rank"], item["rank_id"], item["is_species_level"]) for item in results],
            [
                ("Aurelia", "Genus", 180, False),
                ("Aurelia (Aurelissa)", "Subgenus", 190, False),
                ("Aurelia aurita", "Species", 220, True),
                ("Aurelia aurita var. x", "Variety", 240, True),
                ("Aurelia limbata", "Species", None, False),
                ("Aureliidae", "Family", None, False),
                ("Aurelia odd", "", None, False),
            ],
        )
        self.assertEqual(Species.objects.count(), 2)
        self.assertFalse(AuditLog.objects.exists())

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_timeout_is_one_bounded_attempt(self, urlopen):
        urlopen.side_effect = socket.timeout("timed out")
        self.client.force_login(self.admin)
        response = self.search(self.a, "Aurelia aurita")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"code": "worms_unavailable"})
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 10)
        # Manual institutional registration while WoRMS is down is covered by
        # test_institution_species_api; this legacy creation path is closed.

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_rejects_invalid_queries_without_calling_out(self, urlopen):
        self.client.force_login(self.admin)
        for query in ("", "Au", "Aurelia%", "../AphiaRecordByAphiaID/1", "Aurelia?x=1", "A" * 121, "1234"):
            with self.subTest(query=query):
                response = self.search(self.a, query)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json(), {"code": "invalid_query"})
        urlopen.assert_not_called()

    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_search_requires_admin_of_active_institution(self, urlopen):
        self.assertEqual(self.search(self.a, "Aurelia").status_code, 403)
        for user in (self.tech, self.viewer, self.b_admin):
            self.client.force_login(user)
            self.assertEqual(self.search(self.a, "Aurelia").status_code, 403)
        urlopen.assert_not_called()

    @override_settings(WORMS_REST_BASE_URL="https://worms.invalid/rest/", WORMS_TIMEOUT_SECONDS=2)
    @patch("apps.taxonomy.worms.urlopen")
    def test_worms_query_is_path_encoded(self, urlopen):
        urlopen.return_value = FakeResponse([])
        worms.search_by_name("Aurelia sp. (Aurelia)")
        request = urlopen.call_args.args[0]
        self.assertTrue(request.full_url.startswith(
            "https://worms.invalid/rest/AphiaRecordsByName/Aurelia%20sp.%20%28Aurelia%29?"
        ))
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 2)


class SpeciesCodeGeneratorTests(TestCase):
    """Deterministic candidates and safety checks of the code generator itself."""

    def setUp(self):
        self.a = Organization.objects.create(name="Generator A")
        self.b = Organization.objects.create(name="Generator B")

    def allocate(self, organization, species):
        with transaction.atomic():
            return species_codes.allocate_species_code(organization=organization, species=species)

    def test_documented_candidates_follow_the_name(self):
        expected = {
            "Aurelia aurita": "AAU", "Aurelia coerulea": "ACO", "Cassiopea andromeda": "CAN",
            "Chrysaora colorata": "CCO", "Chrysaora lactea": "CLA",
        }
        for name, code in expected.items():
            with self.subTest(name=name):
                self.assertEqual(species_codes.candidate_codes(name)[0], code)
        self.assertEqual(species_codes.candidate_codes("Aurelia aurita")[:6], ["AAU", "AAR", "AAI", "AAT", "AAA", "AUA"])
        for codes in map(species_codes.candidate_codes, expected):
            self.assertEqual(len(codes), len(set(codes)))
            self.assertTrue(all(len(code) == 3 and code.isascii() and code.isupper() and code.isalpha() for code in codes))

    def test_unusual_names_never_invent_an_epithet(self):
        same_as_binomial = ("Aurelia cf. aurita", "Aurelia aff. aurita", "Aurelia (Aurelia) aurita",
                            "× Aurelia aurita", "Aurelia aurita × Aurelia coerulea", "Aurelia aurita var. alba")
        for name in same_as_binomial:
            with self.subTest(name=name):
                self.assertEqual(species_codes.parse_name(name), ("AURELIA", "AURITA"))
        for name in ("Aurelia", "Aurelia sp.", "Aurelia sp. 2", "Aurelia spp.", "Aurelia Aurita"):
            with self.subTest(name=name):
                self.assertEqual(species_codes.parse_name(name), ("AURELIA", ""))
                self.assertEqual(species_codes.candidate_codes(name)[0], "AUR")
        self.assertEqual(species_codes.parse_name("Ćassiopea andromeda")[0], "CASSIOPEA")
        for name in ("", "1234", "× ", "(Aurelia)"):
            with self.subTest(name=name), self.assertRaises(species_codes.SpeciesCodeError) as error:
                species_codes.candidate_codes(name)
            self.assertEqual(error.exception.code, "name_not_derivable")

    def test_collision_falls_back_in_documented_order(self):
        taken = Species.objects.create(scientific_name="Aurelia aurantia")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=taken, code="AAU")
        species = Species.objects.create(scientific_name="Aurelia aurita")
        assignment, created = self.allocate(self.a, species)
        self.assertEqual((assignment.code, created), ("AAR", True))

    def test_code_used_elsewhere_for_another_species_is_skipped(self):
        other = Species.objects.create(scientific_name="Aurelia aurantia")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=other, code="AAU")
        species = Species.objects.create(scientific_name="Aurelia aurita")
        self.assertEqual(self.allocate(self.a, species)[0].code, "AAR")

    def test_same_species_may_share_a_code_across_institutions(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        OrganizationSpeciesCode.objects.create(organization=self.b, species=species, code="AAU")
        self.assertEqual(self.allocate(self.a, species)[0].code, "AAU")

    def test_strain_and_box_prefixes_of_another_species_are_unsafe(self):
        other = Species.objects.create(scientific_name="Legacy species")
        Strain.objects.create(species=other, organization=self.b, code="AAU-OLD-1")
        foreign = Strain.objects.create(species=other, organization=self.b, code="legacy")
        Box.objects.create(organization=self.b, strain=foreign, global_code="AAR-OLD-1.001", box_number="001")
        species = Species.objects.create(scientific_name="Aurelia aurita")
        self.assertEqual(self.allocate(self.a, species)[0].code, "AAI")

    def test_historical_strain_prefix_is_reused_first(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        Strain.objects.create(species=species, organization=self.a, code="AUA-FRA-3")
        Strain.objects.create(species=species, organization=self.a, code="not canonical")
        assignment, created = self.allocate(self.a, species)
        self.assertEqual((assignment.code, created), ("AUA", True))

    def test_conflicting_historical_prefixes_need_a_decision(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        Strain.objects.create(species=species, organization=self.a, code="AUA-FRA-3")
        Strain.objects.create(species=species, organization=self.a, code="AAU-JPN-1")
        with self.assertRaises(species_codes.SpeciesCodeError) as error:
            self.allocate(self.a, species)
        self.assertEqual(error.exception.code, "historical_code_conflict")
        self.assertFalse(OrganizationSpeciesCode.objects.exists())

    def test_historical_prefix_owned_by_another_species_is_a_conflict(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        other = Species.objects.create(scientific_name="Other species")
        OrganizationSpeciesCode.objects.create(organization=self.a, species=other, code="AUA")
        Strain.objects.create(species=species, organization=self.a, code="AUA-FRA-3")
        with self.assertRaises(species_codes.SpeciesCodeError) as error:
            self.allocate(self.a, species)
        self.assertEqual(error.exception.code, "historical_code_conflict")

    def test_exhausted_candidates_fail_without_writing(self):
        species = Species.objects.create(scientific_name="Abc de")
        for index, code in enumerate(species_codes.candidate_codes("Abc de")):
            other = Species.objects.create(scientific_name=f"Filler {index}")
            OrganizationSpeciesCode.objects.create(organization=self.a, species=other, code=code)
        with self.assertRaises(species_codes.SpeciesCodeError) as error:
            self.allocate(self.a, species)
        self.assertEqual(error.exception.code, "code_exhausted")
        self.assertFalse(OrganizationSpeciesCode.objects.filter(species=species).exists())

    def test_concurrent_code_loss_retries_next_candidate(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        original = OrganizationSpeciesCode.objects.create
        attempts = []

        def lose_first(**kwargs):
            attempts.append(kwargs["code"])
            if len(attempts) == 1:
                raise IntegrityError("unique_species_code_per_organization_code")
            return original(**kwargs)

        with patch.object(OrganizationSpeciesCode.objects, "create", side_effect=lose_first):
            assignment, created = self.allocate(self.a, species)
        self.assertEqual((attempts, assignment.code, created), (["AAU", "AAR"], "AAR", True))

    def test_allocation_requires_a_transaction(self):
        species = Species.objects.create(scientific_name="Aurelia aurita")
        with patch.object(transaction, "get_connection") as connection:
            connection.return_value.in_atomic_block = False
            with self.assertRaises(RuntimeError):
                species_codes.allocate_species_code(organization=self.a, species=species)
