from django.contrib.auth.models import Group

from comptes.management.commands.initialiser_roles import initialiser_roles
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from centres.models import Centre
from comptes.models import AffectationUtilisateur

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


class RecherchePatientTests(TestCase):

    def setUp(self):
        self.centre_a = Centre.objects.create(
            code="TAM",
            nom="Centre Antananarivo",
        )

        self.centre_b = Centre.objects.create(
            code="TMM",
            nom="Centre Toamasina",
        )

        self.agent = Utilisateur.objects.create_user(
            username="agent_recherche",
            password="MotDePasseTest123!",
            type_compte="AGENT",
        )

        # Rôle Accueil : la recherche de patients est réservée aux rôles
        # qui identifient les patients.
        initialiser_roles()
        AffectationUtilisateur.objects.create(
            utilisateur=self.agent,
            centre=self.centre_a,
            role="AGENT",
            groupe=Group.objects.get(name="Accueil"),
        )

        self.patient_a = Patient.objects.create(
            nom="Éléonore",
            prenom="Rakoto",
        )

        self.rattachement_a = PatientCentre.objects.create(
            patient=self.patient_a,
            centre=self.centre_a,
        )

        self.patient_b = Patient.objects.create(
            nom="RABE",
            prenom="Paul",
        )

        self.rattachement_b = PatientCentre.objects.create(
            patient=self.patient_b,
            centre=self.centre_b,
        )

        self.client.force_login(self.agent)

    def rechercher(self, terme):
        return self.client.get(
            reverse("patients:recherche"),
            {"q": terme},
        )

    def test_recherche_insensible_aux_accents(self):
        response = self.rechercher("eleonore")

        self.assertEqual(response.status_code, 200)

        resultats = list(response.context["rattachements"])

        self.assertIn(self.rattachement_a, resultats)

    def test_recherche_tolere_une_faute_de_frappe(self):
        response = self.rechercher("eleonre")

        self.assertEqual(response.status_code, 200)

        resultats = list(response.context["rattachements"])

        self.assertIn(self.rattachement_a, resultats)

    def test_recherche_par_matricule_du_centre_actif(self):
        response = self.rechercher("TAM-000001")

        self.assertEqual(response.status_code, 200)

        resultats = list(response.context["rattachements"])

        self.assertIn(self.rattachement_a, resultats)
        self.assertNotIn(self.rattachement_b, resultats)

    def test_patient_d_un_autre_centre_n_est_pas_affiche(self):
        response = self.rechercher("RABE")

        self.assertEqual(response.status_code, 200)

        resultats = list(response.context["rattachements"])

        self.assertNotIn(self.rattachement_b, resultats)

    def test_matricule_d_un_autre_centre_est_inaccessible(self):
        response = self.rechercher("TMM-000001")

        self.assertEqual(response.status_code, 200)

        resultats = list(response.context["rattachements"])

        self.assertEqual(resultats, [])

    def test_patient_archive_n_est_pas_affiche(self):
        self.rattachement_a.archive = True
        self.rattachement_a.save()

        response = self.rechercher("eleonore")

        self.assertEqual(response.status_code, 200)

        resultats = list(response.context["rattachements"])

        self.assertNotIn(self.rattachement_a, resultats)

    def test_centre_actif_est_obligatoire(self):
        administrateur = Utilisateur.objects.create_user(
            username="admin_recherche",
            password="MotDePasseTest123!",
            type_compte="ADMIN",
        )

        self.client.force_login(administrateur)

        response = self.client.get(
            reverse("patients:recherche")
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.url,
            reverse("centres:selection"),
        )   