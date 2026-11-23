"""Lecture des paramètres d'une requête."""


def entier(valeur):
    """
    Identifiant reçu d'un formulaire ou d'une adresse : un entier, ou None.
    Un texte non numérique (« abc ») donne None, donc « introuvable », au
    lieu d'une erreur 500 (relecture du 23/11/2026).
    """
    try:
        return int(valeur)
    except (TypeError, ValueError):
        return None
