
from .models import Centre
from comptes.models import AffectationUtilisateur


SESSION_CENTRE_KEY = "centre_actif_id"


def est_administrateur_central(utilisateur):
    """
    Détermine si l'utilisateur possède un accès central.
    """
    return (
        utilisateur.is_authenticated
        and (
            utilisateur.is_superuser
            or utilisateur.type_compte == "ADMIN"
        )
    )


def centres_autorises(utilisateur):
    """
    Retourne les centres auxquels l'utilisateur peut accéder.
    L'administrateur central peut accéder à tous les centres actifs.
    """
    if not utilisateur.is_authenticated:
        return Centre.objects.none()

    if est_administrateur_central(utilisateur):
        return Centre.objects.filter(actif=True)

    return Centre.objects.filter(
        affectations_utilisateurs__utilisateur=utilisateur,
        affectations_utilisateurs__actif=True,
        actif=True,
    ).distinct()


def utilisateur_peut_acceder_centre(utilisateur, centre):
    """
    Vérifie l'accès à un centre précis.
    """
    if not utilisateur.is_authenticated or not centre.actif:
        return False

    if est_administrateur_central(utilisateur):
        return True

    return AffectationUtilisateur.objects.filter(
        utilisateur=utilisateur,
        centre=centre,
        actif=True,
    ).exists()
