from django import forms
from django.utils.translation import gettext_lazy as _

from .models import BiologicalMeasurement, SalinityMeasurement


class BiologicalMeasurementForm(forms.ModelForm):
    class Meta:
        model = BiologicalMeasurement
        fields = [
            "measured_on",
            "polyp_count",
            "ephyrae_count",
            "strobila_count",
            "culture_status",
            "needs_attention",
            "notes",
        ]
        widgets = {
            "measured_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "measured_on": _("Date du relevé"),
            "polyp_count": _("Polypes"),
            "ephyrae_count": _("Éphyrules"),
            "strobila_count": _("Strobiles"),
            "culture_status": _("État de la culture"),
            "needs_attention": _("Vigilance"),
            "notes": _("Commentaire"),
        }


class SalinityMeasurementForm(forms.ModelForm):
    class Meta:
        model = SalinityMeasurement
        fields = [
            "measured_on",
            "salinity_psu",
            "notes",
        ]
        widgets = {
            "measured_on": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "measured_on": _("Date du relevé"),
            "salinity_psu": _("Salinité (PSU)"),
            "notes": _("Commentaire"),
        }
