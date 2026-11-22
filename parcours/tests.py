import datetime
from io import StringIO
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from centres.models import Centre, Service
from comptes.models import Utilisateur

from .models import (
    EtapeParcours,
    ModificationQuota,
    Parcours,
    ParcoursVersion,
    QuotaParcours,
)
from .quotas import enregistrer_quota, periode_pour, quota_applicable

TANA = ZoneInfo("Indian/Antananarivo")


def heure_tana(annee, mois, jour, heure, minute=0):
    return datetime.datetime(annee, mois, jour, heure, minute, tzinfo=TANA)


class BaseParcoursTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code="TAM", nom="Centre TAM")
        self.accueil = Service.objects.create(
            centre=self.centre, code="ACC", nom="Accueil", est_accueil=True
        )
        self.dentiste = Service.objects.create(
            centre=self.centre, code="DEN", nom="Dentiste"
        )
        self.pharmacie = Service.objects.create(
            centre=self.centre, code="PHA", nom="Pharmacie", est_pharmacie=True
        )
        self.parcours = Parcours.objects.create(
            centre=self.centre,
            code="DENTISTE",
            prefixe="D",
            libelle_borne="Dentiste",
            soumis_quota=True,
        )

    def ajouter_etape(self, version, ordre, service):
        return EtapeParcours.objects.create(
            version=version,
            ordre=ordre,
            service=service,
            file=service.file_par_defaut(),
        )

    def version_publiee(self):
        brouillon = self.parcours.creer_brouillon()
        self.ajouter_etape(brouillon, 1, self.accueil)
        self.ajouter_etape(brouillon, 2, self.dentiste)
        return brouillon.publier()


class ParcoursTests(BaseParcoursTests):
    def test_prefixe_unique_par_centre(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Parcours.objects.create(
                centre=self.centre, code="AUTRE", prefixe="D", libelle_borne="X"
            )

    def test_prefixe_une_seule_lettre(self):
        parcours = Parcours(
            centre=self.centre, code="X", prefixe="d", libelle_borne="X"
        )
        with self.assertRaises(ValidationError):
            parcours.full_clean()


class VersionTests(BaseParcoursTests):
    def test_publication(self):
        version = self.version_publiee()

        self.assertEqual(version.statut, ParcoursVersion.Statut.PUBLIEE)
        self.assertIsNotNone(version.date_publication)
        self.assertEqual(self.parcours.version_publiee(), version)

    def test_premiere_etape_doit_etre_l_accueil(self):
        brouillon = self.parcours.creer_brouillon()
        self.ajouter_etape(brouillon, 1, self.dentiste)

        with self.assertRaises(ValidationError):
            brouillon.publier()

    def test_version_vide_non_publiable(self):
        brouillon = self.parcours.creer_brouillon()

        with self.assertRaises(ValidationError):
            brouillon.publier()

    def test_pharmacie_jamais_etape(self):
        brouillon = self.parcours.creer_brouillon()

        with self.assertRaises(ValidationError):
            self.ajouter_etape(brouillon, 1, self.pharmacie)

    def test_service_d_un_autre_centre_refuse(self):
        autre = Centre.objects.create(code="TMM", nom="Centre TMM")
        service_autre = Service.objects.create(centre=autre, code="DEN", nom="Dentiste")
        brouillon = self.parcours.creer_brouillon()

        with self.assertRaises(ValidationError):
            self.ajouter_etape(brouillon, 1, service_autre)

    def test_file_d_un_autre_service_refusee(self):
        brouillon = self.parcours.creer_brouillon()

        with self.assertRaises(ValidationError):
            EtapeParcours.objects.create(
                version=brouillon,
                ordre=1,
                service=self.accueil,
                file=self.dentiste.file_par_defaut(),
            )

    def test_version_publiee_immuable(self):
        version = self.version_publiee()

        with self.assertRaises(ValidationError):
            self.ajouter_etape(version, 3, self.accueil)

        etape = version.etapes.first()
        etape.ordre = 9
        with self.assertRaises(ValidationError):
            etape.save()

        with self.assertRaises(ValidationError):
            etape.delete()

        version.numero = 42
        with self.assertRaises(ValidationError):
            version.save()

        version.refresh_from_db()
        with self.assertRaises(ValidationError):
            version.delete()

    def test_nouvelle_version_recopie_les_etapes_et_archive_l_ancienne(self):
        v1 = self.version_publiee()

        v2 = self.parcours.creer_brouillon()
        self.assertEqual(v2.numero, 2)
        self.assertEqual(
            list(v2.etapes.values_list("ordre", "service_id")),
            list(v1.etapes.values_list("ordre", "service_id")),
        )

        # Un seul brouillon à la fois.
        self.assertEqual(self.parcours.creer_brouillon(), v2)

        v2.publier()
        v1.refresh_from_db()

        self.assertEqual(v1.statut, ParcoursVersion.Statut.ARCHIVEE)
        self.assertEqual(self.parcours.version_publiee(), v2)
        # L'ancienne version garde ses étapes pour les tickets déjà émis.
        self.assertEqual(v1.etapes.count(), 2)

    def test_brouillon_supprimable(self):
        brouillon = self.parcours.creer_brouillon()
        self.ajouter_etape(brouillon, 1, self.accueil)

        brouillon.delete()

        self.assertFalse(ParcoursVersion.objects.filter(pk=brouillon.pk).exists())


class QuotaTests(BaseParcoursTests):
    def creer_quota(self, jour, periode, places, debut, fin=None):
        return QuotaParcours.objects.create(
            parcours=self.parcours,
            jour_semaine=jour,
            periode=periode,
            nombre_places=places,
            date_debut=debut,
            date_fin=fin,
        )

    def test_periode_selon_l_heure_de_coupure(self):
        # 18/11/2026 est un mercredi.
        self.assertEqual(
            periode_pour(self.centre, heure_tana(2026, 11, 18, 11, 59)),
            (datetime.date(2026, 11, 18), 2, QuotaParcours.Periode.MATIN),
        )
        self.assertEqual(
            periode_pour(self.centre, heure_tana(2026, 11, 18, 12, 0))[2],
            QuotaParcours.Periode.APRES_MIDI,
        )

    def test_periode_calculee_dans_le_fuseau_du_centre(self):
        # 08:30 UTC = 11:30 à Antananarivo : encore le matin.
        utc = datetime.datetime(2026, 11, 18, 8, 30, tzinfo=datetime.timezone.utc)
        self.assertEqual(periode_pour(self.centre, utc)[2], QuotaParcours.Periode.MATIN)

    def test_quota_applicable(self):
        quota = self.creer_quota(2, "MATIN", 7, datetime.date(2026, 11, 1))
        self.creer_quota(2, "APRES_MIDI", 4, datetime.date(2026, 11, 1))

        self.assertEqual(
            quota_applicable(self.parcours, heure_tana(2026, 11, 18, 9)),
            quota,
        )

    def test_dimanche_ferme(self):
        self.creer_quota(2, "MATIN", 7, datetime.date(2026, 11, 1))

        # 22/11/2026 est un dimanche : aucun quota.
        self.assertIsNone(
            quota_applicable(self.parcours, heure_tana(2026, 11, 22, 9))
        )

    def test_quota_hors_dates_de_validite(self):
        self.creer_quota(
            2, "MATIN", 7, datetime.date(2026, 11, 1), datetime.date(2026, 11, 10)
        )

        self.assertIsNone(
            quota_applicable(self.parcours, heure_tana(2026, 11, 18, 9))
        )

    def test_chevauchement_refuse_par_la_validation(self):
        self.creer_quota(0, "MATIN", 7, datetime.date(2026, 11, 1))

        quota = QuotaParcours(
            parcours=self.parcours,
            jour_semaine=0,
            periode="MATIN",
            nombre_places=5,
            date_debut=datetime.date(2026, 12, 1),
        )

        with self.assertRaises(ValidationError):
            quota.full_clean()

    def test_chevauchement_refuse_par_postgresql(self):
        self.creer_quota(0, "MATIN", 7, datetime.date(2026, 11, 1))

        with self.assertRaises(IntegrityError), transaction.atomic():
            self.creer_quota(0, "MATIN", 5, datetime.date(2026, 12, 1))

    def test_succession_de_quotas_autorisee(self):
        self.creer_quota(
            0, "MATIN", 7, datetime.date(2026, 11, 1), datetime.date(2026, 11, 30)
        )
        self.creer_quota(0, "MATIN", 8, datetime.date(2026, 12, 1))

        self.assertEqual(
            quota_applicable(self.parcours, heure_tana(2026, 12, 7, 9)).nombre_places,
            8,
        )

    def test_modification_historisee(self):
        responsable = Utilisateur.objects.create_user(
            username="direction", password="MotDePasseTest123!"
        )
        quota = enregistrer_quota(
            QuotaParcours(
                parcours=self.parcours,
                jour_semaine=0,
                periode="MATIN",
                nombre_places=7,
                date_debut=datetime.date(2026, 11, 1),
            ),
            responsable,
        )

        quota.nombre_places = 9
        enregistrer_quota(quota, responsable)

        historique = list(ModificationQuota.objects.order_by("date", "pk"))

        self.assertEqual(len(historique), 2)
        self.assertEqual(historique[0].action, "CREATION")
        self.assertEqual(historique[1].action, "MODIFICATION")
        self.assertEqual(historique[1].auteur, responsable)
        self.assertEqual(historique[1].valeurs_avant["nombre_places"], 7)
        self.assertEqual(historique[1].valeurs_apres["nombre_places"], 9)


class CommandeInitialisationTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code="TAM", nom="Centre TAM")

    def lancer(self, *args):
        call_command("initialiser_parcours", *args, stdout=StringIO())

    def test_initialisation_complete(self):
        self.lancer("TAM", "--medecins", "3")

        self.assertEqual(self.centre.services.count(), 7)   # + médecin en chef
        self.assertEqual(
            set(Parcours.objects.values_list("prefixe", flat=True)),
            {"M", "D", "I", "E", "C"},
        )

        medecin = Parcours.objects.get(prefixe="M")
        self.assertEqual(
            list(
                medecin.version_publiee()
                .etapes.order_by("ordre")
                .values_list("service__code", flat=True)
            ),
            ["ACC", "INF", "MED"],
        )

        self.assertEqual(
            Service.objects.get(code="MED").file_par_defaut().nb_appels_simultanes,
            3,
        )

        dentiste = Parcours.objects.get(prefixe="D")
        self.assertTrue(dentiste.soumis_quota)
        self.assertEqual(dentiste.quotas.count(), 11)
        self.assertEqual(
            dentiste.quotas.get(jour_semaine=5, periode="MATIN").nombre_places, 4
        )
        self.assertFalse(
            dentiste.quotas.filter(jour_semaine=5, periode="APRES_MIDI").exists()
        )

    def test_relance_sans_doublon(self):
        self.lancer("TAM")
        self.lancer("TAM")

        self.assertEqual(self.centre.services.count(), 7)   # + médecin en chef
        self.assertEqual(ParcoursVersion.objects.count(), 5)
        self.assertEqual(QuotaParcours.objects.count(), 11)

    def test_reutilise_un_service_existant_au_code_different(self):
        Service.objects.create(
            centre=self.centre, code="ACCUEIL", nom="Accueil", est_accueil=True
        )

        self.lancer("TAM")

        self.assertEqual(self.centre.services.filter(est_accueil=True).count(), 1)
        self.assertEqual(self.centre.services.count(), 7)   # + médecin en chef
