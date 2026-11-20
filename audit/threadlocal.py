"""
Mémorise l'utilisateur de la requête en cours, pour que les signaux
d'audit puissent attribuer une modification faite dans l'administration.
"""

import threading

_local = threading.local()


def requete_courante():
    """Requête en cours (pour l'adresse IP des actions d'administration)."""
    return getattr(_local, "requete", None)


def utilisateur_courant():
    utilisateur = getattr(_local, "utilisateur", None)
    if utilisateur is not None and getattr(utilisateur, "is_authenticated", False):
        return utilisateur
    return None


class UtilisateurCourantMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.utilisateur = getattr(request, "user", None)
        _local.requete = request
        try:
            return self.get_response(request)
        finally:
            _local.utilisateur = None
            _local.requete = None
