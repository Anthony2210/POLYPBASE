"""HTTP contract for DNA validation intents."""

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import get_required_active_organization_from_request

from .dna_validation import mutate_dna_validation
from .serializers import BoxDnaValidationSerializer


class BoxDnaValidationAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def mutate(self, request, box_id, operation):
        validation = mutate_dna_validation(
            user=request.user,
            organization=get_required_active_organization_from_request(request),
            box_id=box_id, operation=operation, data=lambda: request.data,
        )
        if operation == "revoke":
            return Response(status=204)
        return Response(
            BoxDnaValidationSerializer(validation).data,
            status=201 if operation == "add" else 200,
        )

    def post(self, request, box_id):
        return self.mutate(request, box_id, "add")

    def patch(self, request, box_id):
        return self.mutate(request, box_id, "update")


class BoxDnaValidationRevokeAPIView(BoxDnaValidationAPIView):
    http_method_names = ["post", "options"]

    def post(self, request, box_id):
        return self.mutate(request, box_id, "revoke")
