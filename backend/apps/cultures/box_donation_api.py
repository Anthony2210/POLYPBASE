"""HTTP surface of whole-Box donation proposals. No acceptance endpoint exists.

Every endpoint requires the explicit `X-Organization-Id` context and an active
Admin membership of that institution. Lists are filtered by institution in the
query itself. The destination sees a summary, never the source history,
source accounts or local Box id. Responses are `no-store`.
"""

from django.utils.cache import patch_vary_headers
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import get_required_active_organization_from_request

from . import box_donation
from .box_donation import DonationError


class DonationAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def handle_exception(self, exc):
        if isinstance(exc, DonationError):
            return Response({"detail": exc.detail, "code": exc.code, **exc.extra}, status=exc.status)
        return super().handle_exception(exc)

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response["Cache-Control"] = "no-store"
        patch_vary_headers(response, ["Cookie"])
        return response


class BoxDonationPreflightAPIView(DonationAPIView):
    def post(self, request):
        organization = get_required_active_organization_from_request(request)
        return Response(box_donation.preflight_proposal(
            actor=request.user, organization=organization, data=request.data,
        ))


class BoxDonationOutgoingAPIView(DonationAPIView):
    def get(self, request):
        organization = get_required_active_organization_from_request(request)
        proposals = box_donation.outgoing_proposals(actor=request.user, organization=organization)
        return Response({"results": [box_donation.source_data(item) for item in proposals]})

    def post(self, request):
        organization = get_required_active_organization_from_request(request)
        proposal, created = box_donation.create_proposal(
            actor=request.user, organization=organization, data=request.data,
        )
        # Serialize from committed state, within the same authorized scope.
        proposal, _side = box_donation.proposal_for_institution(
            actor=request.user, organization=organization, proposal_id=proposal.proposal_id,
        )
        return Response(
            box_donation.source_data(proposal),
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class BoxDonationIncomingAPIView(DonationAPIView):
    def get(self, request):
        organization = get_required_active_organization_from_request(request)
        proposals = box_donation.incoming_proposals(actor=request.user, organization=organization)
        return Response({"results": [box_donation.destination_data(item) for item in proposals]})


class BoxDonationDetailAPIView(DonationAPIView):
    def get(self, request, proposal_id):
        organization = get_required_active_organization_from_request(request)
        proposal, side = box_donation.proposal_for_institution(
            actor=request.user, organization=organization, proposal_id=proposal_id,
        )
        if side == "source":
            return Response(box_donation.source_data(proposal, include_current=True))
        return Response(box_donation.destination_data(proposal))


class BoxDonationCancelAPIView(DonationAPIView):
    def post(self, request, proposal_id):
        organization = get_required_active_organization_from_request(request)
        box_donation.cancel_proposal(
            actor=request.user, organization=organization, proposal_id=proposal_id, data=request.data,
        )
        proposal, _side = box_donation.proposal_for_institution(
            actor=request.user, organization=organization, proposal_id=proposal_id,
        )
        return Response(box_donation.source_data(proposal))
