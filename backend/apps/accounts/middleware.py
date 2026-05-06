from django.conf import settings
from django.utils import translation

from .models import UserPreference


class UserInterfaceLanguageMiddleware:
    """Activate the interface language selected in the user's account settings."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        language = self._get_language(request)
        translation.activate(language)
        request.LANGUAGE_CODE = language

        response = self.get_response(request)
        response.set_cookie(settings.LANGUAGE_COOKIE_NAME, language, samesite="Lax")
        translation.deactivate()
        return response

    def _get_language(self, request):
        if request.user.is_authenticated:
            preference, _created = UserPreference.objects.get_or_create(user=request.user)
            request.session["interface_language"] = preference.interface_language
            return preference.interface_language

        session_language = request.session.get("interface_language")
        if session_language in dict(settings.LANGUAGES):
            return session_language

        return settings.LANGUAGE_CODE.split("-")[0]
