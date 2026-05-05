import json
from datetime import date

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Prefetch, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from .forms import ReleveBiologiqueForm
from .models import (
    Alerte,
    AppartenanceUtilisateur,
    Boite,
    JournalAction,
    ReleveBiologique,
    ReleveSalinite,
    Structure,
    TemperatureJournaliere,
    ZoneThermique,
)


def _authorized_structures(user):
    if user.is_superuser:
        return Structure.objects.all()

    return Structure.objects.filter(
        appartenanceutilisateur__utilisateur=user,
        appartenanceutilisateur__est_active=True,
        est_active=True,
    ).distinct()


def _authorized_structure_ids(user):
    return list(_authorized_structures(user).values_list("id", flat=True))


def _boite_queryset_for_user(user):
    return Boite.objects.select_related(
        "structure",
        "souche",
        "souche__espece",
        "souche__provenance",
        "provenance",
        "zone_thermique",
    ).prefetch_related(
        Prefetch(
            "releves_biologiques",
            queryset=ReleveBiologique.objects.select_related("utilisateur").order_by("-date_releve", "-date_saisie"),
        ),
        "alertes",
        "tags",
    ).filter(structure_id__in=_authorized_structure_ids(user))


def _serialize_releve(releve):
    return {
        "id": releve.id,
        "date_releve": releve.date_releve.isoformat(),
        "nombre_polypes": releve.nombre_polypes,
        "nombre_ephyres": releve.nombre_ephyres,
        "nombre_strobiles": releve.nombre_strobiles,
        "etat_culture": releve.etat_culture,
        "vigilance": releve.vigilance,
        "commentaire": releve.commentaire,
        "utilisateur": releve.utilisateur.get_username() if releve.utilisateur else None,
        "date_saisie": releve.date_saisie.isoformat(),
    }


def _serialize_boite(boite, include_history=False):
    releves = list(boite.releves_biologiques.all())
    dernier_releve = releves[0] if releves else None
    tags = list(boite.tags.all()) if hasattr(boite, "_prefetched_objects_cache") else boite.tags.all()
    alertes = [alerte for alerte in boite.alertes.all() if alerte.est_active]

    data = {
        "id": boite.id,
        "global_code": boite.global_code,
        "code_local": boite.code_local,
        "numero_boite": boite.numero_boite,
        "statut": boite.statut,
        "structure": {
            "id": boite.structure_id,
            "nom": boite.structure.nom,
            "slug": boite.structure.slug,
        },
        "espece": {
            "id": boite.souche.espece_id,
            "nom_scientifique": boite.souche.espece.nom_scientifique,
            "nom_commun": boite.souche.espece.nom_commun,
            "code_genre_espece": boite.souche.espece.code_genre_espece,
        },
        "souche": {
            "id": boite.souche_id,
            "code": boite.souche.code,
            "numero": boite.souche.numero,
            "code_provenance": boite.souche.code_provenance,
        },
        "zone_thermique": None if not boite.zone_thermique else {
            "id": boite.zone_thermique_id,
            "nom": boite.zone_thermique.nom,
            "type_zone": boite.zone_thermique.type_zone,
            "temperature_consigne": (
                float(boite.zone_thermique.temperature_consigne)
                if boite.zone_thermique.temperature_consigne is not None
                else None
            ),
        },
        "date_creation": boite.date_creation.isoformat() if boite.date_creation else None,
        "date_entree": boite.date_entree.isoformat() if boite.date_entree else None,
        "volume_litres": float(boite.volume_litres) if boite.volume_litres is not None else None,
        "dernier_releve": _serialize_releve(dernier_releve) if dernier_releve else None,
        "alertes_actives": [
            {
                "id": alerte.id,
                "type_alerte": alerte.type_alerte,
                "niveau": alerte.niveau,
                "message": alerte.message,
                "date_creation": alerte.date_creation.isoformat(),
            }
            for alerte in alertes
        ],
        "tags": [
            {
                "id": tag.id,
                "type_tag": tag.type_tag,
                "code": tag.code,
                "url": tag.url,
                "est_actif": tag.est_actif,
            }
            for tag in tags
        ],
    }

    if include_history:
        data["releves_biologiques"] = [_serialize_releve(releve) for releve in releves]
        data["parentes"] = [
            {
                "parent": lien.boite_parent.global_code,
                "enfant": lien.boite_enfant.global_code,
                "type_lien": lien.type_lien,
            }
            for lien in list(boite.liens_parents.select_related("boite_parent", "boite_enfant"))
            + list(boite.liens_enfants.select_related("boite_parent", "boite_enfant"))
        ]

    return data


@login_required
def liste_boites(request):
    boites = _boite_queryset_for_user(request.user).order_by("global_code")

    return render(request, "core/liste_boites.html", {"boites": boites})


@login_required
def detail_boite(request, boite_id):
    boite = get_object_or_404(
        _boite_queryset_for_user(request.user),
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
    boite = get_object_or_404(_boite_queryset_for_user(request.user), id=boite_id)

    if request.method == "POST":
        form = ReleveBiologiqueForm(request.POST)
        if form.is_valid():
            releve = form.save(commit=False)
            releve.boite = boite
            releve.utilisateur = request.user
            releve.save()
            JournalAction.objects.create(
                structure=boite.structure,
                utilisateur=request.user,
                action="saisie",
                objet_type="boite",
                objet_id=boite.global_code,
                description=f"Relevé biologique du {releve.date_releve}",
            )
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


def politique_confidentialite(request):
    return render(request, "core/politique_confidentialite.html")


@require_GET
def api_health(request):
    return JsonResponse(
        {
            "status": "ok",
            "service": "polypbase",
            "timestamp": timezone.now().isoformat(),
        }
    )


@login_required
@require_GET
def api_dashboard(request):
    structure_ids = _authorized_structure_ids(request.user)
    boites = Boite.objects.filter(structure_id__in=structure_ids)
    releves = ReleveBiologique.objects.filter(boite__structure_id__in=structure_ids)
    alertes = Alerte.objects.filter(structure_id__in=structure_ids, date_resolution__isnull=True)

    derniers_releves = releves.order_by("-date_releve", "-date_saisie")[:8]
    total_releves = releves.aggregate(
        polypes=Sum("nombre_polypes"),
        ephyres=Sum("nombre_ephyres"),
        strobiles=Sum("nombre_strobiles"),
    )

    return JsonResponse(
        {
            "structures": list(
                _authorized_structures(request.user).values("id", "nom", "slug", "ville", "pays")
            ),
            "stats": {
                "boites_total": boites.count(),
                "boites_actives": boites.filter(statut="active").count(),
                "especes": boites.values("souche__espece").distinct().count(),
                "zones_thermiques": ZoneThermique.objects.filter(structure_id__in=structure_ids).count(),
                "alertes_actives": alertes.count(),
                "polypes_releves": total_releves["polypes"] or 0,
                "ephyres_relevees": total_releves["ephyres"] or 0,
                "strobiles_releves": total_releves["strobiles"] or 0,
            },
            "dernieres_saisies": [_serialize_releve(releve) for releve in derniers_releves],
            "alertes": [
                {
                    "id": alerte.id,
                    "type_alerte": alerte.type_alerte,
                    "niveau": alerte.niveau,
                    "message": alerte.message,
                    "boite": alerte.boite.global_code if alerte.boite else None,
                    "zone": alerte.zone_thermique.nom if alerte.zone_thermique else None,
                    "date_creation": alerte.date_creation.isoformat(),
                }
                for alerte in alertes.select_related("boite", "zone_thermique")[:12]
            ],
        }
    )


@login_required
@require_GET
def api_boites(request):
    boites = _boite_queryset_for_user(request.user).order_by("global_code")

    statut = request.GET.get("statut")
    if statut:
        boites = boites.filter(statut=statut)

    structure = request.GET.get("structure")
    if structure:
        boites = boites.filter(structure__slug=structure)

    search = request.GET.get("q")
    if search:
        boites = boites.filter(
            Q(global_code__icontains=search)
            | Q(code_local__icontains=search)
            | Q(souche__espece__nom_scientifique__icontains=search)
        )

    return JsonResponse({"results": [_serialize_boite(boite) for boite in boites[:200]]})


@login_required
@require_GET
def api_boite_detail(request, boite_id):
    boite = get_object_or_404(_boite_queryset_for_user(request.user), id=boite_id)
    return JsonResponse(_serialize_boite(boite, include_history=True))


@login_required
@require_http_methods(["GET", "POST"])
def api_releves_boite(request, boite_id):
    boite = get_object_or_404(_boite_queryset_for_user(request.user), id=boite_id)

    if request.method == "GET":
        releves = boite.releves_biologiques.select_related("utilisateur")
        return JsonResponse({"results": [_serialize_releve(releve) for releve in releves]})

    try:
        payload = json.loads(request.body.decode("utf-8")) if request.body else request.POST
        date_releve = date.fromisoformat(payload.get("date_releve") or timezone.localdate().isoformat())
        releve = ReleveBiologique.objects.filter(boite=boite, date_releve=date_releve).order_by("-date_saisie").first()
        created = releve is None
        if created:
            releve = ReleveBiologique(boite=boite, date_releve=date_releve)

        releve.nombre_polypes = int(payload.get("nombre_polypes", 0))
        releve.nombre_ephyres = int(payload.get("nombre_ephyres", 0))
        releve.nombre_strobiles = int(payload.get("nombre_strobiles", 0))
        releve.etat_culture = payload.get("etat_culture", "non_precise")
        releve.vigilance = payload.get("vigilance") in (True, "true", "1", "on", 1)
        releve.commentaire = payload.get("commentaire", "")
        releve.utilisateur = request.user
        releve.save()
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        return JsonResponse({"error": f"Données invalides: {exc}"}, status=400)

    JournalAction.objects.create(
        structure=boite.structure,
        utilisateur=request.user,
        action="saisie" if created else "modification",
        objet_type="boite",
        objet_id=boite.global_code,
        description=f"Relevé biologique du {releve.date_releve}",
    )

    return JsonResponse(_serialize_releve(releve), status=201 if created else 200)


@login_required
@require_GET
def api_zones(request):
    structure_ids = _authorized_structure_ids(request.user)
    zones = ZoneThermique.objects.filter(structure_id__in=structure_ids).select_related("structure").prefetch_related(
        Prefetch(
            "temperatures_journalieres",
            queryset=TemperatureJournaliere.objects.order_by("-date"),
        ),
        Prefetch(
            "releves_salinite",
            queryset=ReleveSalinite.objects.select_related("utilisateur").order_by("-date_releve"),
        ),
    ).annotate(nombre_boites=Count("boite"))

    results = []
    for zone in zones:
        derniere_temperature = next(iter(zone.temperatures_journalieres.all()), None)
        derniere_salinite = next(iter(zone.releves_salinite.all()), None)
        results.append(
            {
                "id": zone.id,
                "nom": zone.nom,
                "type_zone": zone.type_zone,
                "structure": zone.structure.nom,
                "temperature_consigne": (
                    float(zone.temperature_consigne)
                    if zone.temperature_consigne is not None
                    else None
                ),
                "temperature_moyenne": (
                    float(derniere_temperature.temperature_moyenne)
                    if derniere_temperature
                    else None
                ),
                "salinite_psu": float(derniere_salinite.salinite_psu) if derniere_salinite else None,
                "nombre_boites": zone.nombre_boites,
                "est_active": zone.est_active,
            }
        )

    return JsonResponse({"results": results})
