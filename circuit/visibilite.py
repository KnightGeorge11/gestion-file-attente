"""
Visibilité des patients par service.

Règle : un agent ne voit un ticket (et donc le patient qui le porte) que si
ce ticket a, ou a eu, un passage dans un service où l'agent prend en charge
des patients (permission « appeler » dans la portée de son affectation).

Conséquences :
- un médecin voit les patients arrivés dans la file des médecins (pas ceux
  encore à l'accueil ou à l'infirmerie) ;
- la pharmacie ne voit que les patients qui lui ont été transférés ;
- l'accueil voit tous les tickets, puisque tous les patients y passent ;
- la direction (statistiques sans données nominatives) ne voit aucun patient ;
- l'administrateur central voit tout.
"""

from centres.access import est_administrateur_central
from centres.models import Service
from comptes.permissions import a_droit

from .models import Ticket


def services_operationnels(utilisateur, centre):
    """Services du centre où l'agent prend en charge des patients."""
    if centre is None:
        return []
    return [
        service
        for service in Service.objects.filter(centre=centre, actif=True)
        if a_droit(utilisateur, "circuit.appeler", centre, service)
    ]


def tickets_visibles(utilisateur, centre):
    if centre is None:
        return Ticket.objects.none()
    if est_administrateur_central(utilisateur):
        return Ticket.objects.filter(centre=centre)
    services = services_operationnels(utilisateur, centre)
    if not services:
        return Ticket.objects.none()
    return Ticket.objects.filter(centre=centre, passages__service__in=services).distinct()
