"""
Page « Réglages » (décision du 27/11/2026 : « facilite toutes les créations »).

Tout ce qui demandait l'administration se fait ici en un formulaire court :
- motifs « Autre » de la borne (ajouter, retirer) ;
- imprimantes de tickets (ajouter, tester, retirer) ;
- places du dentiste (un tableau jours × matin / après-midi) ;
- le centre lui-même et un nouveau centre complet (plateforme seulement).

Accès : administrateur de la plateforme, ou administrateur du centre actif.
"""

import datetime
import re

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import transaction
from django.db.models import Max, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core.requetes import entier
from core.timeutils import maintenant_centre

from .access import SESSION_CENTRE_KEY, est_administrateur_central
from .decorators import centre_actif_requis
from .models import Centre, ImprimanteTicket

JOURS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def peut_regler(utilisateur, centre):
    from comptes.permissions import a_droit

    if centre is None:
        return False
    return est_administrateur_central(utilisateur) or a_droit(utilisateur, "centres.change_borne", centre)


def _exiger(request):
    if not peut_regler(request.user, request.centre_actif):
        raise PermissionDenied


# ---------------------------------------------------------------------------
# Places du dentiste (parcours soumis à quota)
# ---------------------------------------------------------------------------

def grille_quotas(parcours, jour):
    """[{jour, nom, MATIN: places|None, APRES_MIDI: places|None}] en vigueur à cette date."""
    from parcours.models import QuotaParcours

    en_vigueur = QuotaParcours.objects.filter(parcours=parcours, date_debut__lte=jour).filter(
        Q(date_fin__isnull=True) | Q(date_fin__gte=jour)
    )
    places = {(q.jour_semaine, q.periode): q.nombre_places for q in en_vigueur}
    return [
        {"jour": n, "nom": JOURS[n], "MATIN": places.get((n, "MATIN")), "APRES_MIDI": places.get((n, "APRES_MIDI"))}
        for n in range(7)
    ]


@transaction.atomic
def enregistrer_grille(parcours, valeurs, auteur):
    """
    valeurs : {(jour, période): places ou None (fermé)}. Seules les cases
    changées sont touchées ; un changement vaut à partir d'aujourd'hui (les
    jours passés gardent leurs places, pour les statistiques).
    """
    from parcours.models import QuotaParcours
    from parcours.quotas import enregistrer_quota, supprimer_quota

    aujourd_hui = maintenant_centre(parcours.centre).date()
    hier = aujourd_hui - datetime.timedelta(days=1)
    changements = 0
    for (jour, periode), places in valeurs.items():
        actuel = QuotaParcours.objects.filter(
            parcours=parcours, jour_semaine=jour, periode=periode, date_debut__lte=aujourd_hui,
        ).filter(Q(date_fin__isnull=True) | Q(date_fin__gte=aujourd_hui)).first()
        if (actuel.nombre_places if actuel else None) == places:
            continue
        changements += 1
        # Réglages futurs déjà prévus pour cette case : remplacés par le nouveau.
        for futur in QuotaParcours.objects.filter(parcours=parcours, jour_semaine=jour, periode=periode,
                                                   date_debut__gt=aujourd_hui):
            supprimer_quota(futur, auteur)
        if actuel is not None:
            if actuel.date_debut == aujourd_hui:
                if places is None:
                    supprimer_quota(actuel, auteur)
                else:
                    actuel.nombre_places = places
                    actuel.date_fin = None
                    enregistrer_quota(actuel, auteur)
                continue
            actuel.date_fin = hier
            enregistrer_quota(actuel, auteur)
        if places is not None:
            enregistrer_quota(QuotaParcours(
                parcours=parcours, jour_semaine=jour, periode=periode, nombre_places=places,
                date_debut=aujourd_hui,
            ), auteur)
    return changements


# ---------------------------------------------------------------------------
# Nouveau centre complet
# ---------------------------------------------------------------------------

def creer_centre(nom, code, medecins=2):
    """Centre + services, parcours, places du dentiste et motifs « Autre » habituels."""
    from io import StringIO

    nom = " ".join((nom or "").split())[:200]
    code = (code or "").strip().upper()
    if not nom:
        raise ValidationError("Donnez le nom du centre.")
    if not re.fullmatch(r"[A-Z0-9]{2,10}", code):
        raise ValidationError("Le code du centre : 2 à 10 lettres ou chiffres, sans espace (ex. TAM).")
    if Centre.objects.filter(code=code).exists():
        raise ValidationError(f"Le code « {code} » est déjà utilisé par un autre centre.")
    with transaction.atomic():
        centre = Centre(nom=nom, code=code)
        centre.full_clean()
        centre.save()
        call_command("initialiser_parcours", code, medecins=max(2, medecins), stdout=StringIO())
    return centre


# ---------------------------------------------------------------------------
# Vues
# ---------------------------------------------------------------------------

@centre_actif_requis
def reglages(request):
    from circuit.models import MotifDemande
    from parcours.models import Parcours

    _exiger(request)
    centre = request.centre_actif
    aujourd_hui = maintenant_centre(centre).date()
    quotas = [
        {"parcours": p, "grille": grille_quotas(p, aujourd_hui)}
        for p in Parcours.objects.filter(centre=centre, actif=True, soumis_quota=True).order_by("ordre_affichage")
    ]
    return render(request, "centres/reglages.html", {
        "page": "reglages",
        "plateforme": est_administrateur_central(request.user),
        "motifs": MotifDemande.objects.filter(centre=centre).order_by("-actif", "ordre", "libelle"),
        "imprimantes": ImprimanteTicket.objects.filter(centre=centre).prefetch_related("bornes")
        .order_by("-actif", "libelle"),
        "quotas": quotas,
        "periodes": [("MATIN", "Matin"), ("APRES_MIDI", "Après-midi")],
    })


@require_POST
@centre_actif_requis
def motif(request):
    from circuit.models import MotifDemande

    _exiger(request)
    centre = request.centre_actif
    if request.POST.get("motif"):
        objet = get_object_or_404(MotifDemande.objects.filter(centre=centre), pk=entier(request.POST.get("motif")))
        objet.actif = not objet.actif
        objet.save(update_fields=["actif"])
        messages.success(request, f"Motif « {objet.libelle} » {'remis' if objet.actif else 'retiré'} à la borne.")
        return redirect("centres:reglages")
    libelle = " ".join(request.POST.get("libelle", "").split())[:150]
    if not libelle:
        messages.error(request, "Écrivez le motif (ex. Retrait de résultats).")
    elif MotifDemande.objects.filter(centre=centre, libelle__iexact=libelle).exists():
        messages.error(request, f"Le motif « {libelle} » existe déjà.")
    else:
        ordre = (MotifDemande.objects.filter(centre=centre).aggregate(m=Max("ordre"))["m"] or 0) + 1
        MotifDemande.objects.create(centre=centre, libelle=libelle, ordre=ordre)
        messages.success(request, f"Motif « {libelle} » ajouté : il est proposé à la borne (choix « Autre »).")
    return redirect("centres:reglages")


@require_POST
@centre_actif_requis
def imprimante(request):
    """Ajouter, tester, retirer ou remettre une imprimante de tickets."""
    from circuit.impression import ErreurImpression, imprimer_test

    _exiger(request)
    centre = request.centre_actif
    action = request.POST.get("action", "ajouter")
    if action != "ajouter":
        objet = get_object_or_404(ImprimanteTicket.objects.filter(centre=centre),
                                  pk=entier(request.POST.get("imprimante")))
        if action == "tester":
            try:
                imprimer_test(objet)
            except ErreurImpression as erreur:
                messages.error(request, str(erreur))
            else:
                messages.success(request, f"Ticket de test envoyé à « {objet.libelle} ».")
        elif action == "basculer":
            objet.actif = not objet.actif
            objet.save(update_fields=["actif"])
            messages.success(request, f"Imprimante « {objet.libelle} » {'remise en service' if objet.actif else 'retirée'}.")
        return redirect("centres:reglages")
    objet = ImprimanteTicket(
        centre=centre,
        libelle=" ".join(request.POST.get("libelle", "").split())[:100] or "Imprimante",
        adresse_ip=request.POST.get("adresse_ip", "").strip(),
        port=entier(request.POST.get("port")) or 9100,
        largeur_caracteres=32 if request.POST.get("papier") == "58" else 48,
    )
    try:
        objet.full_clean()
    except ValidationError as erreur:
        messages.error(request, "Imprimante non ajoutée : " + " ".join(
            m for liste in erreur.message_dict.values() for m in liste))
        return redirect("centres:reglages")
    objet.save()
    messages.success(request, f"Imprimante « {objet.libelle} » ajoutée. Cliquez sur « Tester », puis choisissez-la "
                              "pour une borne (onglet Borne).")
    return redirect("centres:reglages")


@require_POST
@centre_actif_requis
def quotas(request, parcours_id):
    from parcours.models import Parcours

    _exiger(request)
    parcours = get_object_or_404(
        Parcours.objects.filter(centre=request.centre_actif, soumis_quota=True), pk=parcours_id
    )
    valeurs = {}
    for jour in range(7):
        for periode in ("MATIN", "APRES_MIDI"):
            brut = request.POST.get(f"q-{jour}-{periode}", "").strip()
            places = entier(brut) if brut else None
            valeurs[(jour, periode)] = places if places else None   # 0 ou vide : fermé
    try:
        changements = enregistrer_grille(parcours, valeurs, request.user)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
        return redirect("centres:reglages")
    if changements:
        messages.success(request, f"Places « {parcours.libelle_borne} » enregistrées ({changements} changement"
                                  f"{'s' if changements > 1 else ''}, valables dès aujourd'hui).")
    else:
        messages.info(request, "Aucun changement.")
    return redirect("centres:reglages")


@require_POST
@centre_actif_requis
def centre_nouveau(request):
    """Plateforme : un centre prêt à l'emploi en une étape (services, borne, places, motifs)."""
    if not est_administrateur_central(request.user):
        raise PermissionDenied
    try:
        centre = creer_centre(request.POST.get("nom"), request.POST.get("code"), entier(request.POST.get("medecins")) or 2)
    except ValidationError as erreur:
        messages.error(request, " ".join(erreur.messages))
        return redirect("centres:reglages")
    request.session[SESSION_CENTRE_KEY] = centre.pk
    messages.success(request, f"Centre « {centre.nom} » créé avec ses services, la borne (Médecin, Dentiste, "
                              "Infirmerie, Échographie), les places du dentiste et les motifs « Autre ». "
                              "Il est maintenant votre centre actif : créez ses comptes (onglet Comptes) et sa borne.")
    return redirect("centres:reglages")


@require_POST
@centre_actif_requis
def centre_modifier(request):
    """Plateforme : nom du centre, tickets délivrés par l'accueil, heures."""
    if not est_administrateur_central(request.user):
        raise PermissionDenied
    centre = request.centre_actif
    centre.nom = " ".join(request.POST.get("nom", "").split())[:200] or centre.nom
    centre.accueil_delivre_tickets = request.POST.get("accueil_delivre_tickets") == "1"
    for champ in ("heure_coupure_matin", "heure_cloture_tickets"):
        try:
            setattr(centre, champ, datetime.time.fromisoformat(request.POST.get(champ, "")))
        except ValueError:
            pass
    try:
        centre.full_clean()
    except ValidationError as erreur:
        messages.error(request, " ".join(m for liste in erreur.message_dict.values() for m in liste))
        return redirect("centres:reglages")
    centre.save()
    messages.success(request, f"Centre « {centre.nom} » enregistré.")
    return redirect("centres:reglages")
