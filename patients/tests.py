from django.contrib.auth import get_user_model
from django.test import TestCase

from centres.models import Centre

from .models import Patient, PatientCentre


Utilisateur = get_user_model()


class GenerationMatriculeTests(TestCase):

    def setUp(self):
        self.centre_tana = Centre.objects.create(
            code="TAM",
            nom="Centre Antananarivo",
        )

        self.centre_tamatave = Centre.objects.create(
            code="TMM",
            nom="Centre Toamasina",
        )

        self.patient = Patient.objects.create(
            nom="RAKOTO",
            prenom="Jean",
        )

    def test_premier_matricule_est_genere(self):
        rattachement = PatientCentre.objects.create(
            patient=self.patient,
            centre=self.centre_tana,
        )

        self.assertEqual(
            rattachement.matricule,
            "TAM-000001",
        )

    def test_numerotation_incremente_dans_un_centre(self):
        premier = PatientCentre.objects.create(
            patient=self.patient,
            centre=self.centre_tana,
        )

        deuxieme_patient = Patient.objects.create(
            nom="RABE",
            prenom="Paul",
        )

        deuxieme = PatientCentre.objects.create(
            patient=deuxieme_patient,
            centre=self.centre_tana,
        )

        self.assertEqual(premier.matricule, "TAM-000001")
        self.assertEqual(deuxieme.matricule, "TAM-000002")

    def test_numerotation_independante_par_centre(self):
        rattachement_tana = PatientCentre.objects.create(
            patient=self.patient,
            centre=self.centre_tana,
        )

        rattachement_tamatave = PatientCentre.objects.create(
            patient=self.patient,
            centre=self.centre_tamatave,
        )

        self.assertEqual(
            rattachement_tana.matricule,
            "TAM-000001",
        )
        self.assertEqual(
            rattachement_tamatave.matricule,
            "TMM-000001",
        )

    def test_meme_patient_ne_peut_pas_etre_rattache_deux_fois(self):
        PatientCentre.objects.create(
            patient=self.patient,
            centre=self.centre_tana,
        )

        with self.assertRaises(Exception):
            PatientCentre.objects.create(
                patient=self.patient,
                centre=self.centre_tana,
            )
