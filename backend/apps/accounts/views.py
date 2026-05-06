from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import translation
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods

from .forms import UserPreferenceForm
from .models import UserPreference


@login_required
@require_http_methods(["GET", "POST"])
def account_settings(request):
    preference, _created = UserPreference.objects.get_or_create(user=request.user)

    if request.method == "POST":
        form = UserPreferenceForm(
            request.POST,
            instance=preference,
        )
        if form.is_valid():
            preference = form.save()
            request.session["interface_language"] = preference.interface_language
            translation.activate(preference.interface_language)
            request.LANGUAGE_CODE = preference.interface_language
            messages.success(request, _("Langue de l'interface mise à jour."))
            return redirect("account_settings")
    else:
        form = UserPreferenceForm(instance=preference)

    return render(
        request,
        "accounts/account_settings.html",
        {
            "form": form,
            "preference": preference,
        },
    )
