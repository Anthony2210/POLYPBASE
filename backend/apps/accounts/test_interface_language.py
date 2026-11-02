"""Japanese interface language: preference contract, gettext fallback and isolation.

Japanese uses Django's standard activation. Its compiled catalog is the effective
catalog built by scripts/compile_django_messages.py (validated Japanese, else the
approved English msgstr, else the source msgid); nothing is patched at runtime.
"""
import asyncio
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import translation
from django.utils.translation import trans_real

from apps.taxonomy.serializers import available_content_languages

from .middleware import UserInterfaceLanguageMiddleware
from .models import UserPreference

ROOT = Path(__file__).resolve().parents[3]
LOCALE = ROOT / "backend/locale"


sys.path.insert(0, str(ROOT / "scripts"))
import check_i18n  # noqa: E402
import compile_django_messages as compiler  # noqa: E402

JA = {k: v for k, v in compiler.parse_po_file(LOCALE / "ja/LC_MESSAGES/django.po").items() if k}
EN = {k: v for k, v in compiler.parse_po_file(LOCALE / "en/LC_MESSAGES/django.po").items() if k}
FR = {k: v for k, v in compiler.parse_po_file(LOCALE / "fr/LC_MESSAGES/django.po").items() if k}
SOURCES = tuple(check_i18n.discover_sources(ROOT))
# Generic framework messages come from Django's own catalogs, never from ours.
FRAMEWORK_MSGID = "This field is required."


def expected(language, msgid):
    """Displayed text, derived from the PO sources independently of the MO."""
    if language == "ja":
        return JA.get(msgid) or EN.get(msgid) or msgid
    return {"fr": FR, "en": EN}[language].get(msgid) or msgid


def reset_translation_caches():
    """Rebuild Django's process-wide catalogs exactly as a settings change does."""
    with override_settings(LOCALE_PATHS=[*settings.LOCALE_PATHS]):
        pass


class JapaneseGettextTests(SimpleTestCase):
    def tearDown(self):
        translation.deactivate()

    def gettext_in(self, language, msgid):
        with translation.override(language):
            return translation.gettext(msgid)

    def test_every_active_msgid_shows_validated_japanese_else_english_else_source(self):
        self.assertEqual(len(JA), 81)
        self.assertTrue(set(JA) <= set(SOURCES))
        for msgid in SOURCES:
            for language in ("ja", "en", "fr"):
                with self.subTest(language=language, msgid=msgid):
                    self.assertEqual(self.gettext_in(language, msgid), expected(language, msgid))

    def test_missing_japanese_uses_english_not_french(self):
        cases = {
            "Mortalité": "Mortality",
            "This account cannot create subculture events.":
                "This account cannot create transplant events. Please contact your administrator.",
            "The parent box is outside the active organization.":
                "The parent box is outside the active institution. Please contact your administrator.",
            "À vérifier": "Pending",
        }
        for msgid, english in cases.items():
            with self.subTest(msgid=msgid):
                self.assertNotIn(msgid, JA)
                self.assertNotEqual(FR.get(msgid, msgid), english)
                self.assertEqual(self.gettext_in("ja", msgid), english)
                self.assertEqual(self.gettext_in("en", msgid), english)

    def test_missing_japanese_without_approved_english_keeps_the_source_text(self):
        for msgid in ("Provenance non établie", "Emplacement à la qualification"):
            with self.subTest(msgid=msgid):
                self.assertNotIn(msgid, JA)
                self.assertNotIn(msgid, EN)
                self.assertEqual(self.gettext_in("ja", msgid), msgid)
                self.assertEqual(self.gettext_in("en", msgid), msgid)

    def test_framework_messages_keep_their_own_translations(self):
        values = {language: self.gettext_in(language, FRAMEWORK_MSGID) for language in ("fr", "en", "ja")}
        self.assertNotIn(FRAMEWORK_MSGID, JA)
        self.assertEqual(values["en"], FRAMEWORK_MSGID)
        self.assertNotEqual(values["fr"], FRAMEWORK_MSGID)
        self.assertNotEqual(values["ja"], FRAMEWORK_MSGID)
        self.assertRegex(values["ja"], "[぀-ヿ一-鿿]")

    def test_no_runtime_patch_of_django_catalogs(self):
        reset_translation_caches()
        with translation.override("ja"):
            translation.gettext("Mortalité")
        # Django's own wiring is untouched: JA keeps its standard default fallback.
        self.assertIs(trans_real.translation("ja")._fallback, trans_real.translation(settings.LANGUAGE_CODE))
        self.assertFalse(Path(__file__).with_name("language.py").exists())

    def test_first_activation_after_cache_rebuild_and_repeated_switching(self):
        msgids = ("Mortalité", "Administrateur", "À vérifier", "Provenance non établie", FRAMEWORK_MSGID)
        baseline = {(language, msgid): self.gettext_in(language, msgid)
                    for language in ("fr", "en", "ja") for msgid in msgids}
        for _round in range(3):
            reset_translation_caches()
            # Japanese is the first catalog loaded after the rebuild.
            for language in ("ja", "en", "fr", "ja", "fr", "en", "ja"):
                for msgid in msgids:
                    with self.subTest(round=_round, language=language, msgid=msgid):
                        self.assertEqual(self.gettext_in(language, msgid), baseline[(language, msgid)])

    def test_concurrent_threads_with_different_languages_are_isolated(self):
        msgids = ("Mortalité", "Administrateur", "À vérifier", "Anglais")
        languages = ["ja", "en", "fr"] * 8
        barrier = threading.Barrier(len(languages))

        def work(language):
            barrier.wait()  # every thread starts on cold catalogs together
            results = []
            for _iteration in range(50):
                translation.activate(language)
                results.append(tuple(translation.gettext(msgid) for msgid in msgids))
                translation.deactivate()
            return language, results

        reset_translation_caches()
        with ThreadPoolExecutor(max_workers=len(languages)) as pool:
            outcomes = list(pool.map(work, languages))
        for language, results in outcomes:
            wanted = tuple(expected(language, msgid) for msgid in msgids)
            self.assertEqual(set(results), {wanted}, language)

    async def test_concurrent_async_tasks_keep_languages_isolated_on_cold_caches(self):
        msgids = ("Mortalité", "Administrateur", "À vérifier", "Provenance non établie")
        reset_translation_caches()

        async def work(language):
            with translation.override(language):
                for _iteration in range(20):
                    await asyncio.sleep(0)
                    self.assertEqual(tuple(translation.gettext(msgid) for msgid in msgids),
                                     tuple(expected(language, msgid) for msgid in msgids))

        await asyncio.gather(*(work(language) for language in ["ja", "fr", "en"] * 8))

    def test_middleware_deactivates_language_when_the_view_raises(self):
        def view(request):
            raise RuntimeError("view failed")

        request = RequestFactory().get("/")
        request.user = type("Anonymous", (), {"is_authenticated": False})()
        request.session = {"interface_language": "ja"}
        with self.assertRaisesMessage(RuntimeError, "view failed"):
            UserInterfaceLanguageMiddleware(view)(request)
        self.assertEqual(translation.get_language(), settings.LANGUAGE_CODE)

    def test_concurrent_middleware_requests_keep_their_own_language(self):
        def view(request):
            return HttpResponse(translation.gettext("Mortalité") + "|" + translation.gettext("Anglais"))

        middleware = UserInterfaceLanguageMiddleware(view)
        anonymous = type("Anonymous", (), {"is_authenticated": False})()

        def request_in(language):
            request = RequestFactory().get("/")
            request.user = anonymous
            request.session = {"interface_language": language}
            response = middleware(request)
            return language, response.content.decode(), response.cookies[settings.LANGUAGE_COOKIE_NAME].value

        reset_translation_caches()
        with ThreadPoolExecutor(max_workers=12) as pool:
            outcomes = list(pool.map(request_in, ["ja", "en", "fr"] * 30))
        for language, content, cookie in outcomes:
            self.assertEqual(content, f"{expected(language, 'Mortalité')}|{expected(language, 'Anglais')}")
            self.assertEqual(cookie, language)
        self.assertEqual({content for _language, content, _cookie in outcomes},
                         {"Mortality|英語", "Mortality|English", "Mortalité|Anglais"})


class EffectiveCatalogTests(SimpleTestCase):
    def test_effective_catalog_provenance(self):
        validated = {"": "header", "Bonjour": "こんにちは"}
        english = {"": "en header", "Bonjour": "Hello", "Au revoir": "Goodbye"}
        catalog = compiler.effective_catalog(validated, english, ("Bonjour", "Au revoir", "Merci"))
        self.assertEqual(catalog, {"": "header", "Au revoir": "Goodbye", "Bonjour": "こんにちは", "Merci": "Merci"})

    def test_compiled_japanese_mo_is_the_effective_catalog_of_the_validated_po(self):
        effective = compiler.effective_catalog(
            compiler.parse_po_file(LOCALE / "ja/LC_MESSAGES/django.po"),
            compiler.parse_po_file(LOCALE / "en/LC_MESSAGES/django.po"), SOURCES)
        self.assertEqual((LOCALE / "ja/LC_MESSAGES/django.mo").read_bytes(), compiler.compile_messages(effective))
        # The validated source never records English or source text as Japanese.
        for msgid, value in JA.items():
            with self.subTest(msgid=msgid):
                self.assertNotEqual(value, msgid)
                self.assertNotEqual(value, EN.get(msgid))


class ContentLanguagesTests(SimpleTestCase):
    def test_scientific_content_languages_are_unchanged(self):
        self.assertEqual(settings.CONTENT_LANGUAGES, [("fr", "Français"), ("en", "English"), ("ja", "日本語")])
        self.assertEqual(
            [(item["code"], item["required"]) for item in available_content_languages()],
            [("fr", True), ("en", False), ("ja", False)],
        )

    def test_interface_languages(self):
        self.assertEqual([code for code, _label in settings.LANGUAGES], ["fr", "en", "ja"])
        self.assertEqual(settings.LANGUAGE_CODE, "fr-fr")


class JapanesePreferenceApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="tech", email="tech@example.org", password="secret")
        self.client.force_login(self.user)

    def patch_language(self, language):
        return self.client.patch(reverse("api_profile"), data={"interface_language": language},
                                 content_type="application/json")

    def test_japanese_preference_is_saved_and_persisted_in_session(self):
        response = self.patch_language("ja")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["interface_language"], "ja")
        self.assertEqual(UserPreference.objects.get(user=self.user).interface_language, "ja")
        self.assertEqual(self.client.session["interface_language"], "ja")
        profile = self.client.get(reverse("api_profile"))
        self.assertEqual(profile.json()["interface_language"], "ja")
        self.assertEqual(profile.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ja")

    def test_fr_ja_en_switch_updates_response_text_and_cookie_immediately(self):
        labels = {"fr": "Anglais", "ja": "英語", "en": "English"}
        for language in ("fr", "ja", "en"):
            with self.subTest(language=language):
                response = self.patch_language(language)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, language)
                self.assertEqual(response.json()["available_languages"][1]["label"], labels[language])
                following = self.client.get(reverse("api_profile"))
                self.assertEqual(following.cookies[settings.LANGUAGE_COOKIE_NAME].value, language)

    def test_rejected_language_keeps_the_previous_cookie_and_preference(self):
        self.assertEqual(self.patch_language("ja").status_code, 200)
        for value in ("de", "ja-JP", "", "JA"):
            with self.subTest(value=value):
                response = self.patch_language(value)
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ja")
                self.assertEqual(UserPreference.objects.get(user=self.user).interface_language, "ja")

    def test_available_languages_keep_french_and_english_and_add_japanese(self):
        for language, english_label in (("fr", "Anglais"), ("en", "English"), ("ja", "英語")):
            with self.subTest(language=language):
                self.assertEqual(self.patch_language(language).status_code, 200)
                self.assertEqual(self.client.get(reverse("api_profile")).json()["available_languages"], [
                    {"code": "fr", "label": "Français"},
                    {"code": "en", "label": english_label},
                    {"code": "ja", "label": "日本語"},
                ])

    def test_login_cookie_is_the_logged_in_users_language(self):
        UserPreference.objects.create(user=get_user_model().objects.create_user(
            username="ja", email="ja@example.org", password="secret"), interface_language="ja")
        self.client.logout()
        anonymous = self.client.get(reverse("api_session_login"))
        self.assertEqual(anonymous.cookies[settings.LANGUAGE_COOKIE_NAME].value, "fr")
        response = self.client.post(reverse("api_session_login"),
                                    data={"email": "ja@example.org", "password": "secret"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"interface_language": "ja"})
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "ja")
        self.assertEqual(self.client.session["interface_language"], "ja")

    def test_account_settings_form_updates_the_cookie_on_the_redirect(self):
        for language in ("fr", "ja", "en"):
            with self.subTest(language=language):
                response = self.client.post(reverse("account_settings"), {"interface_language": language})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, language)
                self.assertEqual(self.client.session["interface_language"], language)
                self.assertEqual(UserPreference.objects.get(user=self.user).interface_language, language)

    def test_logout_cookie_matches_the_new_anonymous_session(self):
        self.assertEqual(self.patch_language("ja").status_code, 200)
        response = self.client.post(reverse("api_session_logout"))
        self.assertEqual(response.status_code, 204)
        self.assertNotIn("interface_language", self.client.session)
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "fr")
        following = self.client.get(reverse("api_session_login"))
        self.assertEqual(following.cookies[settings.LANGUAGE_COOKIE_NAME].value, "fr")

    def test_one_users_language_does_not_reach_another_user(self):
        other = get_user_model().objects.create_user(username="other", email="o@example.org", password="secret")
        other_client = self.client_class()
        other_client.force_login(other)
        self.assertEqual(self.patch_language("ja").status_code, 200)
        response = other_client.get(reverse("api_profile"))
        self.assertEqual(response.json()["interface_language"], "fr")
        self.assertEqual(response.json()["available_languages"][1]["label"], "Anglais")
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME].value, "fr")
