from decimal import Decimal

from django.contrib import messages
from django.contrib.postgres.search import TrigramSimilarity
from django.db import transaction
from django.db.models import Q
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from centres.decorators import centre_actif_requis

from .forms import PatientForm
from .models import (
    Patient,
    PatientCentre,
    SignalementDoublon,
    normaliser_texte,
)


@centre_actif_requis
def recherche_patients(request):
    terme = request.GET.get("q", "").strip()[:100]

    rattachements = PatientCentre.objects.none()

    if terme:
        terme_normalise = normaliser_texte(terme)

        rattachements = (
            PatientCentre.objects
            .filter(
                centre=request.centre_actif,
                archive=False,
                patient__fusionne_dans__isnull=True,
            )
            .select_related("patient", "centre")
            .annotate(
                similarite=TrigramSimilarity(
                    "patient__nom_recherche",
                    terme_normalise,
                )
            )
            .filter(
                Q(
                    patient__nom_recherche__icontains=terme_normalise
                )
                | Q(similarite__gte=0.25)
                | Q(matricule__icontains=terme.upper())
            )
            .order_by(
                "-similarite",
                "patient__nom",
                "patient__prenom",
            )[:50]
        )

    return render(
        request,
        "patients/recherche.html",
        {
            "terme": terme,
            "rattachements": rattachements,
            "centre_actif": request.centre_actif,
        },
    )


def rechercher_doublons(donnees):
    nom_recherche = normaliser_texte(
        f"{donnees['nom']} {donnees.get('prenom', '')}"
    )

    candidats = Patient.objects.filter(fusionne_dans__isnull=True).annotate(
        similarite=TrigramSimilarity(
            "nom_recherche",
            nom_recherche,
        )
    )

    criteres = Q(similarite__gte=0.45)

    date_naissance = donnees.get("date_naissance")

    if date_naissance:
        criteres |= Q(
            nom_recherche=nom_recherche,
            date_naissance=date_naissance,
        )

    entreprise = donnees.get("entreprise")
    matricule_entreprise = donnees.get(
        "matricule_entreprise",
        "",
    ).strip()

    if entreprise and matricule_entreprise:
        criteres |= Q(
            entreprise=entreprise,
            matricule_entreprise__iexact=matricule_entreprise,
        )

    telephone = donnees.get("telephone", "").strip()

    if telephone:
        criteres |= Q(telephone__iexact=telephone)

    return (
        candidats
        .filter(criteres)
        .order_by("-similarite", "nom", "prenom")[:10]
    )


def enregistrer_signalements(patient, candidats):
    for candidat in candidats:
        id_1 = min(patient.pk, candidat.pk)
        id_2 = max(patient.pk, candidat.pk)

        score = Decimal(
            str(round(float(candidat.similarite) * 100, 2))
        )

        SignalementDoublon.objects.get_or_create(
            patient_1_id=id_1,
            patient_2_id=id_2,
            defaults={
                "motif": (
                    "Doublon potentiel détecté lors de la création "
                    "d'une fiche patient."
                ),
                "score_similarite": score,
            },
        )


def rattacher_patient(patient, centre):
    rattachement, cree = PatientCentre.objects.get_or_create(
        patient=patient,
        centre=centre,
    )

    if not cree and rattachement.archive:
        rattachement.archive = False
        rattachement.save(update_fields=["archive", "date_modification"])

    return rattachement


@centre_actif_requis
def creer_patient(request):
    form = PatientForm(request.POST or None)
    doublons = []

    if request.method == "POST" and form.is_valid():

        patient_existant_id = request.POST.get("patient_existant")
        confirmer_nouveau = request.POST.get("confirmer_nouveau")

        # Réutiliser une fiche centrale existante.
        if patient_existant_id:
            candidats = rechercher_doublons(form.cleaned_data)

            patient = candidats.filter(
                pk=patient_existant_id
            ).first()

            if patient is None:
                messages.error(
                    request,
                    "La fiche sélectionnée n'est pas un candidat valide.",
                )
                return redirect("patients:creer")

            with transaction.atomic():
                rattachement = rattacher_patient(
                    patient,
                    request.centre_actif,
                )

            messages.success(
                request,
                f"Fiche patient réutilisée. Matricule : {rattachement.matricule}",
            )

            return redirect(
                f"{reverse('patients:recherche')}?q={rattachement.matricule}"
            )

        # Détecter les doublons potentiels.
        doublons = list(rechercher_doublons(form.cleaned_data))

        if doublons and not confirmer_nouveau:
            return render(
                request,
                "patients/creer.html",
                {
                    "form": form,
                    "doublons": doublons,
                    "centre_actif": request.centre_actif,
                },
            )

        # Créer une nouvelle fiche centrale.
        with transaction.atomic():
            patient = form.save()

            rattachement = PatientCentre.objects.create(
                patient=patient,
                centre=request.centre_actif,
            )

            if doublons:
                enregistrer_signalements(patient, doublons)

        messages.success(
            request,
            f"Patient créé. Matricule : {rattachement.matricule}",
        )

        return redirect(
            f"{reverse('patients:recherche')}?q={rattachement.matricule}"
        )

    return render(
        request,
        "patients/creer.html",
        {
            "form": form,
            "doublons": doublons,
            "centre_actif": request.centre_actif,
        },
    )


def _peut_fusionner(request):
    from comptes.permissions import a_droit

    return a_droit(request.user, "patients.add_fusionpatient", request.centre_actif)


@centre_actif_requis
def doublons(request):
    """Examen des doublons signalés concernant le centre actif."""
    from django.core.exceptions import PermissionDenied, ValidationError

    from .fusion import annuler_fusion, fusionner, marquer_non_doublon
    from .models import FusionPatient

    if not _peut_fusionner(request):
        raise PermissionDenied

    centre = request.centre_actif
    du_centre = Q(patient_1__rattachements__centre=centre) | Q(patient_2__rattachements__centre=centre)
    signalements = SignalementDoublon.objects.filter(du_centre).distinct()

    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "annuler_fusion":
                fusion = get_object_or_404(
                    FusionPatient.objects.filter(
                        Q(patient_source__rattachements__centre=centre)
                        | Q(patient_cible__rattachements__centre=centre)
                    ).distinct(),
                    pk=request.POST.get("fusion"),
                )
                annuler_fusion(fusion, request.user, centre)
                messages.success(request, "Fusion annulée.")
            else:
                signalement = get_object_or_404(signalements, pk=request.POST.get("signalement"))
                if action == "non_doublon":
                    marquer_non_doublon(signalement, request.user)
                    messages.success(request, "Signalement classé : pas un doublon.")
                elif action == "fusionner":
                    garder = request.POST.get("garder")
                    paire = {signalement.patient_1_id: signalement.patient_1,
                             signalement.patient_2_id: signalement.patient_2}
                    cible = paire.get(int(garder or 0))
                    if cible is None:
                        raise ValidationError("Choisissez la fiche à conserver.")
                    source = signalement.patient_2 if cible.pk == signalement.patient_1_id else signalement.patient_1
                    fusionner(source, cible, request.user, request.POST.get("motif", ""), centre)
                    messages.success(request, f"Fiche {source} fusionnée dans {cible}.")
        except ValidationError as erreur:
            messages.error(request, " ".join(erreur.messages))
        return redirect("patients:doublons")

    return render(
        request,
        "patients/doublons.html",
        {
            "signalements": signalements.filter(statut=SignalementDoublon.Statut.A_EXAMINER)
            .select_related("patient_1", "patient_2")[:100],
            "fusions": FusionPatient.objects.filter(
                Q(patient_source__rattachements__centre=centre) | Q(patient_cible__rattachements__centre=centre)
            ).distinct().select_related("patient_source", "patient_cible", "effectuee_par")[:50],
        },
    )
