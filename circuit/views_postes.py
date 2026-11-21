"""
Postes sans compte utilisateur :
- la borne (choix du mode et du service, émission du ticket) ;
- l'écran principal (affichage des appels de tous les services).

Borne (décision du 21/11/2026) : pas de clé. Un administrateur se connecte
une fois sur l'appareil et choisit la borne ; l'appareil la garde ensuite
(cookie signé de 10 ans, indépendant des sessions des agents) : elle ne se
déconnecte plus. Écran de la salle : ouvert depuis l'ordinateur d'un agent
sur son second écran (HDMI ou sans fil), sans clé. Accès limité au réseau
local, jamais par Internet. La sécurité n'intervient pas.
"""

import base64
from datetime import timedelta
from types import SimpleNamespace

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import logout
from django.core.exceptions import PermissionDenied
from django.core.signing import BadSignature
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from centres.access import utilisateur_peut_acceder_centre
from centres.models import Borne, Centre, EcranAffichage
from core.admin import centres_administres
from core.reseau import est_reseau_local
from core.timeutils import fuseau_horaire_centre, maintenant_centre

from . import services
from .impression import consigne_ticket, contenu_ticket, etat_imprimante_borne, imprimer_ticket, resultat_impression_android
from .models import Appel, EntreeFile, MotifDemande, Ticket
from .services import ErreurCircuit

SESSION_BORNE = "borne_id"
# Borne : cookie signé, indépendant de la session (une déconnexion d'agent ou
# d'administrateur sur l'appareil ne désactive pas la borne).
COOKIE_BORNE = "borne_poste"
SEL_COOKIE_BORNE = "circuit.borne"
DUREE_COOKIE_BORNE = 10 * 365 * 24 * 3600
SESSION_ECRAN = "ecran_id"
# Une borne ou un écran reste activé (session renouvelée à chaque usage) :
# le formulaire d'activation ne doit pas réapparaître devant les patients.
DUREE_SESSION_POSTE = 365 * 24 * 3600


def _reseau_local(request):
    if not getattr(settings, "RESEAU_LOCAL_UNIQUEMENT", True):
        return True
    # Adresse réelle du client, même derrière Nginx (core.reseau).
    return est_reseau_local(request)


def _borne_de_l_appareil(request):
    """Borne enregistrée sur cet appareil (cookie signé, ou ancienne session à clé)."""
    try:
        return int(request.get_signed_cookie(COOKIE_BORNE, salt=SEL_COOKIE_BORNE))
    except (KeyError, BadSignature, ValueError):
        pass
    ancienne = request.session.get(SESSION_BORNE)
    if ancienne:
        request._borne_a_memoriser = ancienne   # reportée dans le cookie (borne activée avec une clé)
    return ancienne


def _memoriser_borne(reponse, request, borne_id):
    reponse.set_signed_cookie(
        COOKIE_BORNE, str(borne_id), salt=SEL_COOKIE_BORNE, max_age=DUREE_COOKIE_BORNE,
        httponly=True, samesite="Lax", secure=request.is_secure(),
    )
    return reponse


def _poste_connecte(request, modele, cle_session):
    if not _reseau_local(request):
        raise PermissionDenied("Accès réservé au réseau local.")
    poste_id = _borne_de_l_appareil(request) if modele is Borne else request.session.get(cle_session)
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
    if modele is not Borne and request.session.get_expiry_age() < DUREE_SESSION_POSTE // 2:
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
    borne_id = _borne_de_l_appareil(request)
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


def _activation_borne(request):
    """
    Première mise en service de l'appareil : un administrateur (de la
    plateforme, ou du centre) connecté sur l'appareil choisit la borne.
    Aucune clé. L'administrateur est ensuite déconnecté de l'appareil ; la
    borne, elle, reste activée.
    """
    utilisateur = request.user
    if utilisateur.is_authenticated:
        centres = centres_administres(utilisateur, Borne)
    else:
        centres = Centre.objects.none()
    bornes = (
        Borne.objects.filter(centre__in=centres, actif=True, centre__actif=True)
        .select_related("centre").order_by("centre__nom", "libelle")
    )
    erreur = None
    if request.method == "POST":
        borne = bornes.filter(pk=request.POST.get("borne") or 0).first()
        if borne is not None:
            from audit.journal import journaliser
            from audit.models import JournalAudit

            journaliser(JournalAudit.Action.MODIFICATION, utilisateur, objet=borne,
                        details={"appareil": "borne activée sur cet appareil"}, request=request)
            logout(request)
            return _memoriser_borne(redirect("circuit:borne"), request, borne.pk)
        erreur = "Choisissez une borne dans la liste."
    return render(request, "circuit/borne_activation.html", {
        "bornes": bornes,
        "connecte": utilisateur.is_authenticated,
        "erreur": erreur,
        "connexion": f"{reverse('connexion')}?next={reverse('circuit:borne')}",
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
        return _activation_borne(request)
    reponse = _page_borne(request, poste)
    if getattr(request, "_borne_a_memoriser", None):
        _memoriser_borne(reponse, request, poste.pk)   # borne activée autrefois avec une clé
    return reponse


def _page_borne(request, poste):
    centre = poste.centre
    services.cloture_automatique(centre)
    if services.emission_terminee(centre):
        return _borne_fermee_pour_la_journee(request, centre)
    # Services choisis pour cette borne par l'administrateur de la plateforme
    # (tous ceux du centre si aucun n'est choisi).
    parcours = [
        p for p in poste.parcours_proposes().order_by("ordre_affichage", "libelle_borne")
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

    centre = poste.centre
    services.cloture_automatique(centre)
    if services.emission_terminee(centre):
        return redirect("circuit:borne")
    mode = request.POST.get("mode", "")
    parcours = motif = None
    if mode != Ticket.Mode.AUTRE:
        # Seulement un service proposé par cette borne.
        parcours = poste.parcours_proposes().filter(pk=request.POST.get("parcours") or 0).first()
    elif request.POST.get("motif"):
        motif = MotifDemande.objects.filter(centre=centre, actif=True, pk=request.POST["motif"]).first()

    with transaction.atomic():
        # Verrou sur la borne : deux appuis simultanés passent l'un après
        # l'autre, et le second voit le ticket du premier.
        Borne.objects.select_for_update().filter(pk=poste.pk).first()
        maintenant = timezone.now()
        # Limitation de débit par borne. Double appui : on revient à l'accueil
        # de la borne avec un message, jamais une page brute sans issue.
        delai = timedelta(seconds=getattr(settings, "BORNE_DELAI_MIN_SECONDES", 2))
        if delai.total_seconds() > 0 and Ticket.objects.filter(
            borne=poste, date_emission__gte=maintenant - delai
        ).exists():
            messages.warning(request, "Votre ticket vient d'être imprimé. Veuillez patienter quelques secondes.")
            return redirect("circuit:borne")
        plafond = getattr(settings, "BORNE_MAX_TICKETS_PAR_HEURE", 150)
        if plafond and Ticket.objects.filter(
            borne=poste, date_emission__gte=maintenant - timedelta(hours=1)
        ).count() >= plafond:
            messages.error(request, "La borne est momentanément saturée. Merci de vous présenter à l'accueil.")
            return redirect("circuit:borne")
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


def _imprime_par_android(ticket):
    from .models import Evenement

    return ticket.evenements.filter(type=Evenement.Type.IMPRESSION, details__bluetooth=True).exists()


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
            "consigne": consigne_ticket(ticket),
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
    with transaction.atomic():
        # Verrou sur le ticket : deux appuis sur « Réessayer » n'impriment
        # qu'une fois (le second voit l'impression réussie du premier).
        Ticket.objects.select_for_update().filter(pk=ticket.pk).first()
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
    if _imprime_par_android(ticket):
        # Déjà imprimé par l'application : pas de second ticket papier.
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
            # Tous les appels en cours (au plus 50) : l'écran en affiche
            # `nombre` et signale les autres (« + N autres appels en cours »).
            if len(en_cours) < 50:
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
    return render(request, "circuit/ecran.html", {
        "centre": centre, "poste": poste, "url_appels": reverse("circuit:ecran_appels"),
    })


def _centre_de_la_salle(request, code_centre):
    """Écran de la salle : réseau local uniquement, sans compte ni clé (numéros de ticket seulement)."""
    if not _reseau_local(request):
        raise PermissionDenied("Accès réservé au réseau local.")
    centre = get_object_or_404(Centre, code__iexact=code_centre, actif=True)
    timezone.activate(fuseau_horaire_centre(centre))
    return centre


def ecran_salle(request, code_centre):
    """
    Écran des appels de la salle, ouvert depuis l'ordinateur d'un agent sur
    son second écran (HDMI ou sans fil, décision du 21/11/2026). Il ne dépend
    pas de la session de l'agent : il reste affiché après sa déconnexion.
    """
    centre = _centre_de_la_salle(request, code_centre)
    return render(request, "circuit/ecran.html", {
        "centre": centre, "poste": None, "salle": True,
        "url_appels": reverse("circuit:ecran_salle_appels", args=[centre.code]),
    })


def ecran_salle_appels(request, code_centre):
    return _appels_de_l_ecran(request, _centre_de_la_salle(request, code_centre), None)


def ecran_appels(request):
    centre, poste = _centre_ecran(request)
    if centre is None:
        return HttpResponse(status=403)
    return _appels_de_l_ecran(request, centre, poste)


def _appels_de_l_ecran(request, centre, poste):
    services.cloture_automatique(centre)
    if poste is not None and (
        poste.derniere_activite is None or timezone.now() - poste.derniere_activite > timedelta(minutes=1)
    ):
        # Suivi de l'écran : une écriture par minute suffit (et non toutes les 3 s).
        EcranAffichage.objects.filter(pk=poste.pk).update(derniere_activite=timezone.now())
    nombre = poste.nb_appels_affiches if poste else 8
    en_cours, precedents = appels_ecran(centre, nombre)
    masques = max(0, len(en_cours) - nombre)
    en_cours = en_cours[:nombre]
    recents = timezone.now() - timedelta(seconds=centre.duree_affichage_appel_secondes)
    return render(
        request,
        "circuit/_ecran_appels.html",
        {
            "en_cours": en_cours,
            "appels_masques": masques,
            "precedents": precedents,
            "fuseau": fuseau_horaire_centre(centre),
            "recents": recents,
            "annonce": poste.annonce_vocale if poste else True,
        },
    )


@require_POST
def poste_deconnexion(request):
    """Désactive l'écran à clé de cet appareil. La borne, elle, ne se déconnecte jamais."""
    request.session.pop(SESSION_ECRAN, None)
    return redirect("circuit:ecran")
