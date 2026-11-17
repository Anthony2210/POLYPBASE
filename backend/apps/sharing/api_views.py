"""HTTP surface of external read-only Box sharing.

Two disjoint families:

- `admin/box-shares/...`: management by Admins of the active source
  institution (explicit `X-Organization-Id` required).
- `shared-boxes/...`: the recipient side. It never reads an organization
  context: every identifier comes from the stored grant of the signed-in
  account, so headers, query parameters and Box ids cannot widen the scope.

Every response is `no-store`: it may carry another institution's data.
"""

from django.db.models import Prefetch
from django.utils.cache import patch_vary_headers
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.identity import serialize_user_identity
from apps.accounts.permissions import get_required_active_organization_from_request
from apps.measurements.scientific_snapshot import (
    IDENTITY_SELECTORS,
    OBSERVATION_PROVENANCE_SELECTORS,
    OBSERVATION_TIME,
    OBSERVATION_VALUE_SELECTORS,
    SCHEMA,
    SOURCE_SELECTORS,
    VERSION,
)

from . import services
from .models import BoxReaderShare, BoxReaderShareCapture
from .services import MANDATORY_SELECTORS, SharingError

MAX_LIST_LIMIT = 200


class SharingAPIView(APIView):
    """Stable `{detail, code}` errors and uncacheable responses."""

    def handle_exception(self, exc):
        if isinstance(exc, SharingError):
            return Response({"detail": exc.detail, "code": exc.code, **exc.extra}, status=exc.status)
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        patch_vary_headers(response, ["Cookie"])
        return response


def _validated(serializer_class, data):
    serializer = serializer_class(data=data)
    if not serializer.is_valid():
        raise SharingError("invalid_request", 400, "The request is not valid.", errors=serializer.errors)
    return serializer.validated_data


# Source Admin -----------------------------------------------------------------


def _selectors_field(**kwargs):
    # Shape only; the scientific contract validates the selection itself.
    return serializers.ListField(
        child=serializers.CharField(max_length=80), allow_empty=True, max_length=50, **kwargs,
    )


class _CreateSerializer(serializers.Serializer):
    box_id = serializers.IntegerField(min_value=1)
    recipient_email = serializers.CharField(max_length=254)
    mode = serializers.ChoiceField(choices=BoxReaderShare.Mode.choices)
    selectors = _selectors_field()
    invitation_expires_at = serializers.DateTimeField()
    access_expires_at = serializers.DateTimeField()


class _PreviewSerializer(serializers.Serializer):
    box_id = serializers.IntegerField(min_value=1)
    selectors = _selectors_field()


class _UpdateSerializer(serializers.Serializer):
    expected_policy_revision = serializers.IntegerField(min_value=1)
    selectors = _selectors_field(required=False)
    access_expires_at = serializers.DateTimeField(required=False)
    confirm_expansion = serializers.BooleanField(required=False, default=False)
    confirm_recapture = serializers.BooleanField(required=False, default=False)


class _ReissueSerializer(serializers.Serializer):
    invitation_expires_at = serializers.DateTimeField()


def _admin_shares(organization_id):
    return (
        BoxReaderShare.objects.filter(organization_id=organization_id)
        .select_related("box", "created_by")
        .prefetch_related(Prefetch(
            "captures",
            # Metadata only: the captured content is never loaded for lists.
            queryset=BoxReaderShareCapture.objects.only(
                "share_id", "policy_revision", "captured_at", "content_sha256",
            ),
        ))
    )


def _admin_share_data(share, now):
    capture = next(
        (item for item in share.captures.all() if item.policy_revision == share.policy_revision), None,
    )
    return {
        "id": share.pk,
        "box": {"id": share.box_id, "global_code": share.box.global_code},
        "recipient_email": share.recipient_email,
        "mode": share.mode,
        "state": services.share_state(share, now),
        "schema_version": share.schema_version,
        "selectors": share.selectors,
        "policy_revision": share.policy_revision,
        "invitation": {
            "generation": share.invitation_generation,
            "issued_at": share.invitation_issued_at,
            "expires_at": share.invitation_expires_at,
            "accepted_at": share.accepted_at,
            "delivery": {"status": share.delivery_status, "updated_at": share.delivery_updated_at},
        },
        "access_expires_at": share.access_expires_at,
        "revoked_at": share.revoked_at,
        "created_at": share.created_at,
        "created_by": serialize_user_identity(share.created_by),
        "capture": None if capture is None else {
            "captured_at": capture.captured_at,
            "content_sha256": capture.content_sha256,
        },
    }


def _admin_response(organization_id, share_id, *, http_status=status.HTTP_200_OK):
    share = _admin_shares(organization_id).get(pk=share_id)
    now = services.sharing_now()
    return Response({"share": _admin_share_data(share, now), "server_time": now}, status=http_status)


class BoxShareOptionsAPIView(SharingAPIView):
    """What a source Admin may choose, and what is mandatory."""

    def get(self, request):
        organization = get_required_active_organization_from_request(request)
        services.require_source_admin(request.user, organization)
        groups = (
            ("source", SOURCE_SELECTORS),
            ("identity", IDENTITY_SELECTORS),
            ("observation", [OBSERVATION_TIME, *OBSERVATION_VALUE_SELECTORS, *OBSERVATION_PROVENANCE_SELECTORS]),
        )
        policy = services.lifetime_policy()
        return Response({
            "frozen_capture_available": services.frozen_capture_available(),
            "schema": SCHEMA,
            "schema_version": VERSION,
            "modes": list(BoxReaderShare.Mode.values),
            "selectors": [
                {
                    "name": name,
                    "group": group,
                    "mandatory": name in MANDATORY_SELECTORS,
                    "requires": (
                        [OBSERVATION_TIME] if group == "observation" and name != OBSERVATION_TIME else []
                    ),
                }
                for group, names in groups for name in names
            ],
            "mandatory_selectors": sorted(MANDATORY_SELECTORS),
            "lifetime_policy": {
                "configured": policy is not None,
                "invitation_max_lifetime_seconds": None if policy is None else int(policy[0].total_seconds()),
                "access_max_lifetime_seconds": None if policy is None else int(policy[1].total_seconds()),
            },
        })


class BoxSharePreviewAPIView(SharingAPIView):
    """Exactly what a recipient would currently receive for a selection."""

    def post(self, request):
        organization = get_required_active_organization_from_request(request)
        data = _validated(_PreviewSerializer, request.data)
        snapshot = services.preview_share(
            actor=request.user, organization=organization,
            box_id=data["box_id"], selectors=data["selectors"],
        )
        return Response({"snapshot": snapshot})


class BoxShareListCreateAPIView(SharingAPIView):
    def get(self, request):
        organization = get_required_active_organization_from_request(request)
        source = services.require_source_admin(request.user, organization)
        shares = _admin_shares(source.pk)
        box_id = request.query_params.get("box_id")
        if box_id is not None:
            if not box_id.isascii() or not box_id.isdigit():
                raise SharingError("invalid_request", 400, "The request is not valid.")
            shares = shares.filter(box_id=int(box_id))
        try:
            limit = min(max(int(request.query_params.get("limit", MAX_LIST_LIMIT)), 1), MAX_LIST_LIMIT)
            offset = max(int(request.query_params.get("offset", 0)), 0)
        except ValueError as error:
            raise SharingError("invalid_request", 400, "The request is not valid.") from error
        now = services.sharing_now()
        page = list(shares.order_by("-created_at", "-pk")[offset:offset + limit + 1])
        return Response({
            "shares": [_admin_share_data(share, now) for share in page[:limit]],
            "has_more": len(page) > limit,
            "server_time": now,
        })

    def post(self, request):
        organization = get_required_active_organization_from_request(request)
        data = _validated(_CreateSerializer, request.data)
        share = services.create_share(actor=request.user, organization=organization, **data)
        return _admin_response(share.organization_id, share.pk, http_status=status.HTTP_201_CREATED)


class BoxShareDetailAPIView(SharingAPIView):
    def get(self, request, pk):
        organization = get_required_active_organization_from_request(request)
        source = services.require_source_admin(request.user, organization)
        share = _admin_shares(source.pk).filter(pk=pk).first()
        if share is None:
            raise SharingError("share_not_found", 404, "This share does not exist.")
        now = services.sharing_now()
        return Response({"share": _admin_share_data(share, now), "server_time": now})

    def patch(self, request, pk):
        organization = get_required_active_organization_from_request(request)
        data = _validated(_UpdateSerializer, request.data)
        share = services.update_share(
            actor=request.user, organization=organization, share_id=pk, **data,
        )
        return _admin_response(share.organization_id, share.pk)


class BoxShareRevokeAPIView(SharingAPIView):
    def post(self, request, pk):
        organization = get_required_active_organization_from_request(request)
        share = services.revoke_share(actor=request.user, organization=organization, share_id=pk)
        return _admin_response(share.organization_id, share.pk)


class BoxShareInvitationReissueAPIView(SharingAPIView):
    def post(self, request, pk):
        organization = get_required_active_organization_from_request(request)
        data = _validated(_ReissueSerializer, request.data)
        share = services.reissue_invitation(
            actor=request.user, organization=organization, share_id=pk, **data,
        )
        return _admin_response(share.organization_id, share.pk)


# Recipient --------------------------------------------------------------------


class _TokenSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=200, trim_whitespace=False)


class SharedBoxListAPIView(SharingAPIView):
    """Grants of the signed-in account, whatever its memberships."""

    def get(self, request):
        return Response({
            "shares": services.list_shared_boxes(user=request.user),
            "server_time": services.sharing_now(),
        })


class SharedBoxInvitationInspectAPIView(SharingAPIView):
    """Describe an invitation. POST keeps the secret out of URLs; nothing is consumed."""

    def post(self, request):
        data = _validated(_TokenSerializer, request.data)
        return Response({"invitation": services.inspect_invitation(user=request.user, token=data["token"])})


class SharedBoxInvitationAcceptAPIView(SharingAPIView):
    def post(self, request):
        data = _validated(_TokenSerializer, request.data)
        return Response({"share": services.accept_invitation(user=request.user, token=data["token"])})


class SharedBoxDetailAPIView(SharingAPIView):
    """The only endpoint serving shared scientific data. GET only."""

    def get(self, request, public_id):
        return Response(services.read_shared_box(user=request.user, public_id=public_id))
