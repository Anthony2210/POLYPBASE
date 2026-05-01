from django.shortcuts import get_object_or_404, redirect, render
from django.contrib.auth.decorators import login_required

from .forms import ReleveBiologiqueForm
from .models import Boite


@login_required
def liste_boites(request):
    boites = Boite.objects.select_related(
        "structure",
        "souche",
        "souche__espece",
        "zone_thermique",
    ).order_by("global_code")

    return render(request, "core/liste_boites.html", {"boites": boites})


@login_required
def detail_boite(request, boite_id):
    boite = get_object_or_404(
        Boite.objects.select_related(
            "structure",
            "souche",
            "souche__espece",
            "zone_thermique",
        ),
        id=boite_id,
    )

    releves = boite.releves_biologiques.all()

    return render(
        request,
        "core/detail_boite.html",
        {
            "boite": boite,
            "releves": releves,
        },
    )


@login_required
def ajouter_releve(request, boite_id):
    boite = get_object_or_404(Boite, id=boite_id)

    if request.method == "POST":
        form = ReleveBiologiqueForm(request.POST)
        if form.is_valid():
            releve = form.save(commit=False)
            releve.boite = boite
            releve.utilisateur = request.user
            releve.save()
            return redirect("detail_boite", boite_id=boite.id)
    else:
        form = ReleveBiologiqueForm()

    return render(
        request,
        "core/ajouter_releve.html",
        {
            "boite": boite,
            "form": form,
        },
    )