"""
Éléments d'interface partagés : icônes SVG locales (aucune ressource
externe) et choix de l'icône d'un service ou d'un parcours.
"""

import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()

# Code de service ou préfixe de parcours -> icône (voir templates/_icones.html).
ICONES_CODES = {
    "ACC": "accueil", "INF": "infirmerie", "MED": "medecin", "DEN": "dentiste",
    "ECH": "echographie", "PHA": "pharmacie", "MEC": "medecin",
    "M": "medecin", "D": "dentiste", "I": "infirmerie", "E": "echographie", "A": "autre", "C": "medecin",
}
# Mot présent dans le nom -> icône (services créés avec d'autres codes).
ICONES_MOTS = (
    ("accueil", "accueil"), ("infirm", "infirmerie"), ("médec", "medecin"), ("medec", "medecin"),
    ("dent", "dentiste"), ("écho", "echographie"), ("echo", "echographie"), ("pharm", "pharmacie"),
)


@register.simple_tag
def static_v(chemin):
    """
    Adresse d'un fichier statique avec sa version (date de modification) :
    après une mise à jour, les navigateurs prennent le nouveau style ou
    script sans Ctrl+F5. En production, le stockage « Manifest » de
    whitenoise ajoute déjà une empreinte au nom du fichier.
    """
    from django.contrib.staticfiles.storage import ManifestFilesMixin, staticfiles_storage

    url = static(chemin)
    # Production : le nom contient déjà l'empreinte. Inutile de lire la date du
    # fichier sur le disque à chaque page.
    if "?" in url or isinstance(staticfiles_storage, ManifestFilesMixin):
        return url
    fichier = finders.find(chemin)
    if fichier:
        try:
            return f"{url}?v={int(os.path.getmtime(fichier))}"
        except OSError:
            pass
    return url


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


# Accueil de l'administration rangé par tâche (décision du 27/11/2026 : « trop
# de choses, on s'y perd »). (nom du modèle, libellé simple, à quoi ça sert, icône)
SECTIONS_ADMIN = (
    ("Le plus courant", "Aussi faisable dans l'application : onglets Comptes, Borne et Écran.", (
        ("Utilisateur", "Comptes du personnel", "Créer, modifier, désactiver un compte", "groupe"),
        ("Borne", "Bornes", "Tablettes où les patients prennent leur ticket", "tickets"),
        ("Parcours", "Services de la borne", "Boutons de la borne et chemin du patient", "transfert"),
        ("EcranAffichage", "Écrans de la salle", "Ce que chaque TV affiche", "ecran"),
        ("ImprimanteTicket", "Imprimantes", "Imprimantes de tickets des bornes", "imprimante"),
    )),
    ("Réglages du centre", "À régler une fois, à l'installation d'un centre.", (
        ("Centre", "Centres", "Nom, horaires de clôture, fonctionnement", "centre"),
        ("Service", "Services", "Accueil, soins, pharmacie ; horaires d'ouverture", "services"),
        ("FileAttente", "Files d'attente", "Absences, délais, agents en parallèle", "sablier"),
        ("QuotaParcours", "Places du dentiste", "Nombre de places par matinée / après-midi", "calendrier"),
        ("MotifDemande", "Motifs « Autre »", "Rendez-vous, cotisation, direction…", "message"),
        ("AffectationUtilisateur", "Rôles des comptes par centre", "Détail des affectations (avancé)", "utilisateur"),
        ("Group", "Rôles et droits", "Ce que chaque rôle peut faire (avancé)", "bouclier"),
    )),
)
SECTION_HISTORIQUE = ("Consultation et historique", "Enregistré automatiquement : rien à saisir ici.")


@register.simple_tag
def sections_admin(app_list):
    """Sections de l'accueil de l'administration, avec seulement les modules autorisés."""
    modeles = {m["object_name"]: m for app in app_list for m in app["models"]}
    sections, ranges = [], set()
    for titre, aide, entrees in SECTIONS_ADMIN:
        liens = []
        for nom, libelle, description, icone in entrees:
            if nom in modeles:
                liens.append({"modele": modeles[nom], "libelle": libelle, "description": description, "icone": icone})
                ranges.add(nom)
        if liens:
            sections.append({"titre": titre, "aide": aide, "liens": liens})
    reste = [
        {"modele": m, "libelle": m["name"], "description": "", "icone": "historique"}
        for nom, m in modeles.items() if nom not in ranges
    ]
    if reste:
        sections.append({"titre": SECTION_HISTORIQUE[0], "aide": SECTION_HISTORIQUE[1], "liens": reste,
                         "replie": True})
    return sections
