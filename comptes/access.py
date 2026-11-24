
from centres.access import (
    est_administrateur_central,
    utilisateur_peut_acceder_centre,
)

from .models import AffectationUtilisateur, AffectationService


def utilisateur_peut_acceder_service(utilisateur, centre, service):
    """Vérifie l'accès d'un utilisateur à un service précis."""

    if not utilisateur or not utilisateur.is_authenticated:
        return False

    if centre is None or service is None:
        return False

    if not centre.actif or not service.actif:
        return False

    if service.centre_id != centre.pk:
        return False

    if est_administrateur_central(utilisateur):
        return True

    if not utilisateur_peut_acceder_centre(utilisateur, centre):
        return False

    affectation = AffectationUtilisateur.objects.filter(
        utilisateur=utilisateur,
        centre=centre,
        actif=True,
    ).first()

    if affectation is None:
        return False

    restrictions = AffectationService.objects.filter(
        affectation=affectation,
    )

    if not restrictions.exists():
        return True

    if restrictions.filter(service=service).exists():
        return True

    # Infirmerie en relais de l'accueil quand aucun agent d'accueil n'est connecté (24/11/2026).
    if service.est_accueil:
        from .presence import relais_accueil

        return relais_accueil(affectation, centre)
    return False
