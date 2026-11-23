"""
Moteur du circuit des patients.

Toutes les opérations :
- sont transactionnelles (tout ou rien) ;
- verrouillent les lignes concernées (file, ticket, compteur, quota) ;
- enregistrent leur événement dans la même transaction ;
- sont attribuées à l'agent connecté.

Les contrôles de permission (rôle, centre, service) sont faits par les vues
avec `comptes.permissions.a_droit` ; ce module vérifie les règles métier.
"""

import datetime
from datetime import timedelta

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Max
from django.utils import timezone

from centres.models import FileAttente, Service
from core.timeutils import maintenant_centre
from parcours.models import Parcours, QuotaParcours
from parcours.quotas import periode_pour, quota_applicable

from .models import (
    Appel,
    DemandeAutre,
    EntreeFile,
    Evenement,
    Notification,
    Passage,
    ReponseNotification,
    ReservationQuota,
    Ticket,
    Transfert,
)

PREFIXE_AUTRE = "A"


class ErreurCircuit(ValidationError):
    """Règle métier non respectée (message destiné à l'agent)."""


def _evenement(ticket, type_, agent=None, passage=None, entree=None, **details):
    return Evenement.objects.create(
        centre_id=ticket.centre_id,
        ticket=ticket,
        passage=passage,
        entree=entree,
        type=type_,
        agent=agent,
        date=timezone.now(),
        details=details,
    )


# ---------------------------------------------------------------------------
# Émission des tickets (borne)
# ---------------------------------------------------------------------------

def prochain_numero(centre, prefixe, jour):
    """
    Incrémente atomiquement le compteur (centre, préfixe, jour).
    INSERT ... ON CONFLICT DO UPDATE : aucun doublon possible, même avec
    des émissions simultanées.
    """
    with connection.cursor() as curseur:
        curseur.execute(
            """
            INSERT INTO circuit_compteurticket (centre_id, prefixe, jour, dernier_numero)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (centre_id, prefixe, jour)
            DO UPDATE SET dernier_numero = circuit_compteurticket.dernier_numero + 1
            RETURNING dernier_numero
            """,
            [centre.pk, prefixe, jour],
        )
        return curseur.fetchone()[0]


CODE_FILE_AUTRE = "AUTRE"


def file_demandes_autre(accueil):
    """
    File de l'accueil réservée aux tickets « Autre » (décision du 22/11/2026) :
    les demandes « Autre » et les patients ne sont jamais dans la même file.
    Créée au premier besoin.
    """
    file, _ = FileAttente.objects.get_or_create(
        service=accueil,
        code=CODE_FILE_AUTRE,
        defaults={"centre_id": accueil.centre_id, "nom": "Demandes « Autre »", "par_defaut": False},
    )
    if not file.actif:
        raise ErreurCircuit("La file des demandes « Autre » de l'accueil n'est pas disponible.")
    return file


def _file_accueil(centre):
    accueil = (
        Service.objects.filter(centre=centre, est_accueil=True, actif=True)
        .order_by("ordre")
        .first()
    )
    if accueil is None:
        raise ErreurCircuit("Aucun service d'accueil actif n'est configuré.")
    file = accueil.file_par_defaut()
    if file is None or not file.actif:
        raise ErreurCircuit("La file de l'accueil n'est pas disponible.")
    return accueil, file


@transaction.atomic
def emettre_ticket(centre, mode, parcours=None, borne=None, motif=None, precision=""):
    """
    Émet un ticket depuis la borne.

    - Interne / Externe : un parcours visible est obligatoire ; le ticket
      suit la version publiée du parcours.
    - Autre : demande hors parcours de soins ; préfixe « A », une demande
      est créée et notifiée à l'accueil, qui l'oriente.

    Centre où l'accueil délivre les tickets (décision du 21/11/2026) : la
    borne est à l'accueil, le patient y prend son ticket et va directement
    au premier service de son parcours, sans file d'attente à l'accueil.
    La place de quota (dentiste) est réservée à ce moment ; quota atteint :
    pas de ticket (rien n'est enregistré). Sinon (borne libre-service), le
    patient passe d'abord par la file de l'accueil.
    """
    if mode not in Ticket.Mode.values:
        raise ErreurCircuit("Mode inconnu.")

    if borne is not None and (borne.centre_id != centre.pk or not borne.actif):
        raise ErreurCircuit("Borne non autorisée.")

    version = None

    if mode == Ticket.Mode.AUTRE:
        prefixe = PREFIXE_AUTRE
        parcours = None
        if motif is not None and motif.centre_id != centre.pk:
            raise ErreurCircuit("Motif inconnu.")
    else:
        if parcours is None:
            raise ErreurCircuit("Veuillez choisir un service.")
        if parcours.centre_id != centre.pk or not parcours.actif or not parcours.visible_borne:
            raise ErreurCircuit("Ce service n'est pas disponible.")
        if parcours.reserve_accueil and not centre.accueil_delivre_tickets:
            # Médecin en chef : jamais depuis une borne en libre-service (décision du 22/11/2026).
            raise ErreurCircuit("Ce service n'est pas disponible.")
        version = parcours.version_publiee()
        if version is None:
            raise ErreurCircuit("Ce parcours n'a pas de version publiée.")
        prefixe = parcours.prefixe

    accueil, file_accueil = _file_accueil(centre)
    if mode == Ticket.Mode.AUTRE:
        # Jamais dans la file des patients (décision du 22/11/2026).
        file_accueil = file_demandes_autre(accueil)

    maintenant = timezone.now()
    jour = maintenant_centre(centre).date()
    sequence = prochain_numero(centre, prefixe, jour)

    ticket = Ticket.objects.create(
        centre=centre,
        numero=f"{prefixe}{sequence:03d}",
        prefixe=prefixe,
        sequence=sequence,
        jour=jour,
        mode=mode,
        parcours=parcours,
        parcours_version=version,
        borne=borne,
        date_emission=maintenant,
    )
    _evenement(ticket, Evenement.Type.EMISSION, mode=mode,
               parcours=parcours.code if parcours else None,
               version=version.numero if version else None)

    if mode == Ticket.Mode.AUTRE:
        DemandeAutre.objects.create(
            centre=centre, ticket=ticket, motif=motif, precision=precision[:500]
        )

    if version is not None and centre.accueil_delivre_tickets:
        if parcours.soumis_quota:
            # Refus si le quota est atteint : la transaction est annulée,
            # aucun ticket ni numéro n'est consommé.
            _reserver_quota(ticket, None)
        etape = (
            version.etapes.select_related("service", "file")
            .filter(ordre__gt=1).order_by("ordre").first()
        )
        if etape is None:
            raise ErreurCircuit("Ce parcours n'a aucun service après l'accueil.")
        _creer_passage(ticket, etape.service, etape.file,
                       origine=Passage.Origine.PARCOURS, ordre_etape=etape.ordre)
    else:
        _creer_passage(
            ticket,
            accueil,
            file_accueil,
            origine=Passage.Origine.PARCOURS,
            ordre_etape=1 if version else None,
        )

    if borne is not None:
        borne.derniere_activite = maintenant
        borne.save(update_fields=["derniere_activite"])

    return ticket


# ---------------------------------------------------------------------------
# Passages et files
# ---------------------------------------------------------------------------

def _verrouiller_file(file_id):
    return FileAttente.objects.select_for_update(of=("self",)).select_related("service", "centre").get(pk=file_id)


def _ajouter_en_file(passage, file, reintegration=False, agent=None):
    _verrouiller_file(file.pk)
    rang = (EntreeFile.objects.filter(file=file).aggregate(m=Max("rang"))["m"] or 0) + 1
    entree = EntreeFile.objects.create(
        centre_id=passage.centre_id,
        passage=passage,
        file=file,
        rang=rang,
        reintegration=reintegration,
        date_entree=timezone.now(),
    )
    _evenement(
        passage.ticket,
        Evenement.Type.REINTEGRATION if reintegration else Evenement.Type.ENTREE_FILE,
        agent=agent,
        passage=passage,
        entree=entree,
        file=file.nom,
        rang=rang,
    )
    return entree


def _creer_passage(ticket, service, file, origine, ordre_etape=None, attente_si_suspendu=False):
    """
    `attente_si_suspendu` : étape suivante du parcours d'un service suspendu.
    Le patient est mis en attente dans sa file et sera appelé à la reprise ;
    l'agent précédent n'est pas bloqué (décision du 23/11/2026). Un service
    ou une file désactivés restent refusés.
    """
    if service.centre_id != ticket.centre_id or file.service_id != service.pk:
        raise ErreurCircuit("Service ou file incohérent avec le centre du ticket.")
    if not service.actif or not file.actif or (service.suspendu and not attente_si_suspendu):
        raise ErreurCircuit(f"Le service {service.nom} n'est pas disponible.")

    passage = Passage.objects.create(
        centre_id=ticket.centre_id,
        ticket=ticket,
        service=service,
        file=file,
        nom_service=service.nom,
        nom_file=file.nom,
        origine=origine,
        ordre_etape=ordre_etape,
        date_arrivee=timezone.now(),
    )
    _ajouter_en_file(passage, file)
    return passage


def _verrouiller_entree(entree_id):
    """Verrouille l'entrée, son passage et son ticket (ordre fixe)."""
    entree = EntreeFile.objects.select_related("file", "file__service").get(pk=entree_id)
    _verrouiller_file(entree.file_id)
    entree = (
        EntreeFile.objects.select_for_update(of=("self",))
        .select_related("file", "file__service", "passage", "passage__ticket")
        .get(pk=entree_id)
    )
    Ticket.objects.select_for_update().get(pk=entree.passage.ticket_id)
    return entree


def entree_tenue_par(agent):
    return (
        EntreeFile.objects.filter(agent=agent, statut__in=EntreeFile.STATUTS_TENUS)
        .select_related("passage", "passage__ticket", "file", "file__service")
        .first()
    )


def _verrouiller_agent(agent):
    """
    Verrou sur le compte de l'agent : deux appels simultanés du même agent
    (deux onglets, deux files) passent l'un après l'autre. Le second voit le
    patient pris par le premier et reçoit un message clair, au lieu d'une
    erreur de contrainte en base (erreur 500).
    """
    from comptes.models import Utilisateur

    # « FOR NO KEY UPDATE » : bloque un second appel du même agent, mais pas
    # les écritures qui référencent simplement l'agent (historique d'une
    # annulation en cours…). Un verrou complet provoquait un interblocage.
    Utilisateur.objects.select_for_update(no_key=True).filter(pk=agent.pk).first()


def _verrouiller_files_du_ticket(ticket_id):
    """
    Ordre des verrous commun à tout le moteur : files, puis entrées, puis
    ticket. Annulation et clôture verrouillent donc d'abord les files où le
    ticket est présent, pour ne jamais se bloquer mutuellement avec un appel.
    """
    files = (
        EntreeFile.objects.filter(passage__ticket_id=ticket_id, statut__in=["EN_ATTENTE", "APPELE", "EN_COURS"])
        .values_list("file_id", flat=True)
    )
    for file_id in sorted(set(files)):
        _verrouiller_file(file_id)


def _verifier_agent_libre(agent, sauf=None):
    tenue = entree_tenue_par(agent)
    if tenue is not None and tenue.pk != getattr(sauf, "pk", None):
        raise ErreurCircuit(
            f"Vous avez déjà le ticket {tenue.passage.ticket.numero} en cours. "
            "Terminez-le avant d'appeler un autre patient."
        )


def _verifier_capacite(file):
    tenues = EntreeFile.objects.filter(file=file, statut__in=EntreeFile.STATUTS_TENUS).count()
    if tenues >= file.nb_appels_simultanes:
        raise ErreurCircuit(
            "Le nombre maximal de patients appelés simultanément dans cette file est atteint."
        )


def file_en_attente(file):
    return (
        EntreeFile.objects.filter(file=file, statut=EntreeFile.Statut.EN_ATTENTE)
        .select_related("passage", "passage__ticket", "passage__ticket__parcours")
        .order_by("rang")
    )


def _enregistrer_appel(entree, agent, mode=Appel.Mode.ECRAN):
    maintenant = timezone.now()
    entree.nb_appels += 1
    entree.date_dernier_appel = maintenant
    if entree.date_premier_appel is None:
        entree.date_premier_appel = maintenant
    entree.save(update_fields=["nb_appels", "date_dernier_appel", "date_premier_appel",
                               "statut", "agent", "poste"])
    passage = entree.passage
    ticket = passage.ticket
    appel = Appel.objects.create(
        centre_id=entree.centre_id,
        entree=entree,
        ticket=ticket,
        service=entree.file.service,
        numero_ticket=ticket.numero,
        nom_service=entree.file.service.nom,
        poste=entree.poste,
        numero_appel=entree.nb_appels,
        mode=mode,
        agent=agent,
        date=maintenant,
    )
    _evenement(
        ticket,
        Evenement.Type.APPEL if entree.nb_appels == 1 else Evenement.Type.RAPPEL,
        agent=agent, passage=passage, entree=entree,
        numero_appel=entree.nb_appels, mode=mode, poste=entree.poste,
    )
    return appel


# Décision du 19/11/2026 : TOUT PASSE D'ABORD PAR L'ACCUEIL. Seul l'accueil
# appelle et oriente les tickets qui l'attendent, y compris les « Autre » ;
# un service n'appelle que les patients que l'accueil lui a orientés.
# Administrateurs, responsables et direction n'appellent donc aucun ticket :
# les demandes « Autre » qui les concernent leur sont transmises par
# l'accueil (Notifications).
MESSAGE_AUTRES_SEULEMENT = (
    "Tout passe d'abord par l'accueil : seul l'accueil appelle et oriente les tickets, "
    "y compris les « Autre ». Les demandes qui vous concernent arrivent dans vos Notifications."
)


def _appel_limite_aux_autres(agent, file):
    from comptes.permissions import appel_limite_aux_autres

    return appel_limite_aux_autres(agent, file.centre)


MESSAGE_CHEF_SANS_APPEL = (
    "Le médecin en chef n'appelle pas à l'écran : les patients qui lui sont envoyés "
    "arrivent dans ses Notifications, où il les reçoit."
)


@transaction.atomic
def appeler(file, agent, entree=None, poste=""):
    """
    Appelle le patient suivant (ou `entree` si la file n'est pas en ordre
    strict). Verrou par file : deux agents ne peuvent pas appeler le même
    patient. Dans la file commune des médecins, le patient est attribué
    au médecin qui l'appelle.
    """
    _verrouiller_agent(agent)
    file = _verrouiller_file(file.pk)
    if not file.actif or not file.service.disponible:
        raise ErreurCircuit("Cette file n'est pas disponible.")
    if file.service.est_medecin_chef:
        raise ErreurCircuit(MESSAGE_CHEF_SANS_APPEL)

    _verifier_agent_libre(agent)
    _verifier_capacite(file)

    attente = file_en_attente(file)
    if _appel_limite_aux_autres(agent, file):
        # Administrateurs, responsables, direction : aucun appel (voir ci-dessus).
        raise ErreurCircuit(MESSAGE_AUTRES_SEULEMENT)
    premier = attente.first()
    if premier is None:
        raise ErreurCircuit("Aucun patient en attente.")

    if entree is None:
        cible = premier
    else:
        cible = attente.filter(pk=entree.pk).first()
        if cible is None:
            raise ErreurCircuit("Ce patient n'est plus en attente dans cette file.")
        if file.ordre_strict and cible.pk != premier.pk:
            raise ErreurCircuit("Ordre strict : seul le premier patient de la file peut être appelé.")

    # Relecture sous verrou : une annulation ou la clôture a pu fermer le
    # ticket entre la lecture de la file et ce verrou (audit du 20/11/2026).
    cible = EntreeFile.objects.select_for_update(of=("self",)).select_related(
        "file", "file__service", "passage", "passage__ticket"
    ).filter(pk=cible.pk, statut=EntreeFile.Statut.EN_ATTENTE).first()
    if cible is None:
        raise ErreurCircuit("Ce patient n'est plus en attente dans cette file.")
    ticket = Ticket.objects.select_for_update().get(pk=cible.passage.ticket_id)
    if ticket.statut not in Ticket.STATUTS_OUVERTS:
        raise ErreurCircuit("Ce ticket est fermé (annulé ou clôturé).")

    cible.statut = EntreeFile.Statut.APPELE
    cible.agent = agent
    cible.poste = (poste or "")[:50]
    appel = _enregistrer_appel(cible, agent)

    passage = cible.passage
    passage.statut = Passage.Statut.APPELE
    passage.save(update_fields=["statut"])
    return appel


@transaction.atomic
def rappeler(entree, agent):
    entree = _verrouiller_entree(entree.pk)
    if entree.statut != EntreeFile.Statut.APPELE or entree.agent_id != agent.pk:
        raise ErreurCircuit("Ce patient n'est pas appelé par vous.")
    if entree.nb_appels >= entree.file.nb_appels_avant_absence:
        raise ErreurCircuit("Nombre maximal d'appels atteint : marquez le patient absent.")
    prochain = entree.prochain_appel_possible()
    if prochain and timezone.now() < prochain:
        secondes = int((prochain - timezone.now()).total_seconds()) + 1
        raise ErreurCircuit(f"Veuillez patienter {secondes} s avant de rappeler.")
    return _enregistrer_appel(entree, agent)


@transaction.atomic
def marquer_absent(entree, agent):
    """Absence seulement après N appels sans réponse, espacés du délai paramétré."""
    entree = _verrouiller_entree(entree.pk)
    if entree.statut != EntreeFile.Statut.APPELE or entree.agent_id != agent.pk:
        raise ErreurCircuit("Ce patient n'est pas appelé par vous.")
    if entree.nb_appels < entree.file.nb_appels_avant_absence:
        raise ErreurCircuit(
            f"Le patient doit être appelé {entree.file.nb_appels_avant_absence} fois "
            "avant d'être déclaré absent."
        )
    prochain = entree.prochain_appel_possible()
    if prochain and timezone.now() < prochain:
        secondes = int((prochain - timezone.now()).total_seconds()) + 1
        raise ErreurCircuit(f"Veuillez patienter {secondes} s après le dernier appel.")

    entree.statut = EntreeFile.Statut.ABSENT
    entree.date_absence = timezone.now()
    entree.agent = None
    entree.save(update_fields=["statut", "date_absence", "agent"])

    passage = entree.passage
    passage.statut = Passage.Statut.ABSENT
    passage.save(update_fields=["statut"])

    _evenement(passage.ticket, Evenement.Type.ABSENCE, agent=agent, passage=passage,
               entree=entree, nb_appels=entree.nb_appels)
    return entree


@transaction.atomic
def reintegrer(passage, agent):
    """Nouvelle entrée en fin de file pour le même passage (maximum paramétré)."""
    # Ordre commun des verrous : la file, puis le ticket, puis le passage
    # (comme annuler_ticket et cloturer_tickets : ticket, puis ses passages).
    file_id, ticket_id = Passage.objects.values_list("file_id", "ticket_id").get(pk=passage.pk)
    _verrouiller_file(file_id)
    Ticket.objects.select_for_update().get(pk=ticket_id)
    passage = Passage.objects.select_for_update(of=("self",)).select_related("ticket", "file", "centre").get(pk=passage.pk)
    if passage.statut != Passage.Statut.ABSENT:
        raise ErreurCircuit("Seul un patient absent peut être réintégré.")
    if _appel_limite_aux_autres(agent, passage.file):
        # Administrateurs, responsables, direction : pas de gestion des patients.
        raise ErreurCircuit(MESSAGE_AUTRES_SEULEMENT)
    deja = passage.entrees.filter(reintegration=True).count()
    if deja >= passage.centre.max_reintegrations:
        raise ErreurCircuit("Nombre maximal de réintégrations atteint pour ce passage.")
    if not passage.file.actif:
        raise ErreurCircuit("La file n'est plus disponible.")

    passage.statut = Passage.Statut.EN_ATTENTE
    passage.save(update_fields=["statut"])
    return _ajouter_en_file(passage, passage.file, reintegration=True, agent=agent)


def _consommer_quota(ticket, passage, agent=None):
    """
    Prise en charge dans le service soumis au quota (dentiste) : la place
    réservée par l'accueil est consommée.

    Place libérée entre-temps (patient déclaré absent, puis revenu) : le
    patient reprend une place si le quota en a encore une ; sinon la prise
    en charge est refusée, et une dérogation (accueil ou responsable) est
    nécessaire. Un patient n'est jamais soigné sans place décomptée
    (décision du 20/11/2026).
    """
    if passage.origine != Passage.Origine.PARCOURS or passage.service.est_accueil:
        return
    reservation = ticket.reservation_active()
    if reservation is None:
        if not (ticket.parcours_id and ticket.parcours.soumis_quota):
            return
        if not ticket.reservations_quota.filter(statut=ReservationQuota.Statut.LIBEREE).exists():
            return
        try:
            reservation = _reserver_quota(ticket, agent)
        except ErreurCircuit as erreur:
            raise ErreurCircuit(
                "La place de ce patient a été libérée et ne peut pas être reprise : "
                f"{' '.join(erreur.messages)} Une dérogation est nécessaire "
                "(accueil ou responsable, depuis la page du ticket)."
            ) from erreur
    if reservation.statut == ReservationQuota.Statut.RESERVEE:
        reservation.statut = ReservationQuota.Statut.CONSOMMEE
        reservation.save(update_fields=["statut"])


@transaction.atomic
def prendre_en_charge(entree, agent, oral=False):
    """
    Début de prise en charge. Pour un service autorisant l'appel oral
    (pharmacie), l'agent peut prendre en charge directement un patient en
    attente : un appel « oral » est enregistré. Le médecin en chef reçoit
    toujours ainsi ses patients, sans appel à l'écran (décision du 22/11/2026).
    """
    _verrouiller_agent(agent)
    # Quota du dentiste : le parcours est verrouillé avant la file, dans le
    # même ordre que emettre_ticket et terminer (parcours, puis file), pour
    # ne pas se bloquer mutuellement quand une place libérée est reprise.
    parcours_quota = (
        EntreeFile.objects.filter(
            pk=entree.pk, passage__ticket__parcours__soumis_quota=True,
            passage__service__est_accueil=False,
        ).values_list("passage__ticket__parcours_id", flat=True).first()
    )
    if parcours_quota is not None:
        Parcours.objects.select_for_update().filter(pk=parcours_quota).first()
    entree = _verrouiller_entree(entree.pk)
    service = entree.file.service
    passage = entree.passage
    ticket = passage.ticket

    if entree.statut == EntreeFile.Statut.EN_ATTENTE:
        if not (oral and (service.appel_oral_autorise or service.est_medecin_chef)):
            raise ErreurCircuit("Appelez d'abord le patient.")
        if not entree.file.actif or not service.disponible:
            raise ErreurCircuit("Cette file n'est pas disponible.")
        if _appel_limite_aux_autres(agent, entree.file):
            raise ErreurCircuit(MESSAGE_AUTRES_SEULEMENT)
        _verifier_agent_libre(agent)
        _verifier_capacite(entree.file)
        if entree.file.ordre_strict:
            premier = file_en_attente(entree.file).first()
            if premier is not None and premier.pk != entree.pk:
                raise ErreurCircuit("Ordre strict : prenez en charge le premier patient.")
        entree.statut = EntreeFile.Statut.APPELE
        entree.agent = agent
        _enregistrer_appel(entree, agent, mode=Appel.Mode.ORAL)
    elif entree.statut != EntreeFile.Statut.APPELE or entree.agent_id != agent.pk:
        raise ErreurCircuit("Ce patient n'est pas appelé par vous.")

    maintenant = timezone.now()
    entree.statut = EntreeFile.Statut.EN_COURS
    entree.date_debut = maintenant
    entree.save(update_fields=["statut", "date_debut"])

    passage.statut = Passage.Statut.EN_COURS
    if passage.date_debut is None:
        passage.date_debut = maintenant
    passage.pris_en_charge_par = agent
    passage.save(update_fields=["statut", "date_debut", "pris_en_charge_par"])

    if ticket.statut == Ticket.Statut.EMIS:
        ticket.statut = Ticket.Statut.EN_COURS
        ticket.save(update_fields=["statut"])

    _consommer_quota(ticket, passage, agent)
    _evenement(ticket, Evenement.Type.PRISE_EN_CHARGE, agent=agent, passage=passage, entree=entree)
    return entree


def _terminer_ticket(ticket, agent, type_=Evenement.Type.FIN_TICKET, statut=Ticket.Statut.TERMINE, **details):
    ticket.statut = statut
    ticket.date_fin = timezone.now()
    ticket.save(update_fields=["statut", "date_fin"])
    _evenement(ticket, type_, agent=agent, **details)


def etape_suivante(ticket, passage):
    if ticket.parcours_version_id is None or passage.ordre_etape is None:
        return None
    return (
        ticket.parcours_version.etapes.select_related("service", "file")
        .filter(ordre__gt=passage.ordre_etape)
        .order_by("ordre")
        .first()
    )


def service_pharmacie(centre):
    return (
        Service.objects.filter(centre=centre, est_pharmacie=True, actif=True, accepte_transferts=True)
        .order_by("ordre")
        .first()
    )


def peut_transferer_pharmacie(passage):
    """
    Tout service de soins peut décider explicitement d'envoyer le patient à
    la pharmacie (décision du 20/11/2026), y compris depuis une étape
    intermédiaire (ex. infirmerie du parcours M) : un motif est alors
    obligatoire et les étapes restantes ne sont pas réalisées. Jamais depuis
    l'accueil (décision du 18/11/2026) ni pour un ticket « Autre ».
    """
    service = passage.service
    if service.est_accueil or service.est_pharmacie:
        return False
    if passage.ticket.est_autre:
        return False
    return service_pharmacie(passage.centre) is not None


def service_medecin_chef(centre):
    return (
        Service.objects.filter(centre=centre, est_medecin_chef=True, actif=True)
        .order_by("ordre")
        .first()
    )


def peut_transferer_medecin_chef(passage):
    """
    Seuls les médecins (services cochés « Peut envoyer au médecin en chef »)
    envoient un patient au médecin en chef (décision du 22/11/2026). L'accueil
    y accède par la borne qu'il tient. Jamais pour un ticket « Autre ».
    """
    service = passage.service
    if not service.envoie_medecin_chef or service.est_medecin_chef:
        return False
    if passage.ticket.est_autre:
        return False
    return service_medecin_chef(passage.centre) is not None


@transaction.atomic
def terminer(entree, agent, transferer_pharmacie=False, motif="", derogation=False, motif_derogation="",
             transferer_medecin_chef=False):
    """
    Fin de prise en charge.

    - À l'accueil : valide le ticket et l'oriente vers son service. Aucune
      information patient n'est collectée (décision du 18/11/2026). Pour un
      parcours soumis à quota (dentiste), la place est réservée à cette
      validation ; si le quota est atteint, seule une dérogation (motif
      obligatoire) le permet, sinon rien n'est modifié.

    - Sans transfert : le patient passe à l'étape suivante du parcours,
      sinon le ticket se termine.
    - Avec transfert (décision explicite du service) : un passage pharmacie
      est créé ; le ticket se termine après la pharmacie.
    - Vers le médecin en chef (médecins seulement, décision du 22/11/2026) :
      un passage est créé dans sa file ; il termine ensuite le ticket ou
      l'envoie à la pharmacie. Depuis une étape
      intermédiaire, un motif est obligatoire et les étapes restantes ne
      sont pas réalisées (décision du 20/11/2026).
    """
    entree = _verrouiller_entree(entree.pk)
    if entree.statut != EntreeFile.Statut.EN_COURS or entree.agent_id != agent.pk:
        raise ErreurCircuit("Ce patient n'est pas pris en charge par vous.")

    passage = Passage.objects.select_for_update(of=("self",)).select_related(
        "ticket", "ticket__parcours_version", "service", "centre"
    ).get(pk=entree.passage_id)
    ticket = passage.ticket

    # Accueil : réservation de la place de quota à la validation du ticket.
    # Faite en premier : si elle échoue, rien n'est modifié (tout ou rien).
    if (
        passage.service.est_accueil
        and not ticket.est_autre
        and ticket.parcours_id
        and ticket.parcours.soumis_quota
        and ticket.reservation_active() is None
    ):
        _reserver_quota(ticket, agent, derogation, motif_derogation or "")

    # Mode Autre : le ticket est clôturé après l'orientation de la demande ;
    # la demande reste ensuite suivie indépendamment (notifications, réponses).
    if ticket.est_autre and passage.service.est_accueil:
        demande = DemandeAutre.objects.filter(ticket=ticket).first()
        if demande is not None and demande.statut == DemandeAutre.Statut.NOUVELLE:
            raise ErreurCircuit(
                "Orientez d'abord la demande (personne ou service) avant de clôturer le ticket."
            )

    suivante = etape_suivante(ticket, passage)
    motif = (motif or "").strip()

    if transferer_pharmacie and transferer_medecin_chef:
        raise ErreurCircuit("Choisissez un seul service de destination.")
    if transferer_pharmacie and not peut_transferer_pharmacie(passage):
        raise ErreurCircuit("Le transfert vers la pharmacie n'est pas possible depuis ce service.")
    if transferer_medecin_chef and not peut_transferer_medecin_chef(passage):
        raise ErreurCircuit("L'envoi au médecin en chef n'est pas possible depuis ce service.")
    if (transferer_pharmacie or transferer_medecin_chef) and suivante is not None and not motif:
        raise ErreurCircuit(
            f"Transfert avant {suivante.service.nom} : indiquez un motif "
            "(les étapes restantes du parcours ne seront pas réalisées)."
        )

    maintenant = timezone.now()
    entree.statut = EntreeFile.Statut.TERMINE
    entree.date_fin = maintenant
    entree.save(update_fields=["statut", "date_fin"])

    passage.statut = Passage.Statut.TERMINE
    passage.date_fin = maintenant
    passage.termine_par = agent
    passage.save(update_fields=["statut", "date_fin", "termine_par"])
    _evenement(ticket, Evenement.Type.FIN_PRISE_EN_CHARGE, agent=agent, passage=passage, entree=entree)

    if transferer_pharmacie or transferer_medecin_chef:
        destination = (service_pharmacie(passage.centre) if transferer_pharmacie
                       else service_medecin_chef(passage.centre))
        file_destination = destination.file_par_defaut()
        if file_destination is None:
            raise ErreurCircuit(f"Le service {destination.nom} n'a pas de file d'attente active.")
        nouveau = _creer_passage(ticket, destination, file_destination,
                                 origine=Passage.Origine.TRANSFERT)
        Transfert.objects.create(
            centre_id=ticket.centre_id,
            ticket=ticket,
            passage_origine=passage,
            passage_destination=nouveau,
            service_destination=destination,
            motif=motif,
            decide_par=agent,
            date=maintenant,
        )
        _evenement(ticket, Evenement.Type.TRANSFERT, agent=agent, passage=passage,
                   vers=destination.nom, motif=motif,
                   etapes_non_realisees=bool(suivante))
        return nouveau

    if suivante is not None and passage.origine == Passage.Origine.PARCOURS:
        return _creer_passage(ticket, suivante.service, suivante.file,
                              origine=Passage.Origine.PARCOURS, ordre_etape=suivante.ordre,
                              attente_si_suspendu=True)

    _terminer_ticket(ticket, agent)
    return None


# ---------------------------------------------------------------------------
# Accueil : quotas, annulation
# ---------------------------------------------------------------------------

def etat_quota(parcours, date_heure=None):
    """Retourne (quota, réservées, restantes) pour la période en cours."""
    quota = quota_applicable(parcours, date_heure)
    jour, _, periode = periode_pour(parcours.centre, date_heure)
    reservees = ReservationQuota.objects.filter(
        parcours=parcours, jour=jour, periode=periode,
        statut__in=ReservationQuota.STATUTS_OCCUPANT,
    ).count()
    if quota is None:
        return None, reservees, 0
    return quota, reservees, max(quota.nombre_places - reservees, 0)


def _reserver_quota(ticket, agent, derogation=False, motif_derogation=""):
    parcours = Parcours.objects.select_for_update().get(pk=ticket.parcours_id)
    quota, reservees, restantes = etat_quota(parcours)
    jour, _, periode = periode_pour(parcours.centre)

    if quota is None:
        raise ErreurCircuit(
            f"Le service {parcours.libelle_borne} est fermé pendant cette période : "
            "aucun ticket ne peut lui être orienté."
        )

    if restantes <= 0 and not derogation:
        # Dérogation accordée à l'avance par un responsable depuis la page du
        # ticket (le responsable n'appelle pas les patients).
        autorisation = derogation_autorisee(ticket)
        if autorisation is not None:
            derogation = True
            motif_derogation = (
                f"{autorisation.details.get('motif', '')} (autorisée par {autorisation.agent})"
            )

    if restantes <= 0:
        if not derogation:
            raise ErreurCircuit(
                f"Quota atteint pour {parcours.libelle_borne} "
                f"({quota.nombre_places} places, période {QuotaParcours.Periode(periode).label.lower()})."
            )
        if not motif_derogation.strip():
            raise ErreurCircuit("Une dérogation exige un motif.")
    else:
        derogation = False
        motif_derogation = ""

    reservation = ReservationQuota.objects.create(
        centre_id=ticket.centre_id,
        ticket=ticket,
        parcours=parcours,
        quota=quota,
        jour=jour,
        periode=periode,
        derogation=derogation,
        motif_derogation=motif_derogation.strip(),
        reservee_par=agent,
        date_reservation=timezone.now(),
    )
    _evenement(
        ticket,
        Evenement.Type.DEROGATION_QUOTA if derogation else Evenement.Type.RESERVATION_QUOTA,
        agent=agent,
        jour=str(jour), periode=periode, places=quota.nombre_places,
        reservees_avant=reservees, motif=motif_derogation.strip(),
    )
    return reservation


def derogation_autorisee(ticket):
    """
    Autorisation de dérogation encore inutilisée pour ce ticket, ou None.
    Elle ne sert qu'une fois : dès qu'une place est réservée après elle, elle
    est consommée. Une place libérée puis reprise au-delà du quota exige une
    nouvelle décision (relecture du 23/11/2026).
    """
    autorisation = (
        ticket.evenements.filter(type=Evenement.Type.DEROGATION_AUTORISEE)
        .select_related("agent")
        .order_by("-date", "-id")
        .first()
    )
    if autorisation is None:
        return None
    if ticket.reservations_quota.filter(date_reservation__gt=autorisation.date).exists():
        return None
    return autorisation


@transaction.atomic
def autoriser_derogation(ticket, agent, motif):
    """
    Un responsable autorise, avec un motif, la validation d'un ticket au-delà
    du quota. L'agent d'accueil valide ensuite le ticket normalement ; la
    réservation est alors marquée « dérogation ». Le responsable n'appelle
    jamais le patient (décision du 19/11/2026).
    """
    motif = (motif or "").strip()
    if not motif:
        raise ErreurCircuit("Le motif de la dérogation est obligatoire.")
    ticket = Ticket.objects.select_for_update(of=("self",)).select_related("parcours").get(pk=ticket.pk)
    if not ticket.ouvert or not ticket.parcours_id or not ticket.parcours.soumis_quota:
        raise ErreurCircuit("Ce ticket n'est pas concerné par un quota.")
    if ticket.reservation_active() is not None:
        raise ErreurCircuit("Une place est déjà réservée pour ce ticket.")
    _evenement(ticket, Evenement.Type.DEROGATION_AUTORISEE, agent=agent, motif=motif)


@transaction.atomic
def liberer_quota(ticket, agent, motif):
    """Libère la place réservée d'un ticket dont le patient ne s'est pas présenté."""
    motif = (motif or "").strip()
    if not motif:
        raise ErreurCircuit("Le motif de libération est obligatoire.")
    Ticket.objects.select_for_update().get(pk=ticket.pk)
    reservation = (
        ReservationQuota.objects.select_for_update(of=("self",))
        .filter(ticket=ticket, statut=ReservationQuota.Statut.RESERVEE)
        .first()
    )
    if reservation is None:
        raise ErreurCircuit("Aucune place réservée (non consommée) pour ce ticket.")
    reservation.statut = ReservationQuota.Statut.LIBEREE
    reservation.liberee_par = agent
    reservation.motif_liberation = motif
    reservation.date_liberation = timezone.now()
    reservation.save(update_fields=["statut", "liberee_par", "motif_liberation", "date_liberation"])
    _evenement(ticket, Evenement.Type.LIBERATION_QUOTA, agent=agent, motif=motif,
               jour=str(reservation.jour), periode=reservation.periode)
    return reservation


def _fermer_passages(ticket):
    maintenant = timezone.now()
    for passage in ticket.passages.select_for_update().filter(statut__in=Passage.STATUTS_ACTIFS):
        passage.entrees.filter(
            statut__in=["EN_ATTENTE", "APPELE", "EN_COURS"]
        ).update(statut=EntreeFile.Statut.ANNULE, date_fin=maintenant, agent=None)
        passage.statut = Passage.Statut.ANNULE
        passage.date_fin = maintenant
        passage.save(update_fields=["statut", "date_fin"])
    ticket.reservations_quota.filter(statut=ReservationQuota.Statut.RESERVEE).update(
        statut=ReservationQuota.Statut.LIBEREE,
        motif_liberation="Ticket annulé ou clôturé",
        date_liberation=maintenant,
    )


@transaction.atomic
def annuler_ticket(ticket, agent, motif):
    motif = (motif or "").strip()
    if not motif:
        raise ErreurCircuit("Le motif d'annulation est obligatoire.")
    _verrouiller_files_du_ticket(ticket.pk)
    ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
    if not ticket.ouvert:
        raise ErreurCircuit("Ce ticket n'est plus ouvert.")
    _fermer_passages(ticket)
    ticket.motif_annulation = motif
    ticket.annule_par = agent
    ticket.save(update_fields=["motif_annulation", "annule_par"])
    _terminer_ticket(ticket, agent, Evenement.Type.ANNULATION, Ticket.Statut.ANNULE, motif=motif)
    return ticket


def _heure_cloture(centre):
    """
    Heure de clôture dans la journée, ou None. 00:00 vaut « à minuit » (le
    changement de jour) : sinon, toute heure étant ≥ 00:00, la borne serait
    fermée en permanence et chaque ticket clôturé aussitôt (relecture du 23/11/2026).
    """
    heure = centre.heure_cloture_tickets
    return heure if heure and heure != datetime.time(0, 0) else None


def _jour_limite_cloture(centre):
    """Les tickets ouverts d'un jour antérieur à celui-ci sont à clôturer."""
    maintenant = maintenant_centre(centre)
    jour = maintenant.date()
    heure = _heure_cloture(centre)
    if heure and maintenant.time() >= heure:
        jour = jour + timedelta(days=1)
    return jour


def emission_terminee(centre):
    """
    Après l'heure de clôture du centre, la borne ne délivre plus de ticket :
    il serait clôturé dans la minute (audit du 20/11/2026). Elle rouvre à
    minuit, au changement de jour.
    """
    heure = _heure_cloture(centre)
    return bool(heure) and maintenant_centre(centre).time() >= heure


INTERVALLE_CLOTURE_AUTOMATIQUE = 60  # secondes


def cloture_automatique(centre):
    """
    Clôture sans intervention : appelée au fil des pages (borne, guichets,
    écran), au plus une fois par minute et par centre. Les tickets de la
    veille ne restent donc jamais dans les files, même si la tâche planifiée
    du serveur (cloturer_tickets) n'a pas tourné. Retourne le nombre de
    tickets clôturés.
    """
    if centre is None:
        return 0
    cle = f"circuit:cloture_automatique:{centre.pk}"
    if not cache.add(cle, True, INTERVALLE_CLOTURE_AUTOMATIQUE):
        return 0
    if not Ticket.objects.filter(
        centre=centre, statut__in=Ticket.STATUTS_OUVERTS, jour__lt=_jour_limite_cloture(centre)
    ).exists():
        return 0
    return cloturer_tickets(centre)


@transaction.atomic
def cloturer_tickets(centre, avant_jour=None):
    """
    Clôture automatique des tickets restés ouverts (décision du 18/11/2026 :
    23 h 59 depuis le 21/11/2026, heure réglable par centre).

    Sont clôturés : les tickets des jours précédents et, une fois l'heure de
    clôture du centre passée, ceux du jour. Les passages en cours sont
    annulés ; les horodatages existants sont conservés.
    """
    if avant_jour is None:
        avant_jour = _jour_limite_cloture(centre)
    identifiants = list(
        Ticket.objects.filter(centre=centre, statut__in=Ticket.STATUTS_OUVERTS, jour__lt=avant_jour)
        .order_by("pk").values_list("pk", flat=True)
    )
    nombre = 0
    for ticket_id in identifiants:
        # Même ordre de verrous que les appels : files, puis ticket.
        _verrouiller_files_du_ticket(ticket_id)
        ticket = Ticket.objects.select_for_update().filter(
            pk=ticket_id, statut__in=Ticket.STATUTS_OUVERTS
        ).first()
        if ticket is None:
            continue   # fermé entre-temps (annulé, terminé)
        _fermer_passages(ticket)
        _terminer_ticket(ticket, None, Evenement.Type.CLOTURE, Ticket.Statut.CLOTURE)
        nombre += 1
    return nombre


# ---------------------------------------------------------------------------
# Mode Autre : notifications et réponses
# ---------------------------------------------------------------------------

@transaction.atomic
def transmettre_demande(demande, agent, destinataire=None, service=None, message=""):
    """
    L'accueil oriente la demande vers une personne précise ou vers un
    service ; dans ce cas, seul l'agent désigné du service est notifié.
    """
    from comptes.models import AffectationUtilisateur

    demande = DemandeAutre.objects.select_for_update(of=("self",)).select_related("ticket").get(pk=demande.pk)
    if demande.statut == DemandeAutre.Statut.CLOTUREE:
        raise ErreurCircuit("Cette demande est clôturée.")

    if (destinataire is None) == (service is None):
        raise ErreurCircuit("Choisissez soit une personne, soit un service.")

    if service is not None:
        if service.centre_id != demande.centre_id:
            raise ErreurCircuit("Service inconnu.")
        if not service.actif:
            raise ErreurCircuit(f"Le service {service.nom} n'est plus actif.")
        destinataire = service.destinataire_demandes
        if destinataire is None:
            raise ErreurCircuit(
                f"Aucun agent désigné pour recevoir les demandes du service {service.nom}. "
                "Choisissez une personne précise."
            )

    if not destinataire.is_active:
        raise ErreurCircuit("Le destinataire n'a pas de compte actif.")

    autorise = destinataire.is_superuser or AffectationUtilisateur.objects.filter(
        utilisateur=destinataire, centre_id=demande.centre_id, actif=True
    ).exists()
    if not autorise:
        raise ErreurCircuit("Le destinataire n'est pas affecté à ce centre.")

    notification = Notification.objects.create(
        centre_id=demande.centre_id,
        demande=demande,
        destinataire=destinataire,
        service=service,
        message=(message or "").strip(),
        transmise_par=agent,
        date=timezone.now(),
    )
    demande.statut = DemandeAutre.Statut.TRANSMISE
    if demande.vue_par_id is None:
        demande.vue_par = agent
        demande.date_vue = timezone.now()
    demande.save(update_fields=["statut", "vue_par", "date_vue"])
    _evenement(demande.ticket, Evenement.Type.NOTIFICATION, agent=agent,
               destinataire=destinataire.pk, service=service.nom if service else None)
    _cloturer_ticket_autre_oriente(demande.ticket_id, agent)
    return notification


def _cloturer_ticket_autre_oriente(ticket_id, agent):
    """
    Demande « Autre » orientée depuis la page Demandes, sans que l'accueil ait
    appelé le visiteur : le ticket est clôturé, comme après l'orientation au
    guichet (le ticket est clôturé après l'orientation ; la demande reste
    suivie par ses notifications). Si l'accueil tient le ticket au guichet
    (appelé ou en cours), c'est l'agent qui le termine (relecture du 23/11/2026).
    """
    _verrouiller_files_du_ticket(ticket_id)
    ticket = Ticket.objects.select_for_update().get(pk=ticket_id)
    if ticket.statut not in Ticket.STATUTS_OUVERTS:
        return
    if EntreeFile.objects.filter(passage__ticket=ticket, statut__in=EntreeFile.STATUTS_TENUS).exists():
        return
    maintenant = timezone.now()
    for passage in ticket.passages.select_for_update().filter(statut__in=Passage.STATUTS_ACTIFS):
        passage.entrees.filter(statut=EntreeFile.Statut.EN_ATTENTE).update(
            statut=EntreeFile.Statut.TERMINE, date_fin=maintenant)
        passage.statut = Passage.Statut.TERMINE
        passage.date_fin = maintenant
        passage.termine_par = agent
        passage.save(update_fields=["statut", "date_fin", "termine_par"])
    _terminer_ticket(ticket, agent)


DELAI_APPEL_VISITEUR = timedelta(seconds=10)


@transaction.atomic
def appeler_visiteur(notification, agent, poste=""):
    """
    Le destinataire d'une demande « Autre » (orientée par l'accueil) appelle
    le visiteur sur l'écran principal (décision du 19/11/2026). L'écran
    affiche le numéro du ticket et la destination : le service de la
    notification, sinon le rôle du destinataire (ex. « Direction »).
    """
    from comptes.permissions import affectation_active

    notification = Notification.objects.select_for_update(of=("self",)).select_related(
        "demande", "demande__ticket", "service", "centre"
    ).get(pk=notification.pk)
    if notification.destinataire_id != agent.pk:
        raise ErreurCircuit("Cette demande ne vous a pas été transmise.")
    demande = notification.demande
    ticket = demande.ticket
    if demande.statut == DemandeAutre.Statut.CLOTUREE:
        raise ErreurCircuit("Cette demande est clôturée.")
    if ticket.jour != maintenant_centre(notification.centre).date():
        raise ErreurCircuit("Ce ticket n'est pas d'aujourd'hui : le visiteur ne peut plus être appelé.")
    # 10 s minimum entre deux appels du visiteur par ce destinataire.
    dernier = Appel.objects.filter(ticket=ticket, agent=agent).order_by("-date").first()
    if dernier is not None and timezone.now() - dernier.date < DELAI_APPEL_VISITEUR:
        secondes = int((DELAI_APPEL_VISITEUR - (timezone.now() - dernier.date)).total_seconds()) + 1
        raise ErreurCircuit(f"Veuillez patienter {secondes} s avant de rappeler.")
    entree = EntreeFile.objects.filter(passage__ticket=ticket).select_related("file__service").order_by("-id").first()
    if entree is None:
        raise ErreurCircuit("Ce visiteur n'est pas passé par l'accueil.")

    if notification.service is not None:
        destination = notification.service.nom
        service = notification.service if notification.service.afficher_ecran_principal else entree.file.service
    else:
        affectation = affectation_active(agent, notification.centre)
        destination = affectation.groupe.name if affectation and affectation.groupe_id else str(agent)
        service = entree.file.service
    maintenant = timezone.now()
    appel = Appel.objects.create(
        centre_id=ticket.centre_id,
        entree=entree,
        ticket=ticket,
        service=service,
        numero_ticket=ticket.numero,
        nom_service=destination[:100],
        poste=(poste or "")[:50],
        numero_appel=Appel.objects.filter(ticket=ticket).count() + 1,
        mode=Appel.Mode.ECRAN,
        agent=agent,
        date=maintenant,
    )
    _evenement(ticket, Evenement.Type.APPEL, agent=agent, notification=notification.pk,
               destination=destination, poste=appel.poste)
    return appel


@transaction.atomic
def repondre_notification(notification, agent, statut, message=""):
    notification = Notification.objects.select_for_update(of=("self",)).select_related(
        "demande", "demande__ticket"
    ).get(pk=notification.pk)
    if notification.destinataire_id != agent.pk:
        raise ErreurCircuit("Cette notification ne vous est pas adressée.")
    if statut not in ReponseNotification.Statut.values:
        raise ErreurCircuit("Statut de réponse inconnu.")
    if notification.demande.statut == DemandeAutre.Statut.CLOTUREE:
        raise ErreurCircuit("Cette demande est déjà clôturée.")

    reponse = ReponseNotification.objects.create(
        centre_id=notification.centre_id,
        notification=notification,
        statut=statut,
        message=(message or "").strip(),
        auteur=agent,
        date=timezone.now(),
    )
    if notification.date_lecture is None:
        notification.date_lecture = timezone.now()
        notification.save(update_fields=["date_lecture"])

    demande = notification.demande
    if statut in ReponseNotification.STATUTS_FINAUX:
        demande.statut = DemandeAutre.Statut.CLOTUREE
        demande.save(update_fields=["statut"])

    _evenement(demande.ticket, Evenement.Type.REPONSE, agent=agent,
               statut=statut, message=reponse.message)
    return reponse
