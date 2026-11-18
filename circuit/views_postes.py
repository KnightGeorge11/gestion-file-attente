"""
Postes sans compte utilisateur :
- la borne libre-service (choix du mode et du service, émission du ticket) ;
- l'écran principal (affichage des appels de tous les services).

Chaque poste est identifié par un code et une clé secrète enregistrés en
session. Accès limité au réseau local. La sécurité n'intervient pas.
"""

import ipaddress
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from centres.access import utilisateur_peut_acceder_centre
from centres.models import Borne, EcranAffichage
from core.timeutils import fuseau_horaire_centre
from parcours.models import Parcours

from . import services
from .models import Appel, MotifDemande, Ticket
from .services import ErreurCircuit

SESSION_BORNE = "borne_id"
SESSION_ECRAN = "ecran_id"


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
        request.session.pop(cle_session, None)
        return None
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
            poste.derniere_activite = timezone.now()
            poste.save(update_fields=["derniere_activite"])
            return None
        erreur = "Code ou clé incorrect."
    return render(request, gabarit, {"erreur": erreur, "titre": titre})


# ---------------------------------------------------------------------------
# Borne
# ---------------------------------------------------------------------------

def borne(request):
    poste = _poste_connecte(request, Borne, SESSION_BORNE)
    if poste is None:
        reponse = _connexion_poste(request, Borne, SESSION_BORNE, "circuit/poste_connexion.html", "Activer la borne")
        return reponse or redirect("circuit:borne")

    centre = poste.centre
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
        },
    )


@require_POST
def borne_emettre(request):
    poste = _poste_connecte(request, Borne, SESSION_BORNE)
    if poste is None:
        return redirect("circuit:borne")

    # Limitation de débit par borne.
    delai = timedelta(seconds=getattr(settings, "BORNE_DELAI_MIN_SECONDES", 2))
    if poste.derniere_activite and timezone.now() - poste.derniere_activite < delai and \
            Ticket.objects.filter(borne=poste, date_emission__gte=timezone.now() - delai).exists():
        return HttpResponse("Veuillez patienter quelques secondes.", status=429)

    centre = poste.centre
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
    return redirect("circuit:borne_ticket", ticket.pk)


def borne_ticket(request, ticket_id):
    poste = _poste_connecte(request, Borne, SESSION_BORNE)
    if poste is None:
        return redirect("circuit:borne")
    ticket = get_object_or_404(Ticket.objects.filter(centre=poste.centre, borne=poste), pk=ticket_id)
    en_attente = ticket.passages.first().entrees.first()
    devant = 0
    if en_attente is not None:
        devant = en_attente.file.entrees.filter(statut="EN_ATTENTE", rang__lt=en_attente.rang).count()
    return render(request, "circuit/borne_ticket.html", {"ticket": ticket, "centre": poste.centre, "devant": devant})


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


def appels_a_afficher(centre, nombre=8):
    """Appels récents de tous les services affichés sur l'écran principal."""
    limite = timezone.now() - timedelta(seconds=max(centre.duree_affichage_appel_secondes, 1) * 10)
    return list(
        Appel.objects.filter(
            centre=centre,
            mode=Appel.Mode.ECRAN,
            date__gte=limite,
            service__afficher_ecran_principal=True,
        )
        .order_by("-date", "-id")[:nombre]
    )


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
    if poste is not None:
        EcranAffichage.objects.filter(pk=poste.pk).update(derniere_activite=timezone.now())
    nombre = poste.nb_appels_affiches if poste else 8
    appels = appels_a_afficher(centre, nombre)
    recents = timezone.now() - timedelta(seconds=centre.duree_affichage_appel_secondes)
    return render(
        request,
        "circuit/_ecran_appels.html",
        {
            "appels": appels,
            "recents": recents,
            "annonce": poste.annonce_vocale if poste else True,
        },
    )


@require_POST
def poste_deconnexion(request):
    request.session.pop(SESSION_BORNE, None)
    request.session.pop(SESSION_ECRAN, None)
    return redirect("circuit:borne")
