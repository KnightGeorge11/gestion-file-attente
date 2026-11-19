"""
Éléments d'interface partagés : icônes SVG locales (aucune ressource
externe) et choix de l'icône d'un service ou d'un parcours.
"""

from django import template
from django.utils.html import format_html

register = template.Library()

# Code de service ou préfixe de parcours -> icône (voir templates/_icones.html).
ICONES_CODES = {
    "ACC": "accueil", "INF": "infirmerie", "MED": "medecin", "DEN": "dentiste",
    "ECH": "echographie", "PHA": "pharmacie",
    "M": "medecin", "D": "dentiste", "I": "infirmerie", "E": "echographie", "A": "autre",
}
# Mot présent dans le nom -> icône (services créés avec d'autres codes).
ICONES_MOTS = (
    ("accueil", "accueil"), ("infirm", "infirmerie"), ("médec", "medecin"), ("medec", "medecin"),
    ("dent", "dentiste"), ("écho", "echographie"), ("echo", "echographie"), ("pharm", "pharmacie"),
)


@register.simple_tag
def icone(nom, classe=""):
    """Icône du sprite local : {% icone "tickets" %}."""
    return format_html(
        '<svg class="ic {}" aria-hidden="true" focusable="false"><use href="#i-{}"></use></svg>', classe, nom
    )


@register.filter
def icone_service(objet):
    """Nom d'icône pour un service, un parcours ou un libellé."""
    if objet is None:
        return "autre"
    code = getattr(objet, "code", None) or getattr(objet, "prefixe", None) or ""
    if str(code).upper() in ICONES_CODES:
        return ICONES_CODES[str(code).upper()]
    nom = (
        getattr(objet, "nom", None) or getattr(objet, "libelle_borne", None) or str(objet)
    ).lower()
    for mot, nom_icone in ICONES_MOTS:
        if mot in nom:
            return nom_icone
    return "service"
