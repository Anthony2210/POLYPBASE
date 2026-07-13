"""Server-rendered views kept alongside the React app.

Only three remain, and each has a reason to exist outside the SPA:

* ``scan_box`` — the stable ``/bac/<id>/`` target printed on QR labels. It is a
  server route so that a scan is recorded even before the app boots, then it
  hands over to the React box sheet.
* ``box_qr`` — renders the QR code itself as an SVG.
* ``privacy_policy`` — a standalone legal page.

The old HTML pages (box list, box detail, measurement form) were removed: they
duplicated the React app, and a scanned QR code used to land on them.
"""

from urllib.parse import quote

from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from urllib.parse import urlparse

from apps.accounts.permissions import get_authorized_organization_ids
from apps.audit.models import AuditLog
from apps.measurements.models import BiologicalMeasurement

from . import qr
from .models import Box


def _box_queryset_for_user(user):
    return Box.objects.select_related(
        "organization",
        "strain",
        "strain__species",
        "strain__origin",
        "origin",
        "thermal_zone",
    ).prefetch_related(
        Prefetch(
            "biological_measurements",
            queryset=BiologicalMeasurement.objects.select_related("user").order_by("-measured_on", "-created_at"),
        ),
        "alerts",
        "tags",
    ).filter(organization_id__in=get_authorized_organization_ids(user))


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


def box_app_url(box):
    """URL of the box sheet in the React app."""
    return f"/boxes/{quote(box.global_code, safe='')}"


@login_required
def scan_box(request, box_id):
    """Stable target encoded in a box QR code.

    Scanning ``/bac/<id>/`` lands here, records the scan, then redirects to the
    box sheet **in the React app**. Keeping the QR target decoupled from the app
    route means printed labels keep working even if that route changes.
    """
    box = get_object_or_404(_box_queryset_for_user(request.user), id=box_id)
    AuditLog.objects.create(
        organization=box.organization,
        user=request.user,
        action=AuditLog.Action.SCAN,
        object_type="box",
        object_id=box.global_code,
        description=f"QR scan of {box.global_code}",
        metadata={"box_id": box.id, "source": "qr_link"},
    )
    return redirect(box_app_url(box))


@login_required
def box_qr(request, box_id):
    """Return the box QR code as an inline SVG image."""
    box = get_object_or_404(_box_queryset_for_user(request.user), id=box_id)
    svg = qr.render_qr_svg(_qr_scan_url(request, box))
    response = HttpResponse(svg, content_type="image/svg+xml")
    response["Content-Disposition"] = f'inline; filename="bac-{box.id}.svg"'
    return response


def privacy_policy(request):
    return render(request, "core/politique_confidentialite.html")
