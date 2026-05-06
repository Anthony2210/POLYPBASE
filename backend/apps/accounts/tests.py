from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from .models import UserPreference


class AccountPreferenceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="tech", password="secret")

    def test_account_settings_defaults_to_french(self):
        self.client.login(username="tech", password="secret")

        response = self.client.get(reverse("account_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Langue de l&#x27;interface")
        preference = UserPreference.objects.get(user=self.user)
        self.assertEqual(preference.interface_language, UserPreference.InterfaceLanguage.FRENCH)

    def test_account_settings_updates_interface_language(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post(
            reverse("account_settings"),
            data={"interface_language": UserPreference.InterfaceLanguage.ENGLISH},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Interface language")
        self.assertEqual(
            self.user.preference.interface_language,
            UserPreference.InterfaceLanguage.ENGLISH,
        )

    def test_legacy_account_preferences_api_is_removed(self):
        self.client.login(username="tech", password="secret")

        response = self.client.post("/accounts/api/preferences/")

        self.assertEqual(response.status_code, 404)
