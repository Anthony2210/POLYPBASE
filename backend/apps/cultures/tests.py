import json
from datetime import date

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import (
    AppartenanceUtilisateur,
    Boite,
    Espece,
    JournalAction,
    ReleveBiologique,
    Role,
    Souche,
    Structure,
    ZoneThermique,
)


class PolypbaseApiTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tech", password="secret")

        self.role = Role.objects.create(nom="technicien")
        self.structure = Structure.objects.create(nom="Aquarium de Paris", slug="aquariumdeparis")
        self.other_structure = Structure.objects.create(nom="Aquarium de Tokyo", slug="aquariumdetokyo")
        AppartenanceUtilisateur.objects.create(
            utilisateur=self.user,
            structure=self.structure,
            role=self.role,
        )

        self.espece = Espece.objects.create(
            nom_scientifique="Aurelia aurita",
            code_genre_espece="AAU",
        )
        self.souche = Souche.objects.create(espece=self.espece, code="1-ATL", numero=1, code_provenance="ATL")
        self.zone = ZoneThermique.objects.create(
            structure=self.structure,
            nom="Armoire-15",
            type_zone="armoire",
            temperature_consigne=15,
        )
        self.other_zone = ZoneThermique.objects.create(
            structure=self.other_structure,
            nom="Armoire-10",
            type_zone="armoire",
            temperature_consigne=10,
        )
        self.boite = Boite.objects.create(
            structure=self.structure,
            global_code="AAU-1.001-ATL",
            numero_boite="001",
            souche=self.souche,
            zone_thermique=self.zone,
        )
        Boite.objects.create(
            structure=self.other_structure,
            global_code="AAU-1.001-TKY",
            numero_boite="001",
            souche=self.souche,
            zone_thermique=self.other_zone,
        )

    def test_api_boites_is_scoped_to_user_structure(self):
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("api_boites"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["global_code"], "AAU-1.001-ATL")

    def test_api_releve_post_records_strobiles_and_journal(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("api_releves_boite", args=[self.boite.id]),
            data=json.dumps(
                {
                    "date_releve": "2026-05-04",
                    "nombre_polypes": 42,
                    "nombre_ephyres": 7,
                    "nombre_strobiles": 2,
                    "etat_culture": "bon",
                    "vigilance": True,
                    "commentaire": "Saisie tablette",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        releve = ReleveBiologique.objects.get(boite=self.boite, date_releve=date(2026, 5, 4))
        self.assertEqual(releve.nombre_strobiles, 2)
        self.assertTrue(releve.vigilance)
        self.assertEqual(JournalAction.objects.filter(action="saisie", objet_id=self.boite.global_code).count(), 1)

    def test_health_endpoint_is_public(self):
        response = self.client.get(reverse("api_health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

# Create your tests here.
