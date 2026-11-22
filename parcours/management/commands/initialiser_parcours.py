"""
Configure un centre avec les services, parcours et quotas confirmés.

Usage :
    python manage.py initialiser_parcours TAM
    python manage.py initialiser_parcours TAM --medecins 3

La commande peut être relancée sans risque : ce qui existe déjà
n'est ni dupliqué ni modifié.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from centres.models import Centre, Service
from core.timeutils import maintenant_centre
from parcours.models import Parcours, QuotaParcours
from parcours.quotas import enregistrer_quota

SERVICES = [
    # code, nom, ordre, options
    ("ACC", "Accueil", 1, {"est_accueil": True, "accepte_transferts": False}),
    ("INF", "Infirmerie", 2, {}),
    ("MED", "Médecin", 3, {"envoie_medecin_chef": True}),   # nom annoncé : « M 0 0 1, Médecin, 1 » (décision du 21/11/2026)
    ("DEN", "Dentiste", 4, {}),
    ("ECH", "Échographie", 5, {}),
    ("PHA", "Pharmacie", 6, {"est_pharmacie": True, "appel_oral_autorise": True}),
    # Médecin en chef (décision du 22/11/2026) : patients envoyés par les
    # médecins, ou ticket délivré par l'accueil à la borne qu'il tient.
    ("MEC", "Médecin en chef", 7, {"est_medecin_chef": True}),
]

PARCOURS = [
    # code, préfixe, libellé, ordre, soumis_quota, étapes
    ("MEDECIN", "M", "Médecin", 1, False, ["ACC", "INF", "MED"]),
    ("DENTISTE", "D", "Dentiste", 2, True, ["ACC", "DEN"]),
    ("INFIRMERIE", "I", "Infirmerie", 3, False, ["ACC", "INF"]),
    ("ECHOGRAPHIE", "E", "Échographie", 4, False, ["ACC", "ECH"]),
    # Proposé seulement sur la borne tenue par l'accueil (jamais en libre-service).
    ("MEDECIN_CHEF", "C", "Médecin en chef", 5, False, ["ACC", "MEC"]),
]

PARCOURS_RESERVES_ACCUEIL = {"MEDECIN_CHEF"}

# Quotas du dentiste confirmés : 7 le matin et 4 l'après-midi
# du lundi au vendredi, 4 le samedi matin. Fermé samedi après-midi
# et dimanche (aucun quota = aucune identification possible).
QUOTAS_DENTISTE = (
    [(jour, QuotaParcours.Periode.MATIN, 7) for jour in range(5)]
    + [(jour, QuotaParcours.Periode.APRES_MIDI, 4) for jour in range(5)]
    + [(5, QuotaParcours.Periode.MATIN, 4)]
)


class Command(BaseCommand):
    help = "Crée les services, parcours publiés et quotas du dentiste d'un centre."

    def add_arguments(self, parser):
        parser.add_argument("code_centre", help="Code du centre (ex. TAM).")
        parser.add_argument(
            "--medecins",
            type=int,
            default=2,
            help="Nombre de médecins pouvant appeler en même temps (file commune).",
        )

    def handle(self, *args, **options):
        code = options["code_centre"].upper()
        medecins = options["medecins"]

        if medecins < 2:
            raise CommandError("--medecins doit être supérieur ou égal à 2 (2 agents minimum par service).")

        try:
            centre = Centre.objects.get(code=code)
        except Centre.DoesNotExist:
            raise CommandError(
                f"Centre « {code} » introuvable. Créez-le d'abord dans l'administration."
            )

        with transaction.atomic():
            services = self._services(centre, medecins)
            self._parcours(centre, services)
            self._motifs_autre(centre)

        self.stdout.write(self.style.SUCCESS(f"Centre {centre} initialisé."))

    def _services(self, centre, medecins):
        services = {}

        for code, nom, ordre, options in SERVICES:
            service = self._service_existant(centre, code, nom, options)
            cree = service is None

            if cree:
                service = Service.objects.create(
                    centre=centre,
                    code=code,
                    nom=nom,
                    ordre=ordre,
                    **options,
                )

            services[code] = service
            self._log(cree, f"Service {nom}" + ("" if cree else f" (code {service.code})"))

        # File des demandes « Autre » de l'accueil, séparée des patients (décision du 22/11/2026).
        from circuit.services import file_demandes_autre

        file_demandes_autre(services["ACC"])

        # File commune des médecins : plusieurs médecins en parallèle.
        file_medecins = services["MED"].file_par_defaut()

        if file_medecins.nb_appels_simultanes < medecins:
            file_medecins.nb_appels_simultanes = medecins
            file_medecins.save(update_fields=["nb_appels_simultanes", "date_modification"])
            self.stdout.write(f"  File commune des médecins : {medecins} appels simultanés")

        return services

    def _service_existant(self, centre, code, nom, options):
        """Retrouve un service déjà créé à la main (par code, nom ou marqueur)."""
        existants = Service.objects.filter(centre=centre)

        service = existants.filter(code__iexact=code).first()
        service = service or existants.filter(nom__iexact=nom).first()

        if service is None and options.get("est_accueil"):
            service = existants.filter(est_accueil=True).first()

        if service is None and options.get("est_pharmacie"):
            service = existants.filter(est_pharmacie=True).first()

        return service

    def _parcours(self, centre, services):
        aujourd_hui = maintenant_centre(centre).date()

        for code, prefixe, libelle, ordre, soumis_quota, etapes in PARCOURS:
            parcours, cree = Parcours.objects.get_or_create(
                centre=centre,
                code=code,
                defaults={
                    "prefixe": prefixe,
                    "libelle_borne": libelle,
                    "ordre_affichage": ordre,
                    "soumis_quota": soumis_quota,
                    "reserve_accueil": code in PARCOURS_RESERVES_ACCUEIL,
                },
            )
            self._log(cree, f"Parcours {prefixe} - {libelle}")

            if parcours.version_publiee() is None:
                brouillon = parcours.creer_brouillon()

                if not brouillon.etapes.exists():
                    for rang, code_service in enumerate(etapes, start=1):
                        service = services[code_service]
                        brouillon.etapes.create(
                            ordre=rang,
                            service=service,
                            file=service.file_par_defaut(),
                        )

                brouillon.publier()
                self.stdout.write(f"  Version v{brouillon.numero} publiée")

            if soumis_quota and not parcours.quotas.exists():
                for jour, periode, places in QUOTAS_DENTISTE:
                    enregistrer_quota(
                        QuotaParcours(
                            parcours=parcours,
                            jour_semaine=jour,
                            periode=periode,
                            nombre_places=places,
                            date_debut=aujourd_hui,
                        ),
                        auteur=None,
                    )
                self.stdout.write(f"  Quotas {libelle} créés")

    def _motifs_autre(self, centre):
        """Motifs du mode Autre proposés sur la borne (ajoutés s'ils manquent)."""
        from circuit.models import MOTIFS_AUTRE_PAR_DEFAUT, MotifDemande

        for rang, libelle in enumerate(MOTIFS_AUTRE_PAR_DEFAUT, start=1):
            # Tolère d'éventuels doublons déjà saisis : on ne recrée rien.
            motif = MotifDemande.objects.filter(centre=centre, libelle=libelle).order_by("pk").first()
            cree = motif is None
            if cree:
                motif = MotifDemande.objects.create(centre=centre, libelle=libelle, ordre=rang)
            elif motif.ordre != rang:
                motif.ordre = rang
                motif.save(update_fields=["ordre"])
            self._log(cree, f"Motif Autre « {libelle} »")

    def _log(self, cree, libelle):
        etat = "créé" if cree else "déjà présent"
        self.stdout.write(f"  {libelle} : {etat}")
