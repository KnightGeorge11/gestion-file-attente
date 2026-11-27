"""
Fil de navigation de chaque page (décision du 27/11/2026) : « Bazar Be ›
Comptes › Jean Rakoto › Modifier », chaque niveau cliquable, au lieu d'un
simple bouton « Retour ».
"""

from django.urls import NoReverseMatch, reverse

# Page : (libellé, page parente). Un libellé None est lu dans la base (nom du
# service, numéro du ticket, nom du compte).
PAGES = {
    "circuit:guichets": ("Services", None),
    "circuit:guichet": (None, "circuit:guichets"),
    "circuit:ma_salle": ("Votre salle", "circuit:guichets"),
    "circuit:tickets": ("Tickets", None),
    "circuit:ticket": (None, "circuit:tickets"),
    "circuit:demandes": ("Demandes", None),
    "circuit:notifications": ("Notifications", None),
    "circuit:ecran_connexion": ("Écran de la salle", None),
    "circuit:bornes": ("Borne", None),
    "statistiques:tableau": ("Statistiques", None),
    "statistiques:direct": ("En direct", "statistiques:tableau"),
    "comptes:comptes": ("Comptes", None),
    "comptes:nouveau": ("Nouveau compte", "comptes:comptes"),
    "comptes:plusieurs": ("Plusieurs comptes", "comptes:comptes"),
    "comptes:profil": (None, "comptes:comptes"),
    "comptes:modifier": ("Modifier", "comptes:profil"),
    "comptes:action": ("Mot de passe", "comptes:profil"),
    "centres:selection": ("Choisir le centre", None),
}


def _libelle(nom, kwargs, centre):
    from centres.models import Service
    from circuit.models import Ticket
    from comptes.models import Utilisateur

    try:
        if nom == "circuit:guichet":
            return Service.objects.filter(pk=kwargs["service_id"], centre=centre).values_list("nom", flat=True).first()
        if nom == "circuit:ticket":
            return Ticket.objects.filter(pk=kwargs["ticket_id"], centre=centre).values_list("numero", flat=True).first()
        if nom == "comptes:profil":
            compte = Utilisateur.objects.filter(pk=kwargs["utilisateur_id"]).first()
            return str(compte) if compte else None
    except (KeyError, ValueError):
        return None
    return None


def _adresse(nom, kwargs):
    try:
        return reverse(nom, kwargs=kwargs)
    except NoReverseMatch:
        try:
            return reverse(nom)
        except NoReverseMatch:
            return None


def fil_de_navigation(request):
    """[{"libelle", "url"}] du centre jusqu'à la page actuelle (sans lien pour celle-ci)."""
    correspondance = getattr(request, "resolver_match", None)
    if correspondance is None or not request.user.is_authenticated:
        return []
    nom = correspondance.view_name
    if nom not in PAGES:
        return []
    kwargs = dict(correspondance.kwargs)
    centre = getattr(request, "centre_actif", None)
    niveaux = []
    courant = nom
    while courant is not None and len(niveaux) < 6:
        libelle, parent = PAGES[courant]
        if libelle is None:
            libelle = _libelle(courant, kwargs, centre) or "…"
        niveaux.append({"libelle": libelle, "url": _adresse(courant, kwargs)})
        courant = parent
    niveaux.reverse()
    niveaux[-1]["url"] = None
    racine = {"libelle": centre.nom if centre else "Accueil", "url": reverse("accueil"), "racine": True}
    return [racine] + niveaux
