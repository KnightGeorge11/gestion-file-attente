"""
Crée des comptes de TEST : 2 comptes par service et par rôle, affectés à
chaque centre (l'agent choisit son centre actif à la connexion).

Usage :
    python manage.py creer_comptes_test                      # tous les centres actifs, mot de passe 1234
    python manage.py creer_comptes_test --centres 001 002
    python manage.py creer_comptes_test --mot-de-passe autre

Comptes créés (n = 1, 2) :
    accueil<n>, infirmier<n>, medecin<n>, chef1 (un seul), dentiste<n>, echographie<n>,
    pharmacie<n>, direction<n>, responsable<n>, admin<n> (administrateur de centre)
(noms raccourcis le 19/11/2026 : les anciens comptes « …test<n> » sont
renommés, leur historique est conservé).

Ces comptes ont un mot de passe faible : la commande refuse de s'exécuter
quand DEBUG est désactivé (production), sauf avec --force.
La commande peut être relancée : rien n'est dupliqué, le mot de passe est
remis à la valeur demandée.
"""

from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from centres.models import Centre, Service
from comptes.management.commands.initialiser_roles import initialiser_roles
from comptes.models import AffectationService, AffectationUtilisateur, Utilisateur

NOMBRE_PAR_SERVICE = 2

# préfixe de l'identifiant, rôle, code du service (None = tout le centre)
COMPTES = [
    ("accueil", "Accueil", "ACC"),
    ("infirmier", "Infirmerie", "INF"),
    ("medecin", "Médecin", "MED"),
    ("dentiste", "Dentiste", "DEN"),
    ("echographie", "Échographie", "ECH"),
    ("pharmacie", "Pharmacie", "PHA"),
    ("chef", "Médecin", "MEC"),          # un seul médecin en chef (décision du 22/11/2026)
    ("direction", "Direction", None),
    ("responsable", "Responsable", None),
    ("admin", "Administrateur de centre", None),
]


# Comptes uniques : un seul médecin en chef (décision du 22/11/2026).
NOMBRE_PAR_PREFIXE = {"chef": 1}


def _nombre(prefixe):
    return NOMBRE_PAR_PREFIXE.get(prefixe, NOMBRE_PAR_SERVICE)


def identifiants_test():
    return [f"{prefixe}{n}" for prefixe, _, _ in COMPTES for n in range(1, _nombre(prefixe) + 1)]


class Command(BaseCommand):
    help = "Crée 2 comptes de test par service (mot de passe par défaut : 1234)."

    def add_arguments(self, parser):
        parser.add_argument("--centres", nargs="*", help="Codes des centres (défaut : tous les centres actifs).")
        parser.add_argument("--mot-de-passe", default="1234")
        parser.add_argument("--force", action="store_true",
                            help="Autoriser la création même si DEBUG est désactivé.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "DEBUG est désactivé (production) : comptes de test à mot de passe faible refusés. "
                "Utilisez --force uniquement sur une base de test."
            )

        centres = Centre.objects.filter(actif=True)
        if options["centres"]:
            centres = centres.filter(code__in=options["centres"])
        centres = list(centres.order_by("code"))
        if not centres:
            raise CommandError("Aucun centre trouvé.")

        initialiser_roles()
        for centre in centres:
            # Services, parcours et quotas (sans effet s'ils existent déjà).
            call_command("initialiser_parcours", centre.code, stdout=self.stdout)

        with transaction.atomic():
            for prefixe, role, code_service in COMPTES:
                groupe = Group.objects.get(name=role)
                for n in range(1, _nombre(prefixe) + 1):
                    username = f"{prefixe}{n}"
                    # Anciens noms : « …test<n> », puis « admincentre<n> » (19/11/2026).
                    anciens = [f"{prefixe}test{n}"] + ([f"admincentre{n}"] if prefixe == "admin" else [])
                    ancien = Utilisateur.objects.filter(username__in=anciens).first()
                    if ancien is not None and not Utilisateur.objects.filter(username=username).exists():
                        ancien.username = username          # ancien nom « …test<n> »
                        ancien.save(update_fields=["username"])
                    existant = Utilisateur.objects.filter(username=username).first()
                    if existant is not None and (
                        existant.is_superuser or existant.type_compte == Utilisateur.TypeCompte.ADMINISTRATEUR
                    ):
                        # Vrai compte de la plateforme qui porte ce nom : jamais
                        # remis à un mot de passe de test (relecture du 23/11/2026).
                        self.stdout.write(f"  {username:<14} ignoré : compte de la plateforme")
                        continue
                    utilisateur, cree = Utilisateur.objects.get_or_create(username=username)
                    utilisateur.first_name, utilisateur.last_name = role, str(n)
                    if prefixe == "admin":
                        utilisateur.type_compte = Utilisateur.TypeCompte.ADMIN_CENTRE
                    utilisateur.is_active = True
                    utilisateur.set_password(options["mot_de_passe"])
                    utilisateur.save()
                    for centre in centres:
                        affectation, _ = AffectationUtilisateur.objects.get_or_create(
                            utilisateur=utilisateur, centre=centre, defaults={"groupe": groupe}
                        )
                        if not affectation.actif or affectation.groupe_id != groupe.pk:
                            affectation.actif = True
                            affectation.groupe = groupe
                            affectation.save()
                        if code_service:
                            service = Service.objects.get(centre=centre, code=code_service)
                            AffectationService.objects.get_or_create(affectation=affectation, service=service)
                    self.stdout.write(f"  {username:<14} {role:<25} {'créé' if cree else 'mis à jour'}")

            # Agent désigné pour les demandes « Autre » des services qui n'en ont pas.
            for centre in centres:
                for code_service, prefixe in (("MED", "medecin"), ("ACC", "accueil"),
                                              ("DEN", "dentiste"), ("ECH", "echographie"),
                                              ("INF", "infirmier"), ("PHA", "pharmacie")):
                    service = Service.objects.get(centre=centre, code=code_service)
                    if service.destinataire_demandes_id is None:
                        service.destinataire_demandes = Utilisateur.objects.get(username=f"{prefixe}1")
                        service.save(update_fields=["destinataire_demandes"])

        codes = ", ".join(c.code for c in centres)
        self.stdout.write(self.style.SUCCESS(
            f"{len(identifiants_test())} comptes de test prêts (centres : {codes}). "
            f"Mot de passe : {options['mot_de_passe']}"
        ))
