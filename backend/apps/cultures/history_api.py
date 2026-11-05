from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import get_required_active_organization_from_request

from .box_history import box_history
from .models import Box


class BoxHistoryAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, box_id):
        organization = get_required_active_organization_from_request(request)
        box = get_object_or_404(Box.objects.select_related("organization"), pk=box_id,
                                organization=organization, merged_into__isnull=True)
        return Response(box_history(box=box, user=request.user))
