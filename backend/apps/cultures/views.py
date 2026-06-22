from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import get_authorized_organization_ids
from apps.audit.models import AuditLog
from apps.measurements.forms import BiologicalMeasurementForm
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


@login_required
def box_list(request):
    boxes = _box_queryset_for_user(request.user).order_by("global_code")

    return render(request, "core/liste_boites.html", {"boxes": boxes})


@login_required
def box_detail(request, box_id):
    box = get_object_or_404(
        _box_queryset_for_user(request.user),
        id=box_id,
    )

    measurements = box.biological_measurements.all()

    return render(
        request,
        "core/detail_boite.html",
        {
            "box": box,
            "measurements": measurements,
            "scan_url": qr.box_scan_url(box),
        },
    )


@login_required
def add_measurement(request, box_id):
    box = get_object_or_404(_box_queryset_for_user(request.user), id=box_id)

    if request.method == "POST":
        form = BiologicalMeasurementForm(request.POST)
        if form.is_valid():
            measurement = form.save(commit=False)
            measurement.box = box
            measurement.user = request.user
            measurement.save()
            AuditLog.objects.create(
                organization=box.organization,
                user=request.user,
                action=AuditLog.Action.ENTRY,
                object_type="box",
                object_id=box.global_code,
                description=f"Biological measurement for {measurement.measured_on}",
            )
            return redirect("detail_boite", box_id=box.id)
    else:
        form = BiologicalMeasurementForm()

    return render(
        request,
        "core/ajouter_releve.html",
        {
            "box": box,
            "form": form,
        },
    )


@login_required
def scan_box(request, box_id):
    """Stable target encoded in a box QR code.

    Scanning ``/bac/<id>/`` lands here, records the access, and redirects to the
    box detail page. The access is visible on every device using the same account.
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
    return redirect("detail_boite", box_id=box.id)


@login_required
def box_qr(request, box_id):
    """Return the box QR code as an inline SVG image."""
    box = get_object_or_404(_box_queryset_for_user(request.user), id=box_id)
    svg = qr.render_qr_svg(qr.box_scan_url(box))
    response = HttpResponse(svg, content_type="image/svg+xml")
    response["Content-Disposition"] = f'inline; filename="bac-{box.id}.svg"'
    return response


def privacy_policy(request):
    return render(request, "core/politique_confidentialite.html")
