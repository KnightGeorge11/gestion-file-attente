"""
Visibilité des patients par service.

Règle (décisions du 18/11/2026) : un agent ne voit que les patients qui
viennent dans son service, c'est-à-dire un ticket :
- qui a, ou a eu, un passage dans un service où l'agent prend en charge des
  patients (permission « appeler » dans la portée de son affectation) ;
- ou, « à l'avance », un ticket encore ouvert dont le parcours prévoit
  plus loin une étape dans ce service (ex. un patient M à l'accueil ou à
  l'infirmerie est visible des médecins).

Un patient transféré à la pharmacie depuis une étape intermédiaire ne va
plus vers les étapes suivantes : il n'est plus « à venir » pour elles.

Conséquences :
- la pharmacie ne voit que les patients qui lui ont été transférés (elle
  n'est jamais une étape de parcours) ;
- l'accueil voit tous les tickets, puisque tous les patients y passent ;
- la direction ne voit aucun patient : seulement les tickets « Autre », qu'elle
  peut appeler (décision du 19/11/2026) ;
- l'administrateur central voit tout.
"""

from django.db.models import Exists, OuterRef, Q, Subquery

from centres.access import est_administrateur_central
from centres.models import Service
from comptes.permissions import a_droit, voit_autres_seulement
from parcours.models import EtapeParcours

from .models import Passage, Ticket


def services_operationnels(utilisateur, centre):
    """Services du centre où l'agent prend en charge des patients."""
    if centre is None:
        return []
    return [
        service
        for service in Service.objects.filter(centre=centre, actif=True)
        if a_droit(utilisateur, "circuit.appeler", centre, service)
    ]


def _a_venir(services):
    """
    Condition « le parcours prévoit encore ce service » : ticket ouvert, dont
    le passage actif est une étape du parcours, et dont une étape suivante
    est dans l'un des services.
    """
    ordre_courant = Passage.objects.filter(
        ticket=OuterRef("pk"),
        statut__in=Passage.STATUTS_ACTIFS,
        origine=Passage.Origine.PARCOURS,
    ).values("ordre_etape")[:1]
    etape_suivante = EtapeParcours.objects.filter(
        version=OuterRef("parcours_version"),
        service__in=services,
        ordre__gt=OuterRef("ordre_courant"),
    )
    return ordre_courant, etape_suivante


def tickets_visibles(utilisateur, centre):
    if centre is None:
        return Ticket.objects.none()
    if est_administrateur_central(utilisateur):
        return Ticket.objects.filter(centre=centre)
    services = services_operationnels(utilisateur, centre)
    if not services:
        return Ticket.objects.none()
    if voit_autres_seulement(utilisateur, centre):
        # Direction : seulement les tickets « Autre », jamais les patients.
        return Ticket.objects.filter(centre=centre, mode=Ticket.Mode.AUTRE)
    ordre_courant, etape_suivante = _a_venir(services)
    passe_par_service = Passage.objects.filter(ticket=OuterRef("pk"), service__in=services)
    return (
        Ticket.objects.filter(centre=centre)
        .annotate(ordre_courant=Subquery(ordre_courant))
        .filter(
            Q(Exists(passe_par_service))
            | Q(statut__in=Ticket.STATUTS_OUVERTS, ordre_courant__isnull=False)
            & Q(Exists(etape_suivante))
        )
    )


def tickets_a_venir(service):
    """Tickets ouverts qui viendront dans ce service (pas encore arrivés)."""
    ordre_courant, etape_suivante = _a_venir([service])
    deja_arrives = Passage.objects.filter(ticket=OuterRef("pk"), service=service)
    actif = Passage.objects.filter(
        ticket=OuterRef("pk"), statut__in=Passage.STATUTS_ACTIFS
    ).values("nom_service")[:1]
    return (
        Ticket.objects.filter(centre_id=service.centre_id, statut__in=Ticket.STATUTS_OUVERTS)
        .annotate(ordre_courant=Subquery(ordre_courant), service_actuel=Subquery(actif))
        .filter(ordre_courant__isnull=False)
        .filter(Exists(etape_suivante))
        .exclude(Exists(deja_arrives))
        .select_related("patient")
        .order_by("date_emission")
    )


def services_medecin_chef(utilisateur, centre):
    """Services « Médecin en chef » où cet agent reçoit les patients."""
    if centre is None:
        return []
    return [
        service
        for service in Service.objects.filter(centre=centre, actif=True, est_medecin_chef=True)
        if a_droit(utilisateur, "circuit.appeler", centre, service)
    ]


def patients_pour_le_chef(utilisateur, centre):
    """
    Patients envoyés au médecin en chef et pas encore reçus (décision du
    22/11/2026) : ils arrivent dans ses Notifications, sans appel à l'écran.
    """
    from .models import EntreeFile

    services = services_medecin_chef(utilisateur, centre)
    if not services:
        return EntreeFile.objects.none()
    return (
        EntreeFile.objects.filter(file__service__in=services, statut=EntreeFile.Statut.EN_ATTENTE)
        .select_related("passage", "passage__ticket", "file", "file__service")
        .order_by("rang", "id")
    )
