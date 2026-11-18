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
    "Direction": ["circuit.voir_statistiques"],
    "Responsable": APPEL + [
        "circuit.annuler_ticket",
        "circuit.orienter_demande",
        "circuit.voir_statistiques",
        "parcours.liberer_quota",
        "parcours.deroger_quota",
    ],
}


def permission(code):
    app, codename = code.split(".")
    return Permission.objects.get(content_type__app_label=app, codename=codename)


def initialiser_roles():
    resultats = {}
    for nom, codes in ROLES.items():
        groupe, _ = Group.objects.get_or_create(name=nom)
        groupe.permissions.set([permission(code) for code in codes])
        resultats[nom] = len(codes)
    return resultats


class Command(BaseCommand):
    help = "Crée les rôles (groupes) et leurs permissions métier."

    def handle(self, *args, **options):
        for nom, nombre in initialiser_roles().items():
            self.stdout.write(f"  Rôle {nom} : {nombre} permissions")
        self.stdout.write(self.style.SUCCESS("Rôles initialisés."))
