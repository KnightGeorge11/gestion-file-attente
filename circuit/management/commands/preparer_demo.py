"""
Prépare un centre pour la recette : rôles, services, parcours, quotas,
motifs du mode Autre, un agent par rôle, une borne et un écran.

Usage :
    python manage.py preparer_demo 001 --mot-de-passe "<mot de passe des agents>"

Les comptes créés s'appellent <role>_<code centre> (ex. accueil_001).
Les clés de la borne et de l'écran sont affichées une seule fois.
La commande peut être relancée : rien n'est dupliqué.
"""

import os

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from centres.models import Borne, Centre, EcranAffichage, Service
from circuit.models import MotifDemande
from comptes.management.commands.initialiser_roles import initialiser_roles
from comptes.models import AffectationService, AffectationUtilisateur, Utilisateur

AGENTS = [
    # identifiant, rôle, services (vide = tous)
    ("accueil", "Accueil", ["ACC"]),
    ("infirmier", "Infirmerie", ["INF"]),
    ("medecin1", "Médecin", ["MED"]),
    ("medecin2", "Médecin", ["MED"]),
    ("dentiste", "Dentiste", ["DEN"]),
    ("echographie", "Échographie", ["ECH"]),
    ("pharmacie", "Pharmacie", ["PHA"]),
    ("direction", "Direction", []),
    ("responsable", "Responsable", ["ACC"]),
]

from circuit.models import MOTIFS_AUTRE_PAR_DEFAUT as MOTIFS  # noqa: E402


class Command(BaseCommand):
    help = "Prépare un centre de démonstration / recette."

    def add_arguments(self, parser):
        parser.add_argument("code_centre")
        parser.add_argument("--mot-de-passe", default=os.getenv("DEMO_MOT_DE_PASSE"))

    def handle(self, *args, **options):
        code = options["code_centre"]
        mot_de_passe = options["mot_de_passe"]
        if not mot_de_passe:
            raise CommandError("Indiquez --mot-de-passe (ou la variable DEMO_MOT_DE_PASSE).")
        centre = Centre.objects.filter(code__iexact=code).first()
        if centre is None:
            raise CommandError(f"Centre « {code} » introuvable.")

        initialiser_roles()
        call_command("initialiser_parcours", centre.code, stdout=self.stdout)

        with transaction.atomic():
            for rang, libelle in enumerate(MOTIFS, start=1):
                MotifDemande.objects.get_or_create(centre=centre, libelle=libelle, defaults={"ordre": rang})

            suffixe = centre.code.lower()
            for identifiant, role, codes in AGENTS:
                username = f"{identifiant}_{suffixe}"
                utilisateur, cree = Utilisateur.objects.get_or_create(
                    username=username, defaults={"first_name": identifiant.capitalize()}
                )
                if cree:
                    utilisateur.set_password(mot_de_passe)
                    utilisateur.save()
                affectation, _ = AffectationUtilisateur.objects.get_or_create(
                    utilisateur=utilisateur, centre=centre,
                    defaults={"groupe": Group.objects.get(name=role)},
                )
                for code_service in codes:
                    AffectationService.objects.get_or_create(
                        affectation=affectation, service=Service.objects.get(centre=centre, code=code_service)
                    )
                self.stdout.write(f"  Agent {username} ({role}) : {'créé' if cree else 'déjà présent'}")

            medecins = Service.objects.get(centre=centre, code="MED")
            if medecins.destinataire_demandes is None:
                medecins.destinataire_demandes = Utilisateur.objects.get(username=f"medecin1_{suffixe}")
                medecins.save(update_fields=["destinataire_demandes"])

            for modele, code_poste, libelle in ((Borne, "BORNE1", "Borne d'entrée"),
                                                (EcranAffichage, "ECRAN1", "Écran principal")):
                if not modele.objects.filter(centre=centre, code=code_poste).exists():
                    poste = modele(centre=centre, code=code_poste, libelle=libelle)
                    cle = poste.generer_cle()
                    poste.save()
                    self.stdout.write(self.style.WARNING(
                        f"  {libelle} : centre {centre.code}, code {code_poste}, clé {cle} (notez-la)"
                    ))

        self.stdout.write(self.style.SUCCESS(f"Centre {centre} prêt pour la recette."))
