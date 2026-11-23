"""
Vérification des droits métier.

Les droits sont portés par les affectations : une affectation relie un
utilisateur, un centre et un rôle (groupe Django). Elle couvre tous les
services du centre ou une liste de services. Des permissions individuelles
peuvent s'y ajouter. Le contrôle se fait toujours avec un centre (et un
service lorsque l'action concerne un service).
"""

from django.contrib.auth.models import Permission

from centres.access import est_administrateur_central

from .models import AffectationService, AffectationUtilisateur


def _codes(permissions):
    return {
        f"{app}.{code}"
        for app, code in permissions.values_list(
            "content_type__app_label", "codename"
        )
    }


def permissions_affectation(affectation):
    """Ensemble des permissions « app.codename » d'une affectation."""
    cache = getattr(affectation, "_cache_permissions", None)

    if cache is not None:
        return cache

    perms = set()

    if affectation.groupe_id:
        perms |= _codes(
            Permission.objects.filter(group__id=affectation.groupe_id)
        )

    perms |= _codes(affectation.permissions.all())
    affectation._cache_permissions = perms
    return perms


def affectation_active(utilisateur, centre):
    if centre is None or not getattr(utilisateur, "is_authenticated", False):
        return None

    return (
        AffectationUtilisateur.objects
        .filter(utilisateur=utilisateur, centre=centre, actif=True, centre__actif=True)
        .select_related("groupe")
        .first()
    )


def service_dans_portee(affectation, service):
    restrictions = AffectationService.objects.filter(affectation=affectation)

    if not restrictions.exists():
        return True

    return restrictions.filter(service=service).exists()


def a_droit(utilisateur, permission, centre, service=None):
    """
    Vérifie qu'un utilisateur possède `permission` (« app.codename »)
    dans `centre`, et pour `service` si fourni.
    """
    if not getattr(utilisateur, "is_authenticated", False):
        return False

    if not utilisateur.is_active or centre is None or not centre.actif:
        return False

    if service is not None:
        if service.centre_id != centre.pk or not service.actif:
            return False

    if est_administrateur_central(utilisateur):
        return True

    affectation = affectation_active(utilisateur, centre)

    if affectation is None:
        return False

    if service is not None and not service_dans_portee(affectation, service):
        return False

    return permission in permissions_affectation(affectation)


# Décision du 19/11/2026 : tout passe d'abord par l'accueil. Les
# administrateurs, les responsables et la direction n'appellent aucun ticket
# (circuit.services.appeler) ; l'accueil leur transmet les demandes « Autre ».
ROLES_APPEL_AUTRES_SEULEMENT = {"Responsable", "Direction", "Administrateur de centre", "Médecin en chef"}


def appel_limite_aux_autres(utilisateur, centre):
    """Vrai si l'utilisateur ne doit appeler aucun ticket dans ce centre (tout passe par l'accueil)."""
    if est_administrateur_central(utilisateur):
        return True
    affectation = affectation_active(utilisateur, centre)
    return bool(
        affectation is not None
        and affectation.groupe_id
        and affectation.groupe.name in ROLES_APPEL_AUTRES_SEULEMENT
    )


def voit_autres_seulement(utilisateur, centre):
    """
    La direction et l'administrateur de centre n'appellent et ne voient que
    les tickets « Autre » : jamais les patients (décisions du 19/11/2026).
    """
    if est_administrateur_central(utilisateur):
        return False
    affectation = affectation_active(utilisateur, centre)
    return bool(
        affectation is not None
        and affectation.groupe_id
        and affectation.groupe.name in ("Direction", "Administrateur de centre", "Médecin en chef")
    )


def permissions_tous_centres(utilisateur):
    """Union des permissions de toutes les affectations actives (menus)."""
    perms = set()

    for affectation in AffectationUtilisateur.objects.filter(
        utilisateur=utilisateur, actif=True, centre__actif=True
    ):
        perms |= permissions_affectation(affectation)

    return perms
