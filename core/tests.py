from zoneinfo import ZoneInfo

from django.test import TestCase
from django.utils import timezone

from comptes.models import Utilisateur, AffectationUtilisateur
from centres.models import Centre, Service
from core.managers import CentreQuerySet
from core.timeutils import (
    fuseau_horaire_centre,
    maintenant_centre,
    convertir_heure_centre,
)


class CoreManagerTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.centre_a = Centre.objects.create(
            code="TST",
            nom="Centre test A",
        )
        cls.centre_b = Centre.objects.create(
            code="TMM",
            nom="Centre test B",
        )

        cls.service_a = Service.objects.create(
            centre=cls.centre_a,
            code="ACC",
            nom="Accueil",
        )
        cls.service_b = Service.objects.create(
            centre=cls.centre_b,
            code="ACC",
            nom="Accueil",
        )

        cls.utilisateur = Utilisateur.objects.create_user(
            username="agent_test",
            password="test-password",
        )

        AffectationUtilisateur.objects.create(
            utilisateur=cls.utilisateur,
            centre=cls.centre_a,
            role="AGENT",
        )

    def test_pour_centre_filtre_correctement(self):
        queryset = CentreQuerySet(
            model=Service,
            using="default",
        )

        resultats = queryset.pour_centre(self.centre_a)

        self.assertEqual(
            list(resultats),
            [self.service_a],
        )

    def test_pour_centre_sans_centre_retourne_vide(self):
        queryset = CentreQuerySet(
            model=Service,
            using="default",
        )

        self.assertEqual(
            queryset.pour_centre(None).count(),
            0,
        )

    def test_pour_utilisateur_limite_au_centre_actif(self):
        queryset = CentreQuerySet(
            model=Service,
            using="default",
        )

        resultats = queryset.pour_utilisateur(
            self.utilisateur,
            self.centre_a,
        )

        self.assertEqual(
            list(resultats),
            [self.service_a],
        )

    def test_pour_utilisateur_refuse_un_centre_non_autorise(self):
        queryset = CentreQuerySet(
            model=Service,
            using="default",
        )

        resultats = queryset.pour_utilisateur(
            self.utilisateur,
            self.centre_b,
        )

        self.assertEqual(resultats.count(), 0)


class CoreTimeUtilsTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(
            code="TST",
            nom="Centre test",
            fuseau_horaire="Indian/Antananarivo",
        )

    def test_recupere_fuseau_horaire(self):
        resultat = fuseau_horaire_centre(self.centre)

        self.assertEqual(
            resultat,
            ZoneInfo("Indian/Antananarivo"),
        )

    def test_maintenant_centre_utilise_le_bon_fuseau(self):
        resultat = maintenant_centre(self.centre)

        self.assertEqual(
            resultat.tzinfo,
            ZoneInfo("Indian/Antananarivo"),
        )

    def test_convertit_une_date_vers_le_fuseau_du_centre(self):
        date_heure = timezone.now()

        resultat = convertir_heure_centre(
            date_heure,
            self.centre,
        )

        self.assertEqual(
            resultat.tzinfo,
            ZoneInfo("Indian/Antananarivo"),
        )

    def test_refuse_une_date_sans_fuseau_horaire(self):
        date_heure = timezone.make_aware(
            timezone.datetime(2026, 11, 18, 10, 0),
        ).replace(tzinfo=None)

        with self.assertRaises(ValueError):
            convertir_heure_centre(
                date_heure,
                self.centre,
            )

    def test_refuse_un_centre_sans_fuseau(self):
        with self.assertRaises(ValueError):
            fuseau_horaire_centre(None)
