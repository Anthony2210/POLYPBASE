from django import forms

from .models import ReleveBiologique, ReleveSalinite


class ReleveBiologiqueForm(forms.ModelForm):
    class Meta:
        model = ReleveBiologique
        fields = [
            "date_releve",
            "nombre_polypes",
            "nombre_ephyres",
            "nombre_strobiles",
            "etat_culture",
            "vigilance",
            "commentaire",
        ]
        widgets = {
            "date_releve": forms.DateInput(attrs={"type": "date"}),
            "commentaire": forms.Textarea(attrs={"rows": 3}),
        }


class ReleveSaliniteForm(forms.ModelForm):
    class Meta:
        model = ReleveSalinite
        fields = [
            "date_releve",
            "salinite_psu",
            "commentaire",
        ]
        widgets = {
            "date_releve": forms.DateInput(attrs={"type": "date"}),
            "commentaire": forms.Textarea(attrs={"rows": 3}),
        }
