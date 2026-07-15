from django.db.models.deletion import ProtectedError, RestrictedError
from rest_framework import generics, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .serializers import OrganizationCreateSerializer
from .models import Organization


class OrganizationCreateAPIView(generics.CreateAPIView):
    """Create a new organization (tenant). Reserved to superusers."""

    serializer_class = OrganizationCreateSerializer

    def perform_create(self, serializer):
        if not self.request.user.is_superuser:
            raise PermissionDenied("Seul un super-administrateur peut créer une structure.")
        serializer.save()


class OrganizationDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    """Edit or delete an organization. Reserved to superusers."""

    queryset = Organization.objects.all()
    serializer_class = OrganizationCreateSerializer

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not request.user.is_superuser:
            raise PermissionDenied("Seul un super-administrateur peut modifier une structure.")

    def destroy(self, request, *args, **kwargs):
        organization = self.get_object()
        if self._has_related_lab_data(organization):
            return Response(
                {
                    "detail": (
                        "Cette structure contient déjà des données liées. "
                        "Suppression refusée pour préserver l'historique."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            return super().destroy(request, *args, **kwargs)
        except (ProtectedError, RestrictedError):
            return Response(
                {"detail": "Cette structure est encore utilisée et ne peut pas être supprimée."},
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
