"""
Crée ou met à jour les rôles (groupes Django) et leurs permissions.

Usage : python manage.py initialiser_roles

La commande peut être relancée : les permissions des rôles sont remises
à la liste de référence ci-dessous. La sécurité n'a aucun rôle.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

APPEL = ["circuit.appeler", "circuit.marquer_absent", "circuit.reintegrer"]
SOINS = APPEL + ["circuit.transferer"]

ROLES = {
    "Accueil": APPEL + [
        "circuit.annuler_ticket",
        "circuit.orienter_demande",
        "parcours.liberer_quota",
    ],
    "Infirmerie": SOINS,
    "Médecin": SOINS,
    "Dentiste": SOINS,
    "Échographie": SOINS,
    "Pharmacie": APPEL,
    # Direction et Responsable n'appellent que les tickets « Autre »
    # (circuit.services.appeler, décision du 19/11/2026). La direction ne
    # voit d'ailleurs que ces tickets (circuit.visibilite).
    "Direction": APPEL + ["circuit.voir_statistiques"],
    # Administrateur d'un centre : paramétrage de son centre dans
    # l'administration (droits Django ci-dessous) ; n'appelle et ne voit que
    # les tickets « Autre ».
    "Administrateur de centre": APPEL + ["circuit.voir_statistiques"],
    "Responsable": APPEL + [
        "circuit.annuler_ticket",
        "circuit.orienter_demande",
        "circuit.voir_statistiques",
        "parcours.liberer_quota",
        "parcours.deroger_quota",
    ],
}


# Droits de l'administration Django du rôle « Administrateur de centre ».
# Tous ces écrans filtrent les données sur les centres de l'administrateur
# (CentreAdminMixin) ; les centres eux-mêmes, les rôles et le journal d'audit
# restent réservés à l'administrateur de la plateforme.
ADMIN_CENTRE = {
    "centres.borne": ("view", "add", "change"),
    "centres.ecranaffichage": ("view", "add", "change"),
    "centres.imprimanteticket": ("view", "add", "change"),
    "centres.service": ("view", "change"),
    "centres.fileattente": ("view", "add", "change"),
    "circuit.motifdemande": ("view", "add", "change"),
    "parcours.parcours": ("view", "change"),
    "parcours.parcoursversion": ("view",),
    "parcours.quotaparcours": ("view", "change"),
    "parcours.modificationquota": ("view",),
    "comptes.utilisateur": ("view", "add", "change"),
    "comptes.affectationutilisateur": ("view", "add", "change"),
    "comptes.affectationservice": ("view", "add", "change", "delete"),
}


def codes_admin_centre():
    return [
        f"{modele.split('.')[0]}.{action}_{modele.split('.')[1]}"
        for modele, actions in ADMIN_CENTRE.items()
        for action in actions
    ]


def permission(code):
    app, codename = code.split(".")
    return Permission.objects.get(content_type__app_label=app, codename=codename)


def initialiser_roles():
    resultats = {}
    for nom, codes in ROLES.items():
        groupe, _ = Group.objects.get_or_create(name=nom)
        if nom == "Administrateur de centre":
            codes = codes + codes_admin_centre()
        groupe.permissions.set([permission(code) for code in codes])
        resultats[nom] = len(codes)
    return resultats


class Command(BaseCommand):
    help = "Crée les rôles (groupes) et leurs permissions métier."

    def handle(self, *args, **options):
        for nom, nombre in initialiser_roles().items():
            self.stdout.write(f"  Rôle {nom} : {nombre} permissions")
        self.stdout.write(self.style.SUCCESS("Rôles initialisés."))
