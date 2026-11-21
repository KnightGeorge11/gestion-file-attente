"""
Corrections P2 de l'audit du 20/11/2026.
"""

import io
import threading

from django.db import IntegrityError, connection
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from centres.models import EcranAffichage, Service
from circuit import services
from circuit.impression import _texte
from circuit.models import EntreeFile, Ticket
from circuit.services import ErreurCircuit

from .base import Donnees
from .test_concurrence import executer_en_parallele
from .test_vues import BaseVues


class MoteurConcurrenceP2Tests(TransactionTestCase):
    def setUp(self):
        self.d = Donnees().construire()

    def test_meme_agent_appelle_dans_deux_files_en_meme_temps(self):
        """Avant : contrainte en base violée, erreur 500. Maintenant : un appel, un message clair."""
        polyvalent = self.d.agent("poly", "Infirmerie", ["ACC", "INF"])
        m = self.d.ticket("M")
        self.d.accueillir(m, self.d.agent("acc", "Accueil", ["ACC"]))   # M001 attend à l'infirmerie
        self.d.ticket("D")                                              # D001 attend à l'accueil
        files = [self.d.file("ACC"), self.d.file("INF")]
        for _ in range(3):
            resultats, erreurs = executer_en_parallele(lambda i: services.appeler(files[i], polyvalent), 2)
            self.assertEqual(len(resultats), 1)
            self.assertEqual(len(erreurs), 1)
            self.assertIsInstance(erreurs[0], ErreurCircuit)
            self.assertNotIsInstance(erreurs[0], IntegrityError)
            self.assertEqual(EntreeFile.objects.filter(agent=polyvalent, statut="APPELE").count(), 1)
            # Remise en attente pour recommencer.
            EntreeFile.objects.filter(agent=polyvalent).update(statut="EN_ATTENTE", agent=None)

    def test_annulation_et_appel_simultanes_sans_interblocage(self):
        accueil = self.d.agent("acc", "Accueil", ["ACC"])
        for _ in range(15):
            ticket = self.d.ticket("M")
            actions = [
                lambda: services.appeler(self.d.file("ACC"), accueil),
                lambda: services.annuler_ticket(ticket, accueil, "Doublon"),
            ]
            _, erreurs = executer_en_parallele(lambda i: actions[i](), 2)
            # Seules des erreurs métier (ticket déjà fermé, aucun patient…) : jamais d'interblocage.
            for erreur in erreurs:
                self.assertIsInstance(erreur, ErreurCircuit, repr(erreur))
            Ticket.objects.filter(statut__in=Ticket.STATUTS_OUVERTS).update(statut="ANNULE")
            EntreeFile.objects.filter(statut__in=["EN_ATTENTE", "APPELE", "EN_COURS"]).update(statut="ANNULE", agent=None)


class StructureP2Tests(BaseVues):
    def test_un_seul_accueil_et_une_seule_pharmacie_par_centre(self):
        from django.core.exceptions import ValidationError

        second = Service(centre=self.d.centre, code="ACC2", nom="Accueil 2", est_accueil=True)
        with self.assertRaisesMessage(ValidationError, "déjà un service d'accueil"):
            second.full_clean()
        with self.assertRaises(IntegrityError):
            Service.objects.create(centre=self.d.centre, code="PHA2", nom="Pharmacie 2", est_pharmacie=True)

    def test_page_des_services_nombre_de_requetes_constant(self):
        self.connecter(self.accueil, self.d.centre)
        self.client.get(reverse("circuit:guichets"))

        def requetes():
            with CaptureQueriesContext(connection) as q:
                self.client.get(reverse("circuit:guichets"))
            return len(q)

        avant = requetes()
        # Pas de ticket dentiste : son quota dépend du jour et de l'heure réels (fermé le samedi après-midi).
        for prefixe in ("M", "E", "I", "M"):
            self.d.accueillir(self.d.ticket(prefixe), self.accueil)
        self.assertEqual(requetes(), avant)


class ExportsP2Tests(BaseVues):
    def test_excel_sans_formule_et_pdf_avec_caracteres_speciaux(self):
        from openpyxl import load_workbook

        directeur = self.d.agent("dir", "Direction")
        piegeux = self.d.agent("piege", "Infirmerie", ["INF"])
        piegeux.first_name = '=HYPERLINK("http://exemple.invalid","x")'
        piegeux.save()
        ticket = self.d.ticket("I")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", piegeux)
        centre = self.d.centre
        centre.nom = "Centre A & B <Tamatave>"
        centre.save()
        self.connecter(directeur, centre)

        classeur = load_workbook(io.BytesIO(self.client.get(reverse("statistiques:export_excel")).content))
        valeurs = [c.value for f in classeur.worksheets for ligne in f.iter_rows() for c in ligne if c.value]
        self.assertTrue(any(str(v).startswith("'=HYPERLINK") for v in valeurs))
        self.assertFalse(any(str(v).startswith("=") for v in valeurs))

        pdf = self.client.get(reverse("statistiques:export_pdf"))
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF"))


class InterfaceP2Tests(BaseVues):
    def test_navigation_boostee_sans_messages_en_double(self):
        self.connecter(self.accueil, self.d.centre)
        url = reverse("circuit:guichet", args=[self.d.svc["ACC"].pk])
        page = self.client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_BOOSTED="true").content.decode()
        self.assertEqual(page.count('id="messages-page"'), 1)

    def test_panneau_patient_en_cours_actualise_sans_consommer_les_messages(self):
        self.d.ticket("M")
        self.connecter(self.accueil, self.d.centre)
        acc = self.d.svc["ACC"].pk
        page = self.client.get(reverse("circuit:guichet", args=[acc]))
        self.assertContains(page, reverse("circuit:guichet_zone", args=[acc]))
        services.appeler(self.d.file("ACC"), self.accueil)
        panneau = self.client.get(reverse("circuit:guichet_zone", args=[acc]), HTTP_HX_REQUEST="true")
        self.assertContains(panneau, "M001")
        self.assertNotContains(panneau, 'id="messages-page"')

    def test_libelles_associes_aux_champs(self):
        self.connecter(self.accueil, self.d.centre)
        ticket = self.d.ticket("M")
        page = self.client.get(reverse("circuit:ticket", args=[ticket.pk]))
        self.assertContains(page, 'for="motif-annuler"')
        self.assertContains(page, 'id="motif-annuler"')

    def test_ecran_signale_les_appels_en_cours_au_dela_de_la_limite(self):
        poste = EcranAffichage(centre=self.d.centre, code="TV1", libelle="Salle", nb_appels_affiches=2)
        cle = poste.generer_cle()
        poste.save()
        agents = [self.d.agent(f"acc{i}", "Accueil", ["ACC"]) for i in range(4)]
        file = self.d.file("ACC")
        file.nb_appels_simultanes = 4
        file.save()
        for agent in agents:
            self.d.ticket("M")
            services.appeler(file, agent)
        self.client.post(reverse("circuit:ecran"), {"centre": "TAM", "code": "TV1", "cle": cle})
        self.assertContains(self.client.get(reverse("circuit:ecran_appels")), "+ 2 autres appels en cours")

    def test_ecran_n_ecrit_en_base_qu_une_fois_par_minute(self):
        poste = EcranAffichage(centre=self.d.centre, code="TV1", libelle="Salle")
        cle = poste.generer_cle()
        poste.save()
        self.client.post(reverse("circuit:ecran"), {"centre": "TAM", "code": "TV1", "cle": cle})
        self.client.get(reverse("circuit:ecran_appels"))
        premiere = EcranAffichage.objects.get(pk=poste.pk).derniere_activite
        self.client.get(reverse("circuit:ecran_appels"))
        self.assertEqual(EcranAffichage.objects.get(pk=poste.pk).derniere_activite, premiere)


class ImpressionP2Tests(TestCase):
    def test_ponctuation_typographique_translitteree(self):
        self.assertEqual(_texte("L’accueil — œuvre…", "ascii"), b"L'accueil - oeuvre...")
        self.assertNotIn(b"?", _texte("L’accueil — œuvre…", "cp850"))
        self.assertEqual(_texte("é", "cp858"), "é".encode("cp858"))   # accents natifs conservés
