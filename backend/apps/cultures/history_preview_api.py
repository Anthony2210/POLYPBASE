"""HTTP integration for request-local, read-only historical simulations."""

from io import BytesIO

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied, RequestDataTooBig
from django.utils.cache import patch_vary_headers
from rest_framework.exceptions import ParseError, PermissionDenied, ValidationError
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import OrganizationMembership
from apps.accounts.permissions import get_required_active_organization_from_request

from .effective_history_sources import HistorySourceNotFound
from .history_preview_serializers import HistoryPreviewRequestSerializer, HistoryPreviewResponseSerializer
from .history_preview_service import preview_box_history
from .history_preview_validation import PreviewInputError


class _BoundedJSONParser(JSONParser):
    def parse(self, stream, media_type=None, parser_context=None):
        # Session CSRF checks may parse DRF's POST before the handler runs.
        # Read through Django's bounded body property at the parser boundary,
        # rather than letting JSONParser bypass DATA_UPLOAD_MAX_MEMORY_SIZE.
        body = parser_context["request"]._request.body
        return super().parse(BytesIO(body), media_type, parser_context)


class BoxHistoryPreviewAPIView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [_BoundedJSONParser]
    http_method_names = ["get", "post", "head", "options"]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        self.organization = get_required_active_organization_from_request(request)
        # Check before draft validation and source loading. Session CSRF may
        # already have parsed the bounded JSON body during authentication.
        # The service rechecks actor and membership inside its source snapshot.
        if not OrganizationMembership.objects.filter(
            user_id=request.user.pk, user__is_active=True,
            organization=self.organization, is_active=True,
            role=OrganizationMembership.Role.ADMIN,
        ).exists():
            raise PermissionDenied("An active institution administrator is required.")

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        patch_vary_headers(response, ("Cookie", "X-Organization-Id"))
        return response

    def handle_exception(self, exc):
        if isinstance(exc, RequestDataTooBig):
            return Response({"code": "request_too_large"}, status=413)
        if isinstance(exc, ParseError):
            return Response({"code": "invalid_json"}, status=400)
        return super().handle_exception(exc)

    def get(self, request, box_id):
        if (set(request.query_params) - {"start", "end"}
                or any(len(request.query_params.getlist(key)) != 1 for key in request.query_params)):
            return Response({"code": "invalid_preview_input"}, status=400)
        return self._preview(request, box_id, request.query_params.dict())

    def post(self, request, box_id):
        if request.query_params:
            return Response({"code": "invalid_preview_input"}, status=400)
        return self._preview(request, box_id, request.data)

    def _preview(self, request, box_id, payload):
        serializer = HistoryPreviewRequestSerializer(data=payload)
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as error:
            return Response({"code": "invalid_preview_input", "errors": error.detail}, status=400)
        try:
            result = preview_box_history(
                actor=request.user, organization_id=self.organization.pk,
                box_id=box_id, **serializer.validated_data,
            )
        except DjangoPermissionDenied:
            raise PermissionDenied("An active institution administrator is required.") from None
        except HistorySourceNotFound:
            return Response({"code": "box_not_found"}, status=404)
        except PreviewInputError as error:
            # Details can contain caller references. Codes and indexes suffice
            # to explain rejected operations without turning errors into oracles.
            data = {"code": error.code, "rejected_operations": [
                {"index": item.index, "code": item.code.value} for item in error.rejections
            ]}
            if error.operation_index is not None:
                data["operation_index"] = error.operation_index
            return Response(data, status=400)
        return Response(HistoryPreviewResponseSerializer(result).data)
