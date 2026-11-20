"""
Postes sans compte utilisateur :
- la borne libre-service (choix du mode et du service, émission du ticket) ;
- l'écran principal (affichage des appels de tous les services).

Chaque poste est identifié par un code et une clé secrète enregistrés en
session. Accès limité au réseau local. La sécurité n'intervient pas.
"""

import base64
import ipaddress
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from centres.access import utilisateur_peut_acceder_centre
from centres.models import Borne, EcranAffichage
from core.timeutils import fuseau_horaire_centre, maintenant_centre
from parcours.models import Parcours

from . import services
from .impression import contenu_ticket, etat_imprimante_borne, imprimer_ticket, resultat_impression_android
from .models import Appel, EntreeFile, MotifDemande, Ticket
from .services import ErreurCircuit

SESSION_BORNE = "borne_id"
SESSION_ECRAN = "ecran_id"
# Une borne ou un écran reste activé (session renouvelée à chaque usage) :
# le formulaire d'activation ne doit pas réapparaître devant les patients.
DUREE_SESSION_POSTE = 365 * 24 * 3600


def _reseau_local(request):
    if not getattr(settings, "RESEAU_LOCAL_UNIQUEMENT", True):
        return True
    try:
        adresse = ipaddress.ip_address(request.META.get("REMOTE_ADDR", ""))
    except ValueError:
        return False
    return adresse.is_private or adresse.is_loopback


def _poste_connecte(request, modele, cle_session):
    if not _reseau_local(request):
        raise PermissionDenied("Accès réservé au réseau local.")
    poste_id = request.session.get(cle_session)
    if not poste_id:
        return None
    poste = modele.objects.select_related("centre").filter(pk=poste_id, actif=True, centre__actif=True).first()
    if poste is None:
        # Poste supprimé : on l'oublie. Poste seulement désactivé par
        # l'administrateur : la session est gardée, pour qu'il reprenne dès
        # sa réactivation, sans ressaisir la clé.
        if not modele.objects.filter(pk=poste_id).exists():
            request.session.pop(cle_session, None)
        return None
    if request.session.get_expiry_age() < DUREE_SESSION_POSTE // 2:
        request.session.set_expiry(DUREE_SESSION_POSTE)
    timezone.activate(fuseau_horaire_centre(poste.centre))
    return poste


def _connexion_poste(request, modele, cle_session, gabarit, titre):
    if not _reseau_local(request):
        raise PermissionDenied("Accès réservé au réseau local.")
    erreur = None
    if request.method == "POST":
        code_centre = request.POST.get("centre", "").strip().upper()
        code = request.POST.get("code", "").strip()
        cle = request.POST.get("cle", "").strip()
        poste = modele.objects.filter(
            centre__code__iexact=code_centre, code=code, actif=True, centre__actif=True
        ).first()
        if poste is not None and poste.verifier_cle(cle):
            request.session.cycle_key()
            request.session[cle_session] = poste.pk
            request.session.set_expiry(DUREE_SESSION_POSTE)
            poste.derniere_activite = timezone.now()
            poste.save(update_fields=["derniere_activite"])
            return None
        erreur = "Code ou clé incorrect."
    return render(request, gabarit, {"erreur": erreur, "titre": titre})


# ---------------------------------------------------------------------------
# Borne
# ---------------------------------------------------------------------------

def _borne_desactivee(request):
    """Borne activée sur cet appareil, mais désactivée par l'administrateur."""
    borne_id = request.session.get(SESSION_BORNE)
    if not borne_id:
        return None
    return Borne.objects.select_related("centre").filter(pk=borne_id).filter(
        Q(actif=False) | Q(centre__actif=False)
    ).first()


def _borne_fermee_pour_la_journee(request, centre):
    return render(request, "circuit/borne_fermee.html", {
        "centre": centre,
        "titre": "Tickets terminés pour aujourd'hui",
        "message": "La borne délivre de nouveau des tickets demain. "
                   "Pour une urgence, présentez-vous à l'accueil.",
    })


def borne(request):
    poste = _poste_connecte(request, Borne, SESSION_BORNE)
    if poste is None and request.method == "GET":
        fermee = _borne_desactivee(request)
        if fermee is not None:
            # Écran « borne fermée » : la page se recharge seule et reprend
            # dès que l'administrateur réactive la borne.
            return render(request, "circuit/borne_fermee.html", {"centre": fermee.centre})
    if poste is None:
        reponse = _connexion_poste(request, Borne, SESSION_BORNE, "circuit/poste_connexion.html", "Activer la borne")
        return reponse or redirect("circuit:borne")

    centre = poste.centre
    services.cloture_automatique(centre)
    if services.emission_terminee(centre):
        return _borne_fermee_pour_la_journee(request, centre)
    parcours = [
        p for p in Parcours.objects.filter(centre=centre, actif=True, visible_borne=True)
        .order_by("ordre_affichage", "libelle_borne")
        if p.version_publiee() is not None
    ]
    return render(
        request,
        "circuit/borne.html",
        {
            "borne": poste,
            "centre": centre,
            "parcours": parcours,
            "motifs": MotifDemande.objects.filter(centre=centre, actif=True),
            "modes": Ticket.Mode.choices,
            "etat_imprimante": etat_imprimante_borne(poste)[0],
        },
    )


@require_POST
def borne_emettre(request):
    poste = _poste_connecte(request, Borne, SESSION_BORNE)
    if poste is None:
        return redirect("circuit:borne")

    # Limitation de débit par borne.
    delai = timedelta(seconds=getattr(settings, "BORNE_DELAI_MIN_SECONDES", 2))
    # Double appui : on revient à l'accueil de la borne avec un message,
    # jamais une page brute sans issue devant le patient.
    if delai.total_seconds() > 0 and Ticket.objects.filter(
        borne=poste, date_emission__gte=timezone.now() - delai
    ).exists():
        messages.warning(request, "Votre ticket vient d'être imprimé. Veuillez patienter quelques secondes.")
        return redirect("circuit:borne")

    centre = poste.centre
    services.cloture_automatique(centre)
    if services.emission_terminee(centre):
        return redirect("circuit:borne")
    mode = request.POST.get("mode", "")
    parcours = motif = None
    if mode != Ticket.Mode.AUTRE:
        parcours = Parcours.objects.filter(centre=centre, pk=request.POST.get("parcours") or 0).first()
    elif request.POST.get("motif"):
        motif = MotifDemande.objects.filter(centre=centre, actif=True, pk=request.POST["motif"]).first()

    try:
        ticket = services.emettre_ticket(centre, mode, parcours=parcours, borne=poste, motif=motif)
    except ErreurCircuit as erreur:
        messages.error(request, " ".join(erreur.messages))
        return redirect("circuit:borne")
    # Option A : le serveur imprime directement sur l'imprimante réseau de la
    # borne (après l'enregistrement du ticket, qui reste valable même si
    # l'impression échoue).
    imprimer_ticket(ticket, poste, _personnes_devant(ticket))
    return redirect("circuit:borne_ticket", ticket.pk)


def _personnes_devant(ticket):
    entree = ticket.passages.first().entrees.first()
    if entree is None:
        return 0
    return entree.file.entrees.filter(statut="EN_ATTENTE", rang__lt=entree.rang).count()


def _etat_impression(ticket, poste):
    """« serveur » (imprimé), « echec », ou « navigateur » (pas d'imprimante réseau)."""
    from .models import Evenement

    if poste.imprimante_id is None or not poste.imprimante.actif:
        return "navigateur"
    dernier = (
        ticket.evenements.filter(type__in=[Evenement.Type.IMPRESSION, Evenement.Type.ECHEC_IMPRESSION])
        .order_by("-date", "-id")
        .first()
    )
    if dernier is not None and dernier.type == Evenement.Type.IMPRESSION:
        return "serveur"
    return "echec"


def _ticket_de_la_borne(poste, ticket_id):
    return get_object_or_404(Ticket.objects.filter(centre=poste.centre, borne=poste), pk=ticket_id)


def borne_ticket(request, ticket_id):
    poste = _poste_connecte(request, Borne, SESSION_BORNE)
    if poste is None:
        return redirect("circuit:borne")
    ticket = _ticket_de_la_borne(poste, ticket_id)
    etat = _etat_impression(ticket, poste)
    recent = timezone.now() - ticket.date_emission < timedelta(minutes=3)
    return render(
        request,
        "circuit/borne_ticket.html",
        {
            "ticket": ticket,
            "centre": poste.centre,
            "devant": _personnes_devant(ticket),
            "impression": etat,
            "peut_reessayer": etat == "echec" and recent,
        },
    )


@require_POST
def borne_reimprimer(request, ticket_id):
    """Nouvelle tentative d'impression, seulement juste après l'émission."""
    poste = _poste_connecte(request, Borne, SESSION_BORNE)
    if poste is None:
        return redirect("circuit:borne")
    ticket = _ticket_de_la_borne(poste, ticket_id)
    if (
        _etat_impression(ticket, poste) == "echec"
        and timezone.now() - ticket.date_emission < timedelta(minutes=3)
    ):
        imprimer_ticket(ticket, poste, _personnes_devant(ticket))
    return redirect("circuit:borne_ticket", ticket.pk)


def borne_ticket_escpos(request, ticket_id):
    """
    Dernier recours (application Android de la borne, imprimante Bluetooth) :
    octets ESC/POS du ticket, en base64, que l'application envoie elle-même à
    l'imprimante appairée. Seulement juste après l'émission, et seulement quand
    aucune imprimante réseau n'est rattachée à la borne.
    """
    poste = _poste_connecte(request, Borne, SESSION_BORNE)
    if poste is None:
        raise PermissionDenied
    ticket = _ticket_de_la_borne(poste, ticket_id)
    if _etat_impression(ticket, poste) != "navigateur" or (
        timezone.now() - ticket.date_emission >= timedelta(minutes=3)
    ):
        raise PermissionDenied
    largeur = 32 if request.GET.get("largeur") == "32" else 48
    page = request.GET.get("page") if request.GET.get("page") in ("cp858", "cp850", "ascii") else "ascii"
    imprimante = SimpleNamespace(page_codes=page, largeur_caracteres=largeur)
    donnees = contenu_ticket(ticket, imprimante, _personnes_devant(ticket))
    return JsonResponse({"numero": ticket.numero, "escpos": base64.b64encode(donnees).decode("ascii")})


@require_POST
def borne_impression_android(request, ticket_id):
    """L'application Android signale si l'imprimante Bluetooth a imprimé le ticket."""
    poste = _poste_connecte(request, Borne, SESSION_BORNE)
    if poste is None:
        raise PermissionDenied
    ticket = _ticket_de_la_borne(poste, ticket_id)
    if _etat_impression(ticket, poste) != "navigateur" or (
        timezone.now() - ticket.date_emission >= timedelta(minutes=3)
    ):
        raise PermissionDenied
    resultat_impression_android(ticket, poste, request.POST.get("ok") == "1", request.POST.get("erreur", ""))
    return HttpResponse(status=204)


# ---------------------------------------------------------------------------
# Écran principal
# ---------------------------------------------------------------------------

def _centre_ecran(request):
    """L'écran est soit un poste identifié par clé, soit un agent connecté."""
    poste = _poste_connecte(request, EcranAffichage, SESSION_ECRAN)
    if poste is not None:
        return poste.centre, poste
    centre = getattr(request, "centre_actif", None)
    if request.user.is_authenticated and centre is not None and utilisateur_peut_acceder_centre(request.user, centre):
        return centre, None
    return None, None


def appels_ecran(centre, nombre=8):
    """
    Appels de l'écran principal, en deux groupes : (en_cours, precedents).

    Chaque appel a son propre état, celui du passage du patient dans le
    service qui l'a appelé (son entrée de file) :
    - en cours : le service n'a pas encore terminé (patient appelé ou en
      cours de prise en charge). Les appels en cours de tous les services
      restent affichés en même temps ; le plus récent est en grand.
    - précédent : le service a terminé (ou le patient est absent, ou le
      ticket est annulé ou clôturé). L'appel rejoint « Appels précédents »
      avec son heure et le nom du service.
    Terminer un passage ne touche que l'appel de ce passage : les appels des
    autres services, et le ticket lui-même, ne changent pas. Un même ticket
    peut donc figurer plusieurs fois dans l'historique (M001 Accueil, puis
    M001 Infirmerie). Un rappel remplace l'appel précédent du même passage.

    Appel d'un visiteur (mode Autre) : son passage à l'accueil est déjà
    terminé ; l'appel reste en cours pendant la durée d'affichage du centre.
    """
    maintenant = timezone.now()
    duree = timedelta(seconds=max(centre.duree_affichage_appel_secondes, 1))
    limite_historique = maintenant - duree * 10
    jour = maintenant_centre(centre).date()
    candidats = (
        Appel.objects.filter(centre=centre, mode=Appel.Mode.ECRAN, service__afficher_ecran_principal=True)
        .filter(
            Q(date__gte=limite_historique)
            | Q(ticket__jour=jour, entree__statut__in=EntreeFile.STATUTS_TENUS)
        )
        .select_related("service", "entree")
        .order_by("-date", "-id")[:200]
    )
    en_cours, precedents, vus = [], [], set()
    for appel in candidats:
        entree = appel.entree
        visiteur = entree.date_fin is not None and appel.date > entree.date_fin
        cle = (appel.entree_id, visiteur)
        if cle in vus:
            continue  # rappel : seul le dernier appel du passage compte
        vus.add(cle)
        if visiteur:
            actif = appel.date >= maintenant - duree
        else:
            actif = entree.statut in EntreeFile.STATUTS_TENUS
        if actif:
            if len(en_cours) < nombre:
                en_cours.append(appel)
        elif appel.date >= limite_historique and len(precedents) < nombre:
            precedents.append(appel)
    return en_cours, precedents


def appels_a_afficher(centre, nombre=8):
    """Tous les appels affichés sur l'écran principal : en cours puis précédents."""
    en_cours, precedents = appels_ecran(centre, nombre)
    return en_cours + precedents


def ecran(request):
    centre, poste = _centre_ecran(request)
    if centre is None:
        reponse = _connexion_poste(
            request, EcranAffichage, SESSION_ECRAN, "circuit/poste_connexion.html", "Activer l'écran principal"
        )
        return reponse or redirect("circuit:ecran")
    return render(request, "circuit/ecran.html", {"centre": centre, "poste": poste})


def ecran_appels(request):
    centre, poste = _centre_ecran(request)
    if centre is None:
        return HttpResponse(status=403)
    services.cloture_automatique(centre)
    if poste is not None:
        EcranAffichage.objects.filter(pk=poste.pk).update(derniere_activite=timezone.now())
    nombre = poste.nb_appels_affiches if poste else 8
    en_cours, precedents = appels_ecran(centre, nombre)
    recents = timezone.now() - timedelta(seconds=centre.duree_affichage_appel_secondes)
    return render(
        request,
        "circuit/_ecran_appels.html",
        {
            "en_cours": en_cours,
            "precedents": precedents,
            "fuseau": fuseau_horaire_centre(centre),
            "recents": recents,
            "annonce": poste.annonce_vocale if poste else True,
        },
    )


@require_POST
def poste_deconnexion(request):
    request.session.pop(SESSION_BORNE, None)
    request.session.pop(SESSION_ECRAN, None)
    return redirect("circuit:borne")
