"""Real Django gettext smoke tests; SimpleTestCase forbids all database access.

Test modules are deliberately excluded from the checker's production inventory.
Calls use the translation module and table-driven msgids, not source markers.
"""
from datetime import date

from django.test import SimpleTestCase
from django.utils import translation
from rest_framework.exceptions import ValidationError

from apps.cultures.serializers import BoxTemperatureHistoryQuerySerializer


IDENTITIES = {
    "Administrateur": "Administrator",
    "Technicien laboratoire": "Lab technician",
    "Étuve": "Incubator",
    "Autre": "Other",
    "Bon": "Good",
    "Moyen": "Medium",
    "Mauvais": "Bad",
    "Mort": "Dead",
    "Saisie manuelle": "Manual entry",
    "Nom :": "Name:",
    "Email :": "Email:",
}
PRIVACY_IDENTITIES = (
    "POLYPBASE collecte uniquement les informations nécessaires au suivi des cultures de polypes, à la gestion des comptes utilisateurs et à la traçabilité scientifique des actions réalisées.",
    "Identité de compte : nom complet, adresse email, rôle et organisation.",
    "Données métier : boîtes, espèces, souches, relevés biologiques, salinités, températures et mouvements.",
    "Traçabilité : date, utilisateur et description des créations, modifications, imports, exports et scans.",
    "Ces données servent à exécuter le service de suivi de collection, sécuriser l'accès aux informations, produire des exports scientifiques ou réglementaires et conserver l'historique nécessaire à l'exploitation du laboratoire.",
    "Chaque utilisateur peut demander l'accès, la rectification ou la suppression de ses données de compte auprès de l'administrateur de son organisation. Les données scientifiques déjà associées à un historique de laboratoire peuvent être anonymisées lorsque leur suppression compromettrait la traçabilité de la collection.",
    "L'application limite l'accès par compte, rôle et organisation. En production, elle doit être servie en HTTPS, avec des sauvegardes régulières et une base de données non exposée directement aux terminaux du laboratoire.",
)
DATE_MESSAGES = (
    "Use YYYY-MM-DD format.",
    "End must be on or after start.",
    "The date range must not exceed 366 days, inclusive.",
)
PERMISSIONS = {
    "This account cannot create subculture events.": {
        "fr": "Ce compte ne peut pas effectuer de repiquage. Merci de contacter un administrateur.",
        "en": "This account cannot create transplant events. Please contact your administrator.",
    },
    "The parent box is outside the active organization.": {
        "fr": "La boîte parent n'appartient pas à la structure active. Merci de contacter un administrateur.",
        "en": "The parent box is outside the active organization. Please contact your administrator.",
    },
}


class BackendI18nTests(SimpleTestCase):
    def test_french_identity_entries_and_english_translations(self):
        for language in ("fr", "en"):
            with translation.override(language):
                for msgid, english in IDENTITIES.items():
                    with self.subTest(language=language, msgid=msgid):
                        self.assertEqual(translation.gettext(msgid), msgid if language == "fr" else english)
                for msgid in PRIVACY_IDENTITIES:
                    with self.subTest(language=language, msgid=msgid):
                        value = translation.gettext(msgid)
                        if language == "fr":
                            self.assertEqual(value, msgid)
                        else:
                            self.assertNotEqual(value, msgid)
                            self.assertTrue(value.strip())

    def test_approved_translations(self):
        cases = {
            "À vérifier": {"en": "Pending"},
            "Duplicate measurement evidence is read-only.": {"en": "Duplicate record evidence is read-only."},
            "Only an active box can be subcultured.": {
                "fr": "Seule une boîte active peut être repiquée.",
                "en": "Only an active box can be subcultured.",
            },
        }
        for msgid, values in cases.items():
            for language, expected in values.items():
                with self.subTest(language=language, msgid=msgid), translation.override(language):
                    self.assertEqual(translation.gettext(msgid), expected)

    def test_only_explicit_permission_messages_have_administrator_instruction(self):
        for language in ("fr", "en"):
            invitation = ("Merci de contacter un administrateur." if language == "fr"
                          else "Please contact your administrator.")
            with translation.override(language):
                for msgid, values in PERMISSIONS.items():
                    with self.subTest(language=language, msgid=msgid):
                        self.assertEqual(translation.gettext(msgid), values[language])
                        self.assertEqual(translation.gettext(msgid).count(invitation), 1)
                for msgid in (*DATE_MESSAGES, "Only an active box can be subcultured.",
                              "The parent polyp count is unknown.", "A nonnegative integer is required."):
                    with self.subTest(language=language, validation=msgid):
                        self.assertNotIn(invitation, translation.gettext(msgid))

    def test_date_identity_entries_through_gettext_and_serializer(self):
        for language in ("fr", "en"):
            with translation.override(language):
                for msgid in DATE_MESSAGES:
                    self.assertEqual(translation.gettext(msgid), msgid)
                # Direct validate isolates the project's translated checks from
                # DRF's own DateField messages; no model queries are performed.
                cases = (
                    ({"start": "2026-1-01", "end": "2026-01-02"},
                     {"start": date(2026, 1, 1), "end": date(2026, 1, 2)}, "start", DATE_MESSAGES[0]),
                    ({"start": "2026-01-02", "end": "2026-01-01"},
                     {"start": date(2026, 1, 2), "end": date(2026, 1, 1)}, "end", DATE_MESSAGES[1]),
                    ({"start": "2026-01-01", "end": "2027-01-02"},
                     {"start": date(2026, 1, 1), "end": date(2027, 1, 2)}, "end", DATE_MESSAGES[2]),
                )
                for data, attrs, field, expected in cases:
                    with self.subTest(language=language, expected=expected):
                        serializer = BoxTemperatureHistoryQuerySerializer(data=data)
                        with self.assertRaises(ValidationError) as caught:
                            serializer.validate(attrs)
                        self.assertEqual(str(caught.exception.detail[field]), expected)
                serializer = BoxTemperatureHistoryQuerySerializer(
                    data={"start": "2026-01-01", "end": "2026-01-01"})
                self.assertTrue(serializer.is_valid(), serializer.errors)
                self.assertEqual(serializer.validated_data["start"], date(2026, 1, 1))

    def test_zero_is_a_valid_value_not_an_absence(self):
        from rest_framework import serializers

        for language in ("fr", "en"):
            with translation.override(language):
                self.assertEqual(translation.gettext("0"), "0")
                self.assertEqual(serializers.IntegerField(min_value=0).run_validation(0), 0)
