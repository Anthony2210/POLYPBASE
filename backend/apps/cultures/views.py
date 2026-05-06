from django.contrib.auth.decorators import login_required
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.permissions import get_authorized_organization_ids
from apps.audit.models import AuditLog
from apps.measurements.forms import BiologicalMeasurementForm
from apps.measurements.models import BiologicalMeasurement

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


def privacy_policy(request):
    return render(request, "core/politique_confidentialite.html")
