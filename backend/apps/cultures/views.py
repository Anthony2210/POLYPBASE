"""Server routes for stable QR entry links, protected SVGs and the legal page."""

from urllib.parse import urlparse

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.vary import vary_on_headers
from rest_framework.exceptions import PermissionDenied

from apps.accounts.permissions import get_required_active_organization_from_request

from . import qr
from .models import Box


def _qr_scan_url(request, box):
    """Build a QR target for the app address currently used in the browser."""
    public_base_url = request.GET.get("public_base_url", "").strip()
    parsed_url = urlparse(public_base_url)

    if (
        parsed_url.scheme in {"http", "https"}
        and parsed_url.netloc
        and not parsed_url.path.rstrip("/")
        and not parsed_url.params
        and not parsed_url.query
        and not parsed_url.fragment
        and not parsed_url.username
        and not parsed_url.password
    ):
        return f"{public_base_url.rstrip('/')}/bac/{box.id}/"

    return qr.box_scan_url(box)


@never_cache
@login_required
def scan_box(request, box_id):
    """Hand off the numeric ID; React supplies context to the authorized scan API."""
    return redirect(f"/?scan_box={box_id}")


@never_cache
@vary_on_headers("X-Organization-Id", "Cookie")
@login_required
def box_qr(request, box_id):
    """Return a QR SVG only in the explicitly selected organization."""
    try:
        organization = get_required_active_organization_from_request(request)
    except PermissionDenied as error:
        # Plain Django views cannot render DRF permission exceptions.
        raise Http404("Box not found.") from error
    box = get_object_or_404(Box.objects.filter(organization=organization), id=box_id)
    svg = qr.render_qr_svg(_qr_scan_url(request, box))
    response = HttpResponse(svg, content_type="image/svg+xml")
    response["Content-Disposition"] = f'inline; filename="bac-{box.id}.svg"'
    return response


def privacy_policy(request):
    return render(request, "core/politique_confidentialite.html")
