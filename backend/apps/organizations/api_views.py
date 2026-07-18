from django.db.models.deletion import ProtectedError, RestrictedError
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from apps.accounts.models import OrganizationMembership
from apps.accounts.permissions import user_is_org_admin

from .models import Organization
from .serializers import OrganizationCreateSerializer


class OrganizationCreateAPIView(generics.CreateAPIView):
    """Create a partner organization from the administration interface."""

    serializer_class = OrganizationCreateSerializer

    def perform_create(self, serializer):
        if not user_is_org_admin(self.request.user):
            raise PermissionDenied("Ce compte ne peut pas creer une structure.")

        organization = serializer.save()
        if not self.request.user.is_superuser:
            OrganizationMembership.objects.get_or_create(
                user=self.request.user,
                organization=organization,
                defaults={
                    "role": OrganizationMembership.Role.ADMIN,
                    "is_active": True,
                },
            )


class OrganizationDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Edit or delete an organization. Reserved to superusers."""

    queryset = Organization.objects.all()
    serializer_class = OrganizationCreateSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_superuser:
            raise PermissionDenied("Ce compte ne peut pas modifier une structure.")

    def destroy(self, request, *args, **kwargs):
        organization = self.get_object()
        if self._has_related_lab_data(organization):
            return Response(
                {
                    "detail": (
                        "Cette structure contient deja des donnees liees. "
                        "Suppression refusee pour preserver l'historique."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            return super().destroy(request, *args, **kwargs)
        except (ProtectedError, RestrictedError):
            return Response(
                {"detail": "Cette structure est encore utilisee et ne peut pas etre supprimee."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    def _has_related_lab_data(self, organization):
        related_managers = [
            organization.memberships,
            organization.boxes,
            organization.thermal_zones,
            organization.probes,
            organization.alerts,
            organization.audit_logs,
            organization.excel_imports,
            organization.data_exports,
            organization.outgoing_sharing_agreements,
            organization.incoming_sharing_agreements,
            organization.outgoing_box_transfers,
            organization.incoming_box_transfers,
        ]
        return any(manager.exists() for manager in related_managers)
