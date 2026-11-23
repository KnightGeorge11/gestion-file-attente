"""
Interfaces des agents (Django Templates + HTMX).

Chaque service a une interface commune ; chaque agent s'authentifie avec
son compte. Toutes les vérifications (centre actif, portée du service,
permission) sont faites côté serveur.
"""

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Avg, Count, F, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from centres.decorators import centre_actif_requis
from centres.models import Service
from comptes.access import utilisateur_peut_acceder_service
from comptes.models import Utilisateur
from comptes.permissions import a_droit, affectation_active, appel_limite_aux_autres, voit_autres_seulement
from core.access import objet_du_centre
from core.timeutils import maintenant_centre

from . import services
from .models import (
    Appel,
    DemandeAutre,
    EntreeFile,
    Notification,
    Passage,
    ReponseNotification,
    ReservationQuota,
    Ticket,
    Transfert,
)
from core.requetes import entier

from .services import ErreurCircuit
from .visibilite import services_operationnels, tickets_a_venir, tickets_visibles


def _est_htmx(request):
    """Requête HTMX qui attend un fragment. Un lien ou formulaire « boosté »
    (hx-boost) est une navigation : il attend une page complète."""
    return (request.headers.get("HX-Request") == "true"
            and request.headers.get("HX-Boosted") != "true")


def _service_autorise(request, service_id):
    service = get_object_or_404(
        Service.objects.filter(centre=request.centre_actif, actif=True), pk=service_id
    )
    if not utilisateur_peut_acceder_service(request.user, request.centre_actif, service):
        raise PermissionDenied
    return service


def _exiger(request, permission, service=None):
    if not a_droit(request.user, permission, request.centre_actif, service):
        raise PermissionDenied


def _file_du_service(request, service):
    files = service.files.filter(actif=True).order_by("-par_defaut", "nom")
    file_id = request.GET.get("file") or request.POST.get("file")
    if file_id:
        return get_object_or_404(files, pk=entier(file_id)), files
    file = files.first()
    if service.est_accueil and service.centre.accueil_delivre_tickets:
        # L'accueil délivre les tickets de soins à la borne : seuls les « Autre »
        # passent par sa file, ouverte par défaut (décision du 22/11/2026).
        file = files.filter(code=services.CODE_FILE_AUTRE).first() or file
    if file is None:
        raise PermissionDenied
    return file, files


# ---------------------------------------------------------------------------
# Liste des services et interface commune d'un service
# ---------------------------------------------------------------------------

def _bornes_administrees(request):
    """Bornes du centre actif que cet utilisateur administre (sinon : refus)."""
    from centres.models import Borne
    from core.admin import centres_administres

    centre = request.centre_actif
    if not centres_administres(request.user, Borne).filter(pk=centre.pk).exists():
        raise PermissionDenied
    return Borne.objects.filter(centre=centre).select_related("imprimante").prefetch_related("parcours")


@centre_actif_requis
def bornes(request):
    """
    « Borne » (décision du 23/11/2026) : l'administrateur active une borne sur
    l'appareil où il se trouve (tablette de l'accueil…), sans passer par
    l'administration, et met chaque borne en service ou hors service.
    L'activation reprend celle de la page de la borne : l'administrateur est
    ensuite déconnecté de l'appareil, qui reste la borne.
    """
    from .views_postes import _borne_de_l_appareil

    liste = list(_bornes_administrees(request).order_by("libelle"))
    for borne in liste:
        borne.services = [p.libelle_borne for p in borne.parcours_proposes().order_by("ordre_affichage")]
    appareil = _borne_de_l_appareil(request)
    return render(request, "circuit/bornes.html", {
        "page": "bornes",
        "bornes": liste,
        "borne_de_l_appareil": next((b for b in liste if b.pk == appareil), None),
        "peut_modifier": a_droit(request.user, "centres.change_borne", request.centre_actif),
    })


@require_POST
@centre_actif_requis
def borne_basculer(request, borne_id):
    """Met une borne en service ou hors service (comme le bouton de l'administration)."""
    from audit.journal import journaliser
    from audit.models import JournalAudit

    borne = get_object_or_404(_bornes_administrees(request), pk=borne_id)
    if not a_droit(request.user, "centres.change_borne", request.centre_actif):
        raise PermissionDenied
    borne.actif = not borne.actif
    borne.save(update_fields=["actif"])
    etat = "remise en service" if borne.actif else "mise hors service"
    journaliser(JournalAudit.Action.MODIFICATION, request.user, objet=borne,
                details={"actif": borne.actif}, request=request)
    messages.success(request, f"Borne « {borne.libelle} » {etat}.")
    return redirect("circuit:bornes")


@centre_actif_requis
def ecran_connexion(request):
    """
    Écran de la salle (décision du 21/11/2026) : le système détecte les écrans
    physiques reliés à cet ordinateur (HDMI, ou sans fil une fois connectés
    par Windows) et y affiche l'écran des appels en plein écran ; il l'y
    réaffiche quand l'écran est rebranché. TV compatible Cast : envoi direct.
    """
    from urllib.parse import urlsplit

    from core.reseau import adresse_pour_le_reseau

    centre = request.centre_actif
    adresse = adresse_pour_le_reseau(request, reverse("circuit:ecran_salle", args=[centre.code]))
    return render(request, "circuit/ecran_connexion.html", {
        "adresse_ecran": adresse,
        "adresse_tv": adresse_pour_le_reseau(request, reverse("ecran_tv")),
        # Toujours « localhost » : aucune adresse réseau trouvée (PC hors réseau).
        "adresse_locale": urlsplit(adresse).hostname in ("localhost", "127.0.0.1", "::1"),
    })


def _contexte_ecrans_wifi(request, rechercher=True):
    """
    Écrans Wi-Fi du point de vue du centre actif :
    - prêts : TV qui ont ouvert une fois …/tv/ (on les choisit d'un clic) ;
    - à préparer : TV annoncées sur le réseau (UPnP/DIAL) qui n'ont pas encore
      ouvert …/tv/.
    """
    from datetime import timedelta

    from django.core.cache import cache

    from centres.models import AppareilEcran
    from core.decouverte import rechercher_ecrans
    from core.reseau import adresse_pour_le_reseau

    centre = request.centre_actif
    # Recherche sur le réseau (environ 3 s) : gardée 30 s pour les actions qui suivent.
    trouves = cache.get("circuit:ecrans-annonces")
    if trouves is None or rechercher:
        trouves = rechercher_ecrans(attente=3)
        cache.set("circuit:ecrans-annonces", trouves, 30)
    noms = {e["adresse_ip"]: e for e in trouves}
    recents = timezone.now() - timedelta(minutes=10)
    # Écrans libres récents, ou ceux de ce centre : jamais les écrans (nom,
    # adresse) des autres centres, sauf pour l'administrateur de la plateforme.
    from centres.access import est_administrateur_central

    visibles = Q(centre=centre) | Q(derniere_activite__gte=recents, centre__isnull=True)
    if est_administrateur_central(request.user):
        visibles |= Q(derniere_activite__gte=recents)
    appareils = list(
        AppareilEcran.objects.select_related("centre").filter(visibles)
        .order_by("-derniere_activite")[:30]
    )
    for appareil in appareils:
        annonce = noms.get(appareil.adresse_ip)
        appareil.nom_affiche = appareil.nom or (annonce["nom"] if annonce else "") or f"Écran {appareil.code}"
        appareil.marque = f"{annonce['marque']} {annonce['modele']}".strip() if annonce else ""
    adresses_pretes = {a.adresse_ip for a in appareils}
    return {
        "appareils": appareils,
        "a_preparer": [e for e in trouves if e["adresse_ip"] not in adresses_pretes],
        "adresse_tv": adresse_pour_le_reseau(request, reverse("ecran_tv")),
    }


@centre_actif_requis
def ecrans_reseau(request):
    """Fragment HTMX : écrans Wi-Fi prêts ou à préparer (« rechercher=1 » : nouvelle recherche sur le réseau)."""
    rechercher = request.GET.get("rechercher") == "1"
    return render(request, "circuit/_ecrans_reseau.html", _contexte_ecrans_wifi(request, rechercher))


@require_POST
@centre_actif_requis
def ecran_wifi_choisir(request):
    """
    « Afficher les appels ici » / « Arrêter » sur un écran Wi-Fi, depuis la
    page Écran. Un écran utilisé par un autre centre ne peut pas être pris
    (sauf par l'administrateur de la plateforme).
    """
    from audit.journal import journaliser
    from audit.models import JournalAudit
    from centres.access import est_administrateur_central
    from centres.models import AppareilEcran

    centre = request.centre_actif
    appareil = get_object_or_404(AppareilEcran.objects.select_related("centre"), pk=entier(request.POST.get("appareil")))
    pris_ailleurs = appareil.centre_id and appareil.centre_id != centre.pk
    if pris_ailleurs and not est_administrateur_central(request.user):
        messages.error(request, f"Cet écran affiche les appels du centre {appareil.centre.nom}.")
    elif request.POST.get("action") == "arreter":
        appareil.centre = None
        appareil.save(update_fields=["centre"])
        messages.success(request, f"Affichage arrêté sur « {appareil} ».")
        journaliser(JournalAudit.Action.MODIFICATION, request.user, centre=centre,
                    details={"ecran_wifi": appareil.code, "action": "arrêt"}, request=request)
    else:
        appareil.centre = centre
        appareil.save(update_fields=["centre"])
        messages.success(request, f"Les appels s'affichent sur « {appareil} » (dans 3 secondes au plus).")
        journaliser(JournalAudit.Action.MODIFICATION, request.user, centre=centre,
                    details={"ecran_wifi": appareil.code, "action": "affichage"}, request=request)
    reponse = render(request, "circuit/_ecrans_reseau.html", _contexte_ecrans_wifi(request, rechercher=False))
    return reponse


SESSION_SALLE = "salle_indiquee"
ROLES_AVEC_SALLE = ("Médecin",)


def _salle_a_indiquer(request):
    """
    Médecin pas encore passé par « Votre salle » depuis sa connexion dans ce
    centre (décision du 21/11/2026) : sa salle est annoncée à chacun de ses appels.
    """
    affectation = affectation_active(request.user, request.centre_actif)
    return bool(
        affectation is not None
        and affectation.groupe_id
        and affectation.groupe.name in ROLES_AVEC_SALLE
        and request.session.get(SESSION_SALLE) != request.centre_actif.pk
    )


def _vers_ma_salle(request):
    return redirect(f"{reverse('circuit:ma_salle')}?next={request.get_full_path()}")


@centre_actif_requis
def ma_salle(request):
    """
    « Votre salle » : le médecin indique la salle où il reçoit (proposée à
    chaque connexion, pré-remplie avec la dernière). Elle est annoncée à
    l'écran à chacun de ses appels.
    """
    from django.utils.http import url_has_allowed_host_and_scheme

    affectation = affectation_active(request.user, request.centre_actif)
    if affectation is None:
        raise PermissionDenied
    suite = request.GET.get("next") or request.POST.get("next") or reverse("circuit:guichets")
    if not url_has_allowed_host_and_scheme(suite, allowed_hosts={request.get_host()}):
        suite = reverse("circuit:guichets")
    obligatoire = bool(affectation.groupe_id and affectation.groupe.name in ROLES_AVEC_SALLE)
    erreur = None
    if request.method == "POST":
        salle = " ".join(request.POST.get("salle", "").split())[:50]
        if obligatoire and not salle:
            erreur = "Indiquez le numéro de la salle où vous recevez les patients (ex. 1)."
        else:
            affectation.salle = salle
            affectation.save(update_fields=["salle"])
            request.session[SESSION_SALLE] = request.centre_actif.pk
            if salle:
                messages.success(request, f"Votre salle : {salle}. Elle sera annoncée à chacun de vos appels.")
            return redirect(suite)
    return render(request, "circuit/ma_salle.html", {
        "salle": affectation.salle, "suite": suite, "obligatoire": obligatoire, "erreur": erreur,
    })


@centre_actif_requis
def guichets(request):
    if _salle_a_indiquer(request):
        return _vers_ma_salle(request)
    centre = request.centre_actif
    services.cloture_automatique(centre)
    jour = maintenant_centre(centre).date()
    # Trois requêtes groupées pour tout le centre (et non trois par service).
    def par_service(requete, champ):
        return dict(requete.values_list(champ).annotate(n=Count("id")).order_by())

    attente_par_service = par_service(
        EntreeFile.objects.filter(centre=centre, statut=EntreeFile.Statut.EN_ATTENTE), "file__service"
    )
    en_cours_par_service = par_service(
        EntreeFile.objects.filter(centre=centre, statut__in=EntreeFile.STATUTS_TENUS), "file__service"
    )
    traites_par_service = par_service(
        Passage.objects.filter(centre=centre, statut=Passage.Statut.TERMINE, ticket__jour=jour), "service"
    )
    liste, chiffres = [], {}
    for service in Service.objects.filter(centre=centre, actif=True).order_by("ordre", "nom"):
        if utilisateur_peut_acceder_service(request.user, centre, service):
            liste.append((service, attente_par_service.get(service.pk, 0)))
            chiffres[service.pk] = {
                "en_cours": en_cours_par_service.get(service.pk, 0),
                "traites": traites_par_service.get(service.pk, 0),
            }
    return render(
        request,
        "circuit/guichets.html",
        {
            "services": [(service, en_attente, chiffres[service.pk]) for service, en_attente in liste],
            "voit_tickets": bool(services_operationnels(request.user, centre)),
            "total_attente": sum(en_attente for _, en_attente in liste),
        },
    )


def _indicateurs_service(service, file, agent, jour):
    """Chiffres du jour affichés en tête de l'interface d'un service."""
    passages_du_jour = Passage.objects.filter(service=service, ticket__jour=jour)
    termines = passages_du_jour.filter(statut=Passage.Statut.TERMINE)
    attente_moyenne = passages_du_jour.filter(date_debut__isnull=False).aggregate(
        moyenne=Avg(F("date_debut") - F("date_arrivee"))
    )["moyenne"]
    plus_ancien = (
        EntreeFile.objects.filter(file=file, statut=EntreeFile.Statut.EN_ATTENTE)
        .order_by("date_entree")
        .values_list("date_entree", flat=True)
        .first()
    )
    return {
        "en_attente": EntreeFile.objects.filter(file=file, statut=EntreeFile.Statut.EN_ATTENTE).count(),
        "en_cours": EntreeFile.objects.filter(file=file, statut__in=EntreeFile.STATUTS_TENUS).count(),
        "traites": termines.count(),
        "traites_par_moi": termines.filter(termine_par=agent).count(),
        "attente_moyenne": attente_moyenne,
        "plus_ancien": plus_ancien,
        "absents": passages_du_jour.filter(statut=Passage.Statut.ABSENT).count(),
    }


def _contexte_guichet(request, service, file):
    services.cloture_automatique(request.centre_actif)
    agent = request.user
    tenue = services.entree_tenue_par(agent)
    if tenue is not None and tenue.file.service_id != service.pk:
        autre_service = tenue.file.service
        tenue = None
    else:
        autre_service = None

    jour = maintenant_centre(request.centre_actif).date()
    attente = list(services.file_en_attente(file))
    en_cours = (
        EntreeFile.objects.filter(file=file, statut__in=EntreeFile.STATUTS_TENUS)
        .select_related("passage__ticket", "agent")
        .order_by("date_premier_appel")
    )
    absents = (
        Passage.objects.filter(file=file, statut=Passage.Statut.ABSENT, ticket__jour=jour)
        .select_related("ticket")
        .order_by("date_arrivee")
    )

    contexte = {
        "service": service,
        "file": file,
        "tenue": tenue,
        "autre_service": autre_service,
        "attente": attente,
        "en_cours": en_cours,
        "absents": absents,
        "peut_appeler": a_droit(agent, "circuit.appeler", request.centre_actif, service),
        "peut_absent": a_droit(agent, "circuit.marquer_absent", request.centre_actif, service),
        "peut_reintegrer": a_droit(agent, "circuit.reintegrer", request.centre_actif, service),
        "peut_transferer": a_droit(agent, "circuit.transferer", request.centre_actif, service),
        "peut_annuler": a_droit(agent, "circuit.annuler_ticket", request.centre_actif, service),
        "peut_orienter": a_droit(agent, "circuit.orienter_demande", request.centre_actif, service),
        # Administrateur, responsables, direction : tickets « Autre » seulement.
        "appel_autres_seulement": appel_limite_aux_autres(agent, request.centre_actif),
        "salle_agent": getattr(affectation_active(agent, request.centre_actif), "salle", ""),
        "maintenant": timezone.now(),
        "indicateurs": _indicateurs_service(service, file, agent, jour),
        # Historique des derniers appels du service (aujourd'hui).
        "derniers_appels": list(
            Appel.objects.filter(service=service, ticket__jour=jour)
            .select_related("agent", "ticket")
            .order_by("-date", "-id")[:8]
        ),
        # Patients qui viendront dans ce service (vus à l'avance).
        "a_venir": list(tickets_a_venir(service)[:30]),
    }

    if voit_autres_seulement(agent, request.centre_actif):
        # Direction : jamais de patient à l'écran, seulement les demandes « Autre ».
        contexte["attente"] = [e for e in contexte["attente"] if e.passage.ticket.mode == Ticket.Mode.AUTRE]
        contexte["en_cours"] = [e for e in contexte["en_cours"] if e.passage.ticket.mode == Ticket.Mode.AUTRE]
        contexte["absents"] = [p for p in contexte["absents"] if p.ticket.mode == Ticket.Mode.AUTRE]
        contexte["a_venir"] = []
        # Filtre sur le mode du ticket, jamais sur la lettre du numéro.
        contexte["derniers_appels"] = [a for a in contexte["derniers_appels"] if a.ticket.mode == Ticket.Mode.AUTRE]

    if tenue is not None:
        passage = tenue.passage
        ticket = passage.ticket
        contexte.update(
            {
                "ticket": ticket,
                "transfert_recu": (
                    Transfert.objects.filter(passage_destination=passage)
                    .select_related("decide_par", "passage_origine").first()
                ),
                "suivante": services.etape_suivante(ticket, passage),
                # Durée de chaque passage déjà fait (décision du 21/11/2026).
                "passages_precedents": list(
                    ticket.passages.filter(date_fin__isnull=False).exclude(pk=passage.pk).order_by("date_arrivee")
                ),
                "transfert_possible": services.peut_transferer_pharmacie(passage),
                # Dernière étape : « Transférer à la pharmacie » en action principale
                # (décision du 23/11/2026) ; la fin du parcours devient secondaire.
                "pharmacie_principale": (
                    services.peut_transferer_pharmacie(passage)
                    and a_droit(agent, "circuit.transferer", request.centre_actif, service)
                    and services.etape_suivante(ticket, passage) is None
                ),
                # « Envoyer vers médecin en chef » (médecins, fin du parcours, 23/11/2026).
                "envoi_chef_possible": (
                    services.peut_envoyer_medecin_chef(passage)
                    and a_droit(agent, "circuit.transferer", request.centre_actif, service)
                ),
                "prochain_appel": tenue.prochain_appel_possible(),
                "appels_max": tenue.nb_appels >= file.nb_appels_avant_absence,
                "reservation": ticket.reservation_active(),
            }
        )
        # Accueil : état du quota (dentiste) avant la validation du ticket.
        if (
            service.est_accueil
            and ticket.parcours_id
            and ticket.parcours.soumis_quota
            and contexte["reservation"] is None
        ):
            contexte["etat_quota"] = services.etat_quota(ticket.parcours)
            contexte["derogation_autorisee"] = services.derogation_autorisee(ticket)
            contexte["peut_deroger"] = a_droit(agent, "parcours.deroger_quota", request.centre_actif, service)

    if service.est_accueil:
        from .impression import etat_imprimantes

        contexte["alertes_imprimante"] = etat_imprimantes(request.centre_actif)
        contexte["nb_demandes_nouvelles"] = DemandeAutre.objects.filter(
            centre=request.centre_actif, statut=DemandeAutre.Statut.NOUVELLE
        ).count()
        contexte["nb_reponses_non_lues"] = ReponseNotification.objects.filter(
            centre=request.centre_actif, lue_par_accueil=False,
            notification__demande__ticket__mode=Ticket.Mode.AUTRE,
        ).count()
    return contexte


@centre_actif_requis
def guichet(request, service_id):
    if _salle_a_indiquer(request):
        return _vers_ma_salle(request)
    service = _service_autorise(request, service_id)
    file, files = _file_du_service(request, service)
    contexte = _contexte_guichet(request, service, file)
    contexte["files"] = files
    return render(request, "circuit/guichet.html", contexte)


@centre_actif_requis
def guichet_liste(request, service_id):
    """Fragment HTMX rafraîchi périodiquement : file d'attente et absents."""
    service = _service_autorise(request, service_id)
    file, _ = _file_du_service(request, service)
    return render(request, "circuit/_liste_file.html", _contexte_guichet(request, service, file))


@centre_actif_requis
def guichet_zone(request, service_id):
    """Fragment HTMX : panneau « Patient en cours » (rafraîchi périodiquement)."""
    service = _service_autorise(request, service_id)
    file, _ = _file_du_service(request, service)
    contexte = _contexte_guichet(request, service, file)
    contexte["sans_messages"] = True   # les messages restent pour la page ou l'action suivante
    return render(request, "circuit/_zone_guichet.html", contexte)


@centre_actif_requis
def guichet_indicateurs(request, service_id):
    """Fragment HTMX : chiffres du jour du service (rafraîchis automatiquement)."""
    service = _service_autorise(request, service_id)
    file, _ = _file_du_service(request, service)
    jour = maintenant_centre(request.centre_actif).date()
    return render(
        request,
        "circuit/_indicateurs_guichet.html",
        {"service": service, "indicateurs": _indicateurs_service(service, file, request.user, jour)},
    )


def _reponse_guichet(request, service, file):
    if _est_htmx(request):
        reponse = render(request, "circuit/_zone_guichet.html", _contexte_guichet(request, service, file))
        # Les chiffres du service se mettent à jour aussitôt après l'action.
        reponse["HX-Trigger"] = "guichet-maj"
        return reponse
    url = reverse("circuit:guichet", args=[service.pk])
    return redirect(f"{url}?file={file.pk}")


def _entree_de_la_file(request, file):
    return get_object_or_404(
        EntreeFile.objects.filter(centre=request.centre_actif, file=file),
        pk=entier(request.POST.get("entree")),
    )


@require_POST
@centre_actif_requis
def guichet_action(request, service_id, action):
    service = _service_autorise(request, service_id)
    file, _ = _file_du_service(request, service)
    agent = request.user

    try:
        if action == "appeler":
            _exiger(request, "circuit.appeler", service)
            entree = None
            if request.POST.get("entree"):
                entree = _entree_de_la_file(request, file)
            # Salle indiquée par l'agent à sa connexion, sauf s'il en précise une autre.
            poste = request.POST.get("poste", "").strip()
            if not poste:
                affectation = affectation_active(agent, request.centre_actif)
                poste = affectation.salle if affectation is not None else ""
            appel = services.appeler(file, agent, entree=entree, poste=poste)
            messages.success(request, f"Ticket {appel.numero_ticket} appelé.")
        elif action == "rappeler":
            _exiger(request, "circuit.appeler", service)
            appel = services.rappeler(_entree_de_la_file(request, file), agent)
            messages.info(request, f"Ticket {appel.numero_ticket} rappelé ({appel.numero_appel}e appel).")
        elif action == "absent":
            _exiger(request, "circuit.marquer_absent", service)
            services.marquer_absent(_entree_de_la_file(request, file), agent)
            messages.warning(request, "Ticket marqué absent.")
        elif action == "prendre":
            _exiger(request, "circuit.appeler", service)
            services.prendre_en_charge(
                _entree_de_la_file(request, file), agent, oral=request.POST.get("oral") == "1"
            )
        elif action == "terminer":
            _exiger(request, "circuit.appeler", service)
            destination = request.POST.get("destination", "")
            transferer = request.POST.get("pharmacie") == "1" or destination == "pharmacie"
            vers_chef = destination == "medecin_chef"
            if transferer or vers_chef:
                _exiger(request, "circuit.transferer", service)
            derogation = request.POST.get("derogation") == "1"
            if derogation:
                _exiger(request, "parcours.deroger_quota", service)
            suite = services.terminer(
                _entree_de_la_file(request, file), agent,
                transferer_pharmacie=transferer, motif=request.POST.get("motif", ""),
                envoyer_medecin_chef=vers_chef,
                derogation=derogation, motif_derogation=request.POST.get("motif_derogation", ""),
            )
            if vers_chef:
                messages.success(request, "Prise en charge terminée : le médecin en chef a reçu le patient dans ses Notifications.")
            elif suite is None:
                messages.success(request, "Prise en charge terminée : fin du parcours.")
            elif suite.service.suspendu:
                messages.warning(request, (
                    f"Ticket orienté vers : {suite.nom_service}. Ce service est suspendu : "
                    "le patient attend dans sa file et sera appelé à la reprise."
                ))
            else:
                messages.success(request, f"Ticket orienté vers : {suite.nom_service}.")
        elif action == "reintegrer":
            _exiger(request, "circuit.reintegrer", service)
            passage = get_object_or_404(
                Passage.objects.filter(centre=request.centre_actif, file=file),
                pk=entier(request.POST.get("passage")),
            )
            services.reintegrer(passage, agent)
            messages.success(request, f"Ticket {passage.ticket.numero} réintégré en fin de file.")
        else:
            raise PermissionDenied
    except ErreurCircuit as erreur:
        messages.error(request, " ".join(erreur.messages))

    return _reponse_guichet(request, service, file)


# ---------------------------------------------------------------------------
# Tickets : détail, annulation, quota
# ---------------------------------------------------------------------------

def _ticket_du_centre(request, ticket_id):
    return objet_du_centre(request, Ticket, pk=ticket_id)


def _service_accueil_du_ticket(ticket):
    passage = ticket.passage_actif()
    return passage.service if passage is not None else None


@centre_actif_requis
def ticket_detail(request, ticket_id):
    # Un agent ne consulte que les patients passés par ses services.
    ticket = get_object_or_404(tickets_visibles(request.user, request.centre_actif), pk=ticket_id)
    centre = request.centre_actif
    service = _service_accueil_du_ticket(ticket)
    accueil = Service.objects.filter(centre=centre, est_accueil=True).first()
    return render(
        request,
        "circuit/ticket.html",
        {
            "ticket": ticket,
            "passages": ticket.passages.select_related("pris_en_charge_par", "termine_par"),
            "evenements": ticket.evenements.select_related("agent"),
            "reservations": ticket.reservations_quota.select_related("reservee_par", "liberee_par"),
            "transferts": ticket.transferts.select_related("decide_par", "service_destination"),
            "service_actuel": service,
            "peut_annuler": ticket.ouvert and a_droit(request.user, "circuit.annuler_ticket", centre, accueil),
            "peut_liberer": a_droit(request.user, "parcours.liberer_quota", centre, accueil)
            and ticket.reservations_quota.filter(statut=ReservationQuota.Statut.RESERVEE).exists(),
            # Dérogation au quota : le responsable l'autorise ici, l'accueil valide ensuite.
            "peut_autoriser_derogation": ticket.ouvert
            and ticket.parcours_id is not None
            and ticket.parcours.soumis_quota
            and ticket.reservation_active() is None
            and a_droit(request.user, "parcours.deroger_quota", centre, accueil),
            "derogation_autorisee": services.derogation_autorisee(ticket),
        },
    )


@require_POST
@centre_actif_requis
def ticket_action(request, ticket_id, action):
    ticket = _ticket_du_centre(request, ticket_id)
    centre = request.centre_actif
    accueil = Service.objects.filter(centre=centre, est_accueil=True).first()
    try:
        if action == "annuler":
            _exiger(request, "circuit.annuler_ticket", accueil)
            services.annuler_ticket(ticket, request.user, request.POST.get("motif", ""))
            messages.success(request, f"Ticket {ticket.numero} annulé.")
        elif action == "deroger":
            _exiger(request, "parcours.deroger_quota", accueil)
            services.autoriser_derogation(ticket, request.user, request.POST.get("motif", ""))
            messages.success(request, f"Dérogation autorisée : l'accueil peut valider le ticket {ticket.numero}.")
        elif action == "liberer":
            _exiger(request, "parcours.liberer_quota", accueil)
            services.liberer_quota(ticket, request.user, request.POST.get("motif", ""))
            messages.success(request, "Place de quota libérée.")
        else:
            raise PermissionDenied
    except ErreurCircuit as erreur:
        messages.error(request, " ".join(erreur.messages))
    return redirect("circuit:ticket", ticket.pk)


@centre_actif_requis
def tickets_du_jour(request):
    """Recherche d'un ticket du jour (accueil)."""
    centre = request.centre_actif
    services.cloture_automatique(centre)
    jour = maintenant_centre(centre).date()
    tickets = (
        tickets_visibles(request.user, centre)
        .filter(jour=jour)
        .select_related("parcours")
    )
    numero = request.GET.get("numero", "").strip().upper()
    if numero:
        tickets = tickets.filter(numero__icontains=numero)
    statut = request.GET.get("statut")
    if statut in Ticket.Statut.values:
        tickets = tickets.filter(statut=statut)
    return render(
        request,
        "circuit/tickets.html",
        {"tickets": tickets.order_by("-date_emission")[:200], "numero": numero, "statut": statut,
         "statuts": Ticket.Statut.choices, "jour": jour},
    )


# ---------------------------------------------------------------------------
# Mode Autre : demandes, notifications, réponses
# ---------------------------------------------------------------------------

def _peut_orienter(request):
    accueil = Service.objects.filter(centre=request.centre_actif, est_accueil=True).first()
    return a_droit(request.user, "circuit.orienter_demande", request.centre_actif, accueil)


@centre_actif_requis
def demandes(request):
    if not _peut_orienter(request):
        raise PermissionDenied
    centre = request.centre_actif
    jour = maintenant_centre(centre).date()

    if request.method == "POST":
        demande = get_object_or_404(DemandeAutre.objects.filter(centre=centre, ticket__mode=Ticket.Mode.AUTRE),
                                    pk=entier(request.POST.get("demande")))
        destinataire = service = None
        if request.POST.get("destinataire"):
            destinataire = get_object_or_404(
                Utilisateur.objects.filter(
                    Q(affectations__centre=centre, affectations__actif=True) | Q(is_superuser=True),
                    is_active=True,
                ).distinct(),
                pk=entier(request.POST["destinataire"]),
            )
        if request.POST.get("service"):
            service = get_object_or_404(Service.objects.filter(centre=centre, actif=True), pk=entier(request.POST["service"]))
        try:
            services.transmettre_demande(demande, request.user, destinataire=destinataire,
                                         service=service, message=request.POST.get("message", ""))
            messages.success(request, f"Demande {demande.ticket.numero} transmise.")
        except ErreurCircuit as erreur:
            messages.error(request, " ".join(erreur.messages))
        return redirect("circuit:demandes")

    liste = (
        # Seulement les demandes « Autre » : un patient envoyé par un médecin au
        # médecin en chef ne concerne pas l'accueil.
        DemandeAutre.objects.filter(centre=centre, ticket__mode=Ticket.Mode.AUTRE)
        .filter(Q(ticket__jour=jour) | ~Q(statut=DemandeAutre.Statut.CLOTUREE))
        .select_related("ticket", "motif")
        .prefetch_related("notifications__reponses", "notifications__destinataire")
        .order_by("statut", "-date_creation")
    )
    ReponseNotification.objects.filter(
        centre=centre, lue_par_accueil=False, notification__demande__ticket__mode=Ticket.Mode.AUTRE
    ).update(lue_par_accueil=True)
    personnes = (
        Utilisateur.objects.filter(affectations__centre=centre, affectations__actif=True, is_active=True)
        .distinct()
        .order_by("last_name", "first_name", "username")
    )
    return render(
        request,
        "circuit/demandes.html",
        {
            "demandes": liste,
            "personnes": personnes,
            "services_centre": Service.objects.filter(centre=centre, actif=True).order_by("ordre"),
        },
    )


@centre_actif_requis
def notifications(request):
    centre = request.centre_actif
    if request.method == "POST":
        notification = get_object_or_404(
            Notification.objects.filter(centre=centre, destinataire=request.user),
            pk=entier(request.POST.get("notification")),
        )
        try:
            services.repondre_notification(notification, request.user, request.POST.get("statut", ""),
                                           request.POST.get("message", ""))
            messages.success(request, "Réponse envoyée à l'accueil.")
        except ErreurCircuit as erreur:
            messages.error(request, " ".join(erreur.messages))
        return redirect("circuit:notifications")

    mes_notifications = (
        Notification.objects.filter(centre=centre, destinataire=request.user)
        .select_related("demande", "demande__ticket", "demande__motif", "transmise_par", "service")
        .prefetch_related("reponses")
        .order_by("-date")[:100]
    )
    Notification.objects.filter(
        centre=centre, destinataire=request.user, date_lecture__isnull=True
    ).update(date_lecture=timezone.now())
    return render(
        request,
        "circuit/notifications.html",
        {
            "notifications": mes_notifications,
            "statuts": ReponseNotification.Statut.choices,
            "aujourd_hui": maintenant_centre(centre).date(),
        },
    )


@require_POST
@centre_actif_requis
def notification_appeler(request, notification_id):
    """Le destinataire d'une demande « Autre » appelle le visiteur à l'écran."""
    notification = get_object_or_404(
        Notification.objects.filter(centre=request.centre_actif, destinataire=request.user), pk=notification_id
    )
    try:
        appel = services.appeler_visiteur(notification, request.user, request.POST.get("poste", ""))
        messages.success(request, f"{appel.numero_ticket} appelé à l'écran : « {appel.nom_service} ».")
    except ErreurCircuit as erreur:
        messages.error(request, " ".join(erreur.messages))
    return redirect("circuit:notifications")


@centre_actif_requis
def badge_notifications(request):
    nombre = Notification.objects.filter(
        centre=request.centre_actif, destinataire=request.user, date_lecture__isnull=True
    ).count()
    if not nombre:
        return HttpResponse("")
    return HttpResponse(f'<span class="pastille">{nombre}</span>')
