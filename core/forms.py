from django import forms

from .models import ReleveBiologique


class ReleveBiologiqueForm(forms.ModelForm):
    class Meta:
        model = ReleveBiologique
        fields = [
            "date_releve",
            "nombre_polypes",
            "nombre_ephyres",
            "etat_culture",
            "commentaire",
        ]
        widgets = {
            "date_releve": forms.DateInput(attrs={"type": "date"}),
            "commentaire": forms.Textarea(attrs={"rows": 3}),
        }