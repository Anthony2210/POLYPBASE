from rest_framework import generics
from rest_framework.exceptions import PermissionDenied

from .models import Organization
from .serializers import OrganizationCreateSerializer


class OrganizationDetailAPIView(generics.RetrieveAPIView):
    """Read organization details. Reserved to superusers."""

    queryset = Organization.objects.all()
    serializer_class = OrganizationCreateSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_superuser:
            raise PermissionDenied("Ce compte ne peut pas modifier une structure.")
