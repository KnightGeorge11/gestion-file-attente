import datetime

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase

from .models import Borne, Centre, FileAttente, HoraireService, Service


class ParametresCentreTests(TestCase):
    def test_valeurs_par_defaut(self):
        centre = Centre.objects.create(code="TAM", nom="Centre TAM")

        self.assertEqual(centre.heure_coupure_matin, datetime.time(12, 0))
        self.assertEqual(centre.heure_cloture_tickets, datetime.time(20, 0))  # décision du 18/11/2026
        self.assertEqual(centre.max_reintegrations, 1)
        self.assertEqual(centre.max_tickets_ouverts_par_patient, 1)


class ServiceEtFileTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code="TAM", nom="Centre TAM")
        self.autre_centre = Centre.objects.create(code="TMM", nom="Centre TMM")

    def test_creation_service_cree_une_file_par_defaut(self):
        service = Service.objects.create(
            centre=self.centre,
            code="DEN",
            nom="Dentiste",
        )

        files = list(service.files.all())

        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].par_defaut)
        self.assertEqual(files[0].centre, self.centre)
        self.assertEqual(files[0].nb_appels_avant_absence, 3)
        self.assertEqual(files[0].nb_appels_simultanes, 2)  # décision du 18/11/2026 : 2 agents minimum
        self.assertEqual(service.file_par_defaut(), files[0])

    def test_modification_service_ne_cree_pas_de_seconde_file(self):
        service = Service.objects.create(
            centre=self.centre,
            code="DEN",
            nom="Dentiste",
        )

        service.nom = "Cabinet dentaire"
        service.save()

        self.assertEqual(service.files.count(), 1)

    def test_une_seule_file_par_defaut_par_service(self):
        service = Service.objects.create(
            centre=self.centre,
            code="MED",
            nom="Médecins",
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            FileAttente.objects.create(
                centre=self.centre,
                service=service,
                code="MED2",
                nom="Autre file",
                par_defaut=True,
            )

    def test_file_d_un_autre_centre_refusee_par_la_validation(self):
        service = Service.objects.create(
            centre=self.centre,
            code="MED",
            nom="Médecins",
        )

        file = FileAttente(
            centre=self.autre_centre,
            service=service,
            code="X",
            nom="File incohérente",
        )

        with self.assertRaises(ValidationError):
            file.full_clean()

    def test_file_d_un_autre_centre_refusee_par_postgresql(self):
        service = Service.objects.create(
            centre=self.centre,
            code="MED",
            nom="Médecins",
        )

        with self.assertRaises(Exception), transaction.atomic():
            FileAttente.objects.create(
                centre=self.autre_centre,
                service=service,
                code="X",
                nom="File incohérente",
            )

    def test_suspension_exige_un_motif(self):
        service = Service.objects.create(
            centre=self.centre,
            code="ECH",
            nom="Échographie",
        )
        service.suspendu = True

        with self.assertRaises(ValidationError):
            service.full_clean()

        service.motif_suspension = "Appareil en maintenance"
        service.full_clean()
        self.assertFalse(service.disponible)

    def test_le_trigger_est_installe(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM pg_trigger "
                "WHERE tgname = 'fileattente_coherence_centre'"
            )
            self.assertIsNotNone(cursor.fetchone())


class HoraireServiceTests(TestCase):
    def setUp(self):
        centre = Centre.objects.create(code="TAM", nom="Centre TAM")
        self.service = Service.objects.create(
            centre=centre,
            code="DEN",
            nom="Dentiste",
        )

    def test_plusieurs_plages_le_meme_jour(self):
        matin = HoraireService(
            service=self.service,
            jour_semaine=0,
            heure_debut=datetime.time(7, 30),
            heure_fin=datetime.time(12, 0),
        )
        matin.full_clean()
        matin.save()

        apres_midi = HoraireService(
            service=self.service,
            jour_semaine=0,
            heure_debut=datetime.time(13, 30),
            heure_fin=datetime.time(16, 0),
        )
        apres_midi.full_clean()
        apres_midi.save()

        self.assertEqual(self.service.horaires.count(), 2)

    def test_chevauchement_refuse(self):
        HoraireService.objects.create(
            service=self.service,
            jour_semaine=0,
            heure_debut=datetime.time(7, 30),
            heure_fin=datetime.time(12, 0),
        )

        chevauchante = HoraireService(
            service=self.service,
            jour_semaine=0,
            heure_debut=datetime.time(11, 0),
            heure_fin=datetime.time(14, 0),
        )

        with self.assertRaises(ValidationError):
            chevauchante.full_clean()

    def test_fin_avant_debut_refusee_par_la_base(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            HoraireService.objects.create(
                service=self.service,
                jour_semaine=0,
                heure_debut=datetime.time(12, 0),
                heure_fin=datetime.time(8, 0),
            )


class BorneTests(TestCase):
    def setUp(self):
        self.centre = Centre.objects.create(code="TAM", nom="Centre TAM")

    def test_cle_stockee_sous_forme_d_empreinte(self):
        borne = Borne(centre=self.centre, code="B1", libelle="Borne entrée")
        cle = borne.generer_cle()
        borne.save()

        borne.refresh_from_db()

        self.assertNotEqual(borne.cle_hash, cle)
        self.assertTrue(borne.verifier_cle(cle))
        self.assertFalse(borne.verifier_cle("mauvaise-cle"))
        self.assertFalse(borne.verifier_cle(""))

    def test_regeneration_invalide_l_ancienne_cle(self):
        borne = Borne(centre=self.centre, code="B1", libelle="Borne entrée")
        ancienne = borne.generer_cle()
        nouvelle = borne.generer_cle()

        self.assertFalse(borne.verifier_cle(ancienne))
        self.assertTrue(borne.verifier_cle(nouvelle))

    def test_code_unique_par_centre(self):
        Borne.objects.create(centre=self.centre, code="B1", libelle="A")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Borne.objects.create(centre=self.centre, code="B1", libelle="B")
