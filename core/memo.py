"""
Mémoire de calcul limitée à un bloc de code (audit du 24/11/2026).

Le menu commun (core.context_processors.navigation) vérifie une quinzaine de
droits pour le même agent et le même centre : sans mémoire, chaque
vérification relisait l'affectation, ses permissions et la présence de
l'accueil (30 à 40 requêtes par page, y compris les rafraîchissements HTMX).

Dans un bloc `with memo():`, un même calcul n'est fait qu'une fois. Hors de
ce bloc, rien n'est mémorisé : les vues voient toujours les droits à jour.
"""

from contextlib import contextmanager
from contextvars import ContextVar

_memoire = ContextVar("memo_calculs", default=None)


@contextmanager
def memo():
    jeton = _memoire.set({})
    try:
        yield
    finally:
        _memoire.reset(jeton)


def memorise(cle, calcul):
    memoire = _memoire.get()
    if memoire is None:
        return calcul()
    if cle not in memoire:
        memoire[cle] = calcul()
    return memoire[cle]
