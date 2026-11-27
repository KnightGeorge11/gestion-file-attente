"""
Création et modification simples des comptes du personnel (décision du
27/11/2026) : une page dans l'application au lieu de l'administration
(compte, puis affectation, puis services). L'administrateur de la
plateforme gère tous les comptes ; l'administrateur de centre, ceux de son
centre, sans pouvoir créer d'administrateurs.
"""

import re
import secrets
import unicodedata

from django.contrib.auth.models import Group
from django.db import transaction

from centres.access import est_administrateur_central
from centres.models import Service

from .models import AffectationService, AffectationUtilisateur, Utilisateur

ROLE_ADMIN_CENTRE = "Administrateur de centre"

# Rôles qui travaillent dans un service précis : le service est obligatoire
# (sans service, le compte aurait accès à tous les services du centre).
SERVICE_DU_ROLE = {
    "Accueil": lambda s: s.est_accueil,
    "Pharmacie": lambda s: s.est_pharmacie,
    "Infirmerie": lambda s: s.code.upper() == "INF" or s.nom.lower() == "infirmerie",
    "Médecin": lambda s: s.code.upper() == "MED" or s.nom.lower() == "médecin",
    "Dentiste": lambda s: s.code.upper() == "DEN" or s.nom.lower() == "dentiste",
    "Échographie": lambda s: s.code.upper() == "ECH" or s.nom.lower() == "échographie",
}
ROLES_AVEC_SERVICE = set(SERVICE_DU_ROLE)
from .models import ROLES_AVEC_SALLE  # noqa: E402,F401  (médecins seulement)

# Lettres et chiffres faciles à lire et à dicter (ni 0/O, ni 1/l/I).
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def peut_gerer_comptes(utilisateur, centre):
    from .permissions import a_droit

    if centre is None:
        return False
    return est_administrateur_central(utilisateur) or a_droit(utilisateur, "comptes.add_utilisateur", centre)


def roles_attribuables(utilisateur):
    """Rôles proposés : tous pour la plateforme ; sans « Administrateur de centre » pour un centre."""
    from .management.commands.initialiser_roles import ROLES

    roles = Group.objects.filter(name__in=ROLES)
    if not est_administrateur_central(utilisateur):
        roles = roles.exclude(name=ROLE_ADMIN_CENTRE)
    ordre = list(ROLES)
    return sorted(roles, key=lambda g: ordre.index(g.name))


def services_du_centre(centre):
    return list(Service.objects.filter(centre=centre, actif=True).order_by("ordre", "nom"))


def service_suggere(role_nom, services):
    test = SERVICE_DU_ROLE.get(role_nom)
    return next((s for s in services if test(s)), None) if test else None


def comptes_modifiables(utilisateur, centre):
    """Comptes que `utilisateur` peut modifier dans `centre` (jamais le sien : pas d'auto-promotion)."""
    from .admin import comptes_geres

    comptes = comptes_geres(utilisateur).exclude(pk=utilisateur.pk)
    if not est_administrateur_central(utilisateur):
        comptes = comptes.filter(affectations__centre=centre)
    return comptes.distinct()


def _ascii(texte):
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", texte.lower())


def identifiant_libre(prenom, nom):
    """« jean.rakoto », ou « jean.rakoto2 » s'il est déjà pris."""
    base = ".".join(p for p in (_ascii(prenom), _ascii(nom)) if p)[:140] or "agent"
    identifiant, rang = base, 1
    while Utilisateur.objects.filter(username=identifiant).exists():
        rang += 1
        identifiant = f"{base}{rang}"
    return identifiant


def mot_de_passe_provisoire():
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def decouper_nom(ligne):
    """« Jean Rakoto » → (« Jean », « Rakoto ») ; « RAKOTO » seul → (« », « RAKOTO »)."""
    morceaux = ligne.split()
    if len(morceaux) == 1:
        return "", morceaux[0]
    return morceaux[0], " ".join(morceaux[1:])


@transaction.atomic
def creer_compte(centre, prenom, nom, groupe, services=(), salle="", identifiant="", mot_de_passe=""):
    """Crée le compte, son affectation au centre et ses services. Retourne (compte, mot de passe)."""
    identifiant = (identifiant or "").strip().lower() or identifiant_libre(prenom, nom)
    mot_de_passe = mot_de_passe or mot_de_passe_provisoire()
    compte = Utilisateur(
        username=identifiant, first_name=prenom.strip()[:150], last_name=nom.strip()[:150],
        type_compte=(Utilisateur.TypeCompte.ADMIN_CENTRE if groupe.name == ROLE_ADMIN_CENTRE
                     else Utilisateur.TypeCompte.AGENT),
    )
    compte.set_password(mot_de_passe)
    compte.save()
    affecter(compte, centre, groupe, services, salle)
    return compte, mot_de_passe


@transaction.atomic
def affecter(compte, centre, groupe, services=(), salle=""):
    """Rôle, services et salle du compte dans ce centre (crée l'affectation au besoin)."""
    affectation, _ = AffectationUtilisateur.objects.get_or_create(
        utilisateur=compte, centre=centre, defaults={"groupe": groupe},
    )
    affectation.groupe = groupe
    affectation.actif = True
    affectation.salle = (salle or "").strip()[:50] if groupe.name in ROLES_AVEC_SALLE else ""
    affectation.save()
    voulus = {s.pk for s in services}
    AffectationService.objects.filter(affectation=affectation).exclude(service_id__in=voulus).delete()
    for service in services:
        AffectationService.objects.get_or_create(affectation=affectation, service=service)
    # Administrateur de centre ⇄ agent : le type du compte suit le rôle.
    admin_ailleurs = AffectationUtilisateur.objects.filter(
        utilisateur=compte, actif=True, groupe__name=ROLE_ADMIN_CENTRE
    ).exists()
    type_voulu = (Utilisateur.TypeCompte.ADMIN_CENTRE if admin_ailleurs
                  else Utilisateur.TypeCompte.AGENT)
    if compte.type_compte in (Utilisateur.TypeCompte.ADMIN_CENTRE, Utilisateur.TypeCompte.AGENT) \
            and compte.type_compte != type_voulu:
        compte.type_compte = type_voulu
        compte.save()
    return affectation
