import datetime
from io import StringIO
from unittest import mock

from django.contrib.auth.models import Group
from django.core.management import call_command
from django.utils import timezone

from centres.models import Centre, Service
from comptes.management.commands.initialiser_roles import initialiser_roles
from comptes.models import AffectationService, AffectationUtilisateur, Utilisateur
from parcours.models import Parcours

from circuit import services
from circuit.models import EntreeFile, MotifDemande, Ticket

# Lundi 16 novembre 2026, 09:00 à Antananarivo (UTC+3).
LUNDI_MATIN = datetime.datetime(2026, 11, 16, 6, 0, tzinfo=datetime.timezone.utc)


class Donnees:
    """Construit un centre complet (services, parcours, quotas, agents)."""

    def construire(self, code="TAM", medecins=2):
        initialiser_roles()
        centre = Centre.objects.create(code=code, nom=f"Centre {code}")
        with mock.patch("django.utils.timezone.now", return_value=LUNDI_MATIN - datetime.timedelta(days=30)):
            call_command("initialiser_parcours", code, "--medecins", str(medecins), stdout=StringIO())
        self.centre = centre
        self.svc = {s.code: s for s in Service.objects.filter(centre=centre)}
        self.parcours = {p.prefixe: p for p in Parcours.objects.filter(centre=centre)}
        self.motif = MotifDemande.objects.get(centre=centre, libelle="Rendez-vous")
        return self

    def agent(self, username, role, services_codes=None, centre=None):
        centre = centre or self.centre
        utilisateur = Utilisateur.objects.create_user(username=username, password="mot-de-passe-1")
        affectation = AffectationUtilisateur.objects.create(
            utilisateur=utilisateur, centre=centre, groupe=Group.objects.get(name=role)
        )
        for code in services_codes or []:
            AffectationService.objects.create(
                affectation=affectation, service=Service.objects.get(centre=centre, code=code)
            )
        return utilisateur

    def file(self, code):
        return self.svc[code].file_par_defaut()

    def entree(self, ticket):
        return EntreeFile.objects.filter(
            passage__ticket=ticket, statut__in=["EN_ATTENTE", "APPELE", "EN_COURS"]
        ).get()

    def ticket(self, prefixe="M", mode=Ticket.Mode.EXTERNE):
        return services.emettre_ticket(self.centre, mode, parcours=self.parcours[prefixe])

    def traiter(self, ticket, code_service, agent, transferer=False, motif=""):
        """Appelle, prend en charge et termine le patient dans un service."""
        file = self.file(code_service)
        entree = self.entree(ticket)
        assert entree.file_id == file.pk, f"{ticket} attendu en {code_service}, trouvé en {entree.file}"
        services.appeler(file, agent, entree=entree)
        services.prendre_en_charge(entree, agent)
        return services.terminer(entree, agent, transferer_pharmacie=transferer, motif=motif)

    def accueillir(self, ticket, agent, **kwargs):
        """Accueil : appel, prise en charge, validation du ticket (aucune info patient)."""
        file = self.file("ACC")
        entree = self.entree(ticket)
        services.appeler(file, agent, entree=entree)
        services.prendre_en_charge(entree, agent)
        return services.terminer(entree, agent, **kwargs)


def a_heure(date_heure):
    return mock.patch("django.utils.timezone.now", return_value=date_heure)


def maintenant_plus(secondes):
    return mock.patch(
        "django.utils.timezone.now",
        return_value=timezone.now() + datetime.timedelta(seconds=secondes),
    )
