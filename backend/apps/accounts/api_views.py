from django.utils import translation
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import get_authorized_organizations

from .models import UserPreference
from .serializers import (
    UserPreferenceSerializer,
    UserProfileSerializer,
    available_interface_languages,
)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class UserProfileAPIView(APIView):
    """Return and update the current user's account preferences."""

    def get(self, request):
        preference = self._get_preference(request.user)
        return Response(self._profile_data(request.user, preference))

    def patch(self, request):
        preference = self._get_preference(request.user)
        serializer = UserPreferenceSerializer(
            preference,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        preference = serializer.save()

        request.session["interface_language"] = preference.interface_language
        translation.activate(preference.interface_language)
        request.LANGUAGE_CODE = preference.interface_language

        return Response(self._profile_data(request.user, preference))

    def _get_preference(self, user):
        preference, _created = UserPreference.objects.get_or_create(user=user)
        return preference

    def _profile_data(self, user, preference):
        organizations = get_authorized_organizations(user).order_by("name")
        serializer = UserProfileSerializer(
            {
                "id": user.id,
                "username": user.get_username(),
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "interface_language": preference.interface_language,
                "organizations": organizations,
                "available_languages": available_interface_languages(),
            }
        )
        return serializer.data
