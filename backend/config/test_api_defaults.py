import os
from importlib.util import module_from_spec, spec_from_file_location
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.test import SimpleTestCase
from rest_framework.generics import GenericAPIView
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from config import settings as app_settings


class ApiSettingsTests(SimpleTestCase):
    def load_settings(self, *, debug, **overrides):
        env = {"DJANGO_DEBUG": debug, "DJANGO_SECRET_KEY": "unused-test-key", **overrides}
        spec = spec_from_file_location("config._isolated_settings", app_settings.__file__)
        settings = module_from_spec(spec)
        with patch.dict(os.environ, env, clear=True), patch("dotenv.load_dotenv"):
            spec.loader.exec_module(settings)
        return settings

    def test_renderer_defaults_depend_on_debug(self):
        json_renderer = "rest_framework.renderers.JSONRenderer"
        browsable_renderer = "rest_framework.renderers.BrowsableAPIRenderer"
        for debug, expected in (("0", [json_renderer]), ("1", [json_renderer, browsable_renderer])):
            with self.subTest(debug=debug):
                settings = self.load_settings(debug=debug)
                self.assertEqual(settings.REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"], expected)

    def test_csrf_defaults_only_trust_local_frontend_origins(self):
        settings = self.load_settings(debug="0")
        self.assertEqual(settings.CSRF_TRUSTED_ORIGINS, ["http://127.0.0.1:5173", "http://localhost:5173"])

    def test_csrf_trusted_origins_allow_an_explicit_tunnel(self):
        origin = "https://qa-example.trycloudflare.com"
        settings = self.load_settings(debug="0", DJANGO_CSRF_TRUSTED_ORIGINS=origin)
        self.assertEqual(settings.CSRF_TRUSTED_ORIGINS, [origin])


class DefaultPaginationTests(SimpleTestCase):
    def paginate(self, **params):
        paginator = GenericAPIView().paginator
        request = Request(APIRequestFactory().get("/api/items/", params))
        results = paginator.paginate_queryset(list(range(250)), request)
        return paginator.get_paginated_response(results).data

    def test_default_page_size_is_unchanged(self):
        self.assertEqual(len(self.paginate()["results"]), 50)

    def test_box_collection_page_size_is_preserved(self):
        self.assertEqual(len(self.paginate(limit=100)["results"]), 100)

    def test_oversized_limit_is_clamped_and_next_link_uses_effective_limit(self):
        page = self.paginate(limit=1000, offset=10)
        self.assertEqual(page["count"], 250)
        self.assertEqual(page["results"], list(range(10, 110)))
        self.assertEqual(parse_qs(urlsplit(page["next"]).query), {"limit": ["100"], "offset": ["110"]})
