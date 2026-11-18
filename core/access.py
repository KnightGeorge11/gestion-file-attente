from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404

from centres.access import (
    SESSION_CENTRE_KEY,
    utilisateur_peut_acceder_centre,
)
from centres.decorators import centre_actif_requis


def obtenir_centre_actif(request):
    """
    Retourne le centre actif uniquement s'il correspond
    au centre enregistré en session et si l'utilisateur y est autorisé.
    """

    utilisateur = getattr(request, "user", None)

    if utilisateur is None or not utilisateur.is_authenticated:
        return None

    centre = getattr(request, "centre_actif", None)

    if centre is None:
        return None

    centre_session = request.session.get(SESSION_CENTRE_KEY)

    if str(centre_session) != str(centre.pk):
        return None

    if not utilisateur_peut_acceder_centre(utilisateur, centre):
        return None

    return centre


def objet_du_centre(request, modele, **filtres):
    """
    Récupère un objet métier uniquement dans le centre actif.
    Un identifiant appartenant à un autre centre donne une erreur 404.
    """
    return get_object_or_404(
        modele.objects.pour_utilisateur(request.user, request.centre_actif),
        **filtres,
    )


def permission_centre_requise(permission):
    """Vue protégée : centre actif + permission dans ce centre."""

    def decorateur(vue):
        @centre_actif_requis
        @wraps(vue)
        def enveloppe(request, *args, **kwargs):
            from comptes.permissions import a_droit

            if not a_droit(request.user, permission, request.centre_actif):
                raise PermissionDenied
            return vue(request, *args, **kwargs)

        return enveloppe

    return decorateur
