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

        try:
            response = self.get_response(request)
            response.set_cookie(
                settings.LANGUAGE_COOKIE_NAME,
                self._response_language(request, language),
                samesite="Lax",
            )
            return response
        finally:
            translation.deactivate()

    @staticmethod
    def _response_language(request, language):
        """Language active at the end of this request.

        Login and preference updates activate the new language inside the view (on
        a DRF request wrapper, so request.LANGUAGE_CODE is not visible here). The
        active language is context-local, never shared with another request.
        """
        if not request.user.is_authenticated:
            # Logout flushes the session without activating the anonymous default.
            session_language = request.session.get("interface_language")
            return (session_language if session_language in dict(settings.LANGUAGES)
                    else settings.LANGUAGE_CODE.split("-")[0])
        final_language = translation.get_language()
        return final_language if final_language in dict(settings.LANGUAGES) else language

    def _get_language(self, request):
        if request.user.is_authenticated and request.path in {
            "/api/measurement-import/inspect/", "/api/measurement-import/preview/",
        }:
            # Diagnostic uploads must not create preferences or persist sessions.
            return (
                UserPreference.objects.filter(user=request.user)
                .values_list("interface_language", flat=True).first()
                or settings.LANGUAGE_CODE.split("-")[0]
            )
        if request.user.is_authenticated:
            preference, _created = UserPreference.objects.get_or_create(user=request.user)
            request.session["interface_language"] = preference.interface_language
            return preference.interface_language

        session_language = request.session.get("interface_language")
        if session_language in dict(settings.LANGUAGES):
            return session_language

        return settings.LANGUAGE_CODE.split("-")[0]
