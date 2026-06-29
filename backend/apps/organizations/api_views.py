from rest_framework import generics
from rest_framework.exceptions import PermissionDenied

from .serializers import OrganizationCreateSerializer


class OrganizationCreateAPIView(generics.CreateAPIView):
    """Create a new organization (tenant). Reserved to superusers."""

    serializer_class = OrganizationCreateSerializer

    def perform_create(self, serializer):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Seul un super-administrateur peut créer une structure.")
        serializer.save()
