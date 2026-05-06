from django import forms
from django.utils.translation import gettext_lazy as _

from .models import UserPreference


class UserPreferenceForm(forms.ModelForm):
    class Meta:
        model = UserPreference
        fields = ["interface_language"]
        labels = {
            "interface_language": _("Langue de l'interface"),
        }
