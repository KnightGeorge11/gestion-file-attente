"""
Médecin en chef (décision du 22/11/2026) : seuls les médecins y envoient un
patient ; l'accueil y accède par la borne qu'il tient. Jamais proposé sur une
borne en libre-service. Ensuite : fin du ticket ou pharmacie.
"""

from django.urls import reverse

from centres.models import Borne
from circuit import services
from circuit.models import Ticket, Transfert
from circuit.services import ErreurCircuit

from .base import activer_borne
from .test_vues import BaseVues


class MedecinChefBorneTests(BaseVues):
    def test_jamais_sur_une_borne_en_libre_service(self):
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Entrée")
        self.assertNotIn(self.d.parcours["C"], borne.parcours_proposes())
        activer_borne(self.client, borne)
        self.assertNotContains(self.client.get(reverse("circuit:borne")), "Médecin en chef")
        with self.assertRaises(ErreurCircuit):
            self.d.ticket("C")
        self.assertFalse(Ticket.objects.filter(prefixe="C").exists())

    def test_borne_tenue_par_l_accueil_envoie_directement_au_medecin_en_chef(self):
        self.d.centre.accueil_delivre_tickets = True
        self.d.centre.save(update_fields=["accueil_delivre_tickets"])
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Accueil")
        activer_borne(self.client, borne)
        self.assertContains(self.client.get(reverse("circuit:borne")), "Médecin en chef")

        ticket = self.d.ticket("C")
        self.assertEqual(ticket.numero, "C001")
        self.assertEqual(self.d.entree(ticket).file_id, self.d.file("MEC").pk)


class MedecinChefTransfertTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        self.chef = self.d.agent("chef1", "Médecin", ["MEC"])

    def _jusqu_au_medecin(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", self.infirmier)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("MED"), self.medecin, entree=entree)
        services.prendre_en_charge(entree, self.medecin)
        return ticket, entree

    def test_le_medecin_envoie_puis_le_chef_transfere_a_la_pharmacie(self):
        ticket, entree = self._jusqu_au_medecin()
        passage = services.terminer(entree, self.medecin, transferer_medecin_chef=True, motif="Avis")
        self.assertEqual(passage.service.code, "MEC")
        self.assertTrue(Transfert.objects.filter(ticket=ticket, service_destination__code="MEC").exists())

        self.d.traiter(ticket, "MEC", self.chef, transferer=True)
        self.d.traiter(ticket, "PHA", self.pharma)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        self.assertEqual(list(ticket.passages.values_list("service__code", flat=True)),
                         ["ACC", "INF", "MED", "MEC", "PHA"])

    def test_le_chef_termine_le_ticket(self):
        ticket, entree = self._jusqu_au_medecin()
        services.terminer(entree, self.medecin, transferer_medecin_chef=True)
        self.assertIsNone(self.d.traiter(ticket, "MEC", self.chef))
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)

    def test_l_infirmerie_ne_peut_pas_envoyer_au_medecin_en_chef(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("INF"), self.infirmier, entree=entree)
        services.prendre_en_charge(entree, self.infirmier)
        self.assertFalse(services.peut_transferer_medecin_chef(entree.passage))
        with self.assertRaises(ErreurCircuit):
            services.terminer(entree, self.infirmier, transferer_medecin_chef=True, motif="x")

    def test_pas_deux_destinations_a_la_fois(self):
        _, entree = self._jusqu_au_medecin()
        with self.assertRaises(ErreurCircuit):
            services.terminer(entree, self.medecin, transferer_pharmacie=True, transferer_medecin_chef=True)

    def test_bouton_au_guichet_du_medecin(self):
        ticket, entree = self._jusqu_au_medecin()
        self.connecter(self.medecin, self.d.centre)
        med = self.d.svc["MED"].pk
        page = self.client.get(reverse("circuit:guichet", args=[med]))
        self.assertContains(page, "Envoyer au médecin en chef")
        self.client.post(reverse("circuit:guichet_action", args=[med, "terminer"]),
                         {"entree": entree.pk, "medecin_chef": "1"})
        self.assertEqual(self.d.entree(ticket).file_id, self.d.file("MEC").pk)

    def test_pas_de_bouton_a_l_infirmerie(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("INF"), self.infirmier, entree=entree)
        services.prendre_en_charge(entree, self.infirmier)
        self.connecter(self.infirmier, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["INF"].pk]))
        self.assertNotContains(page, "médecin en chef")
