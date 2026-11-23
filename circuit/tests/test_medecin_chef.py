"""
Médecin en chef (décision du 22/11/2026) : seuls les médecins y envoient un
patient ; l'accueil y accède par la borne qu'il tient. Jamais proposé sur une
borne en libre-service. Ensuite : fin du ticket ou pharmacie.
"""

from django.urls import reverse

from centres.models import Borne
from circuit import services
from circuit.models import Appel, Ticket, Transfert
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


class BaseMedecinChef(BaseVues):
    """Données communes (sans test) : un infirmier, le médecin en chef, un patient jusqu'au médecin."""

    def setUp(self):
        super().setUp()
        self.infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        self.chef = self.d.agent("chef1", "Médecin", ["MEC"])

    def _recevoir(self, ticket):
        """Le chef reçoit le patient devant lui : sans appel à l'écran."""
        entree = self.d.entree(ticket)
        self.assertEqual(entree.file_id, self.d.file("MEC").pk)
        return services.prendre_en_charge(entree, self.chef, oral=True)

    def _jusqu_au_medecin(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", self.infirmier)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("MED"), self.medecin, entree=entree)
        services.prendre_en_charge(entree, self.medecin)
        return ticket, entree


class MedecinChefTransfertTests(BaseMedecinChef):
    def test_le_medecin_envoie_puis_le_chef_transfere_a_la_pharmacie(self):
        ticket, entree = self._jusqu_au_medecin()
        passage = services.terminer(entree, self.medecin, transferer_medecin_chef=True, motif="Avis")
        self.assertEqual(passage.service.code, "MEC")
        self.assertTrue(Transfert.objects.filter(ticket=ticket, service_destination__code="MEC").exists())

        self._recevoir(ticket)
        services.terminer(self.d.entree(ticket), self.chef, transferer_pharmacie=True)
        self.d.traiter(ticket, "PHA", self.pharma)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        self.assertEqual(list(ticket.passages.values_list("service__code", flat=True)),
                         ["ACC", "INF", "MED", "MEC", "PHA"])

    def test_le_chef_termine_le_ticket(self):
        ticket, entree = self._jusqu_au_medecin()
        services.terminer(entree, self.medecin, transferer_medecin_chef=True)
        self.assertIsNone(services.terminer(self._recevoir(ticket), self.chef))
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


class MedecinChefSansAppelTests(BaseMedecinChef):
    """Décision du 22/11/2026 : un seul chef, salle fixe jamais annoncée, aucun appel à l'écran."""

    def _envoyer(self):
        ticket, entree = self._jusqu_au_medecin()
        services.terminer(entree, self.medecin, transferer_medecin_chef=True, motif="Avis du chef")
        return ticket

    def test_le_chef_n_appelle_jamais_a_l_ecran(self):
        ticket = self._envoyer()
        with self.assertRaisesMessage(ErreurCircuit, "n'appelle pas à l'écran"):
            services.appeler(self.d.file("MEC"), self.chef)
        self._recevoir(ticket)
        self.assertFalse(Appel.objects.filter(ticket=ticket, service__code="MEC", mode=Appel.Mode.ECRAN).exists())

    def test_patient_envoye_dans_les_notifications_du_chef(self):
        ticket = self._envoyer()
        self.connecter(self.chef, self.d.centre, salle_indiquee=False)
        self.assertEqual(self.client.get(reverse("circuit:badge_notifications")).content,
                         b'<span class="pastille">1</span>')
        page = self.client.get(reverse("circuit:notifications"))
        self.assertContains(page, ticket.numero)
        self.assertContains(page, "Avis du chef")
        self.assertContains(page, "Recevoir le patient")
        entree = self.d.entree(ticket)
        self.client.post(reverse("circuit:guichet_action", args=[self.d.svc["MEC"].pk, "prendre"]),
                         {"entree": entree.pk, "file": entree.file_id, "oral": "1"})
        entree.refresh_from_db()
        self.assertEqual(entree.statut, "EN_COURS")
        self.assertEqual(self.client.get(reverse("circuit:badge_notifications")).content, b"")

    def test_le_chef_n_indique_pas_de_salle(self):
        self.connecter(self.chef, self.d.centre, salle_indiquee=False)
        reponse = self.client.get(reverse("circuit:guichets"))
        self.assertFalse(reponse.get("Location", "").startswith(reverse("circuit:ma_salle")))
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["MEC"].pk]))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "Appeler le suivant")
        self.assertNotContains(page, "Votre salle")

    def test_le_medecin_ne_voit_pas_de_salle_du_chef(self):
        ticket = self._envoyer()
        self._recevoir(ticket)
        appel = Appel.objects.get(ticket=ticket, service__code="MEC")
        self.assertEqual(appel.poste, "")


class FileAutreSepareeTests(BaseVues):
    """Décision du 22/11/2026 : les « Autre » et les patients ne sont jamais dans la même file."""

    def test_ticket_autre_dans_sa_propre_file(self):
        patient = self.d.ticket("M")
        autre = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        file_patient, file_autre = self.d.entree(patient).file, self.d.entree(autre).file
        self.assertEqual(file_patient, self.d.file("ACC"))
        self.assertNotEqual(file_autre, file_patient)
        self.assertEqual(file_autre.service, self.d.svc["ACC"])
        self.assertEqual(file_autre.code, services.CODE_FILE_AUTRE)
        self.assertEqual(services.appeler(file_patient, self.accueil).ticket, patient)
        self.assertFalse(services.file_en_attente(file_patient).exists())

    def test_accueil_qui_delivre_les_tickets_ouvre_la_file_autre(self):
        self.d.centre.accueil_delivre_tickets = True
        self.d.centre.save(update_fields=["accueil_delivre_tickets"])
        autre = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        self.connecter(self.accueil, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk]))
        self.assertContains(page, autre.numero)


class MedecinChefOrdreArriveeTests(BaseMedecinChef):
    """Décision du 23/11/2026 : le médecin en chef reçoit ses patients dans l'ordre d'arrivée."""

    def test_reception_dans_l_ordre_d_arrivee(self):
        premier, entree1 = self._jusqu_au_medecin()
        services.terminer(entree1, self.medecin, transferer_medecin_chef=True)
        medecin2 = self.d.agent("med2", "Médecin", ["MED"])
        second = self.d.ticket("M")
        self.d.accueillir(second, self.accueil)
        self.d.traiter(second, "INF", self.infirmier)
        entree2 = self.d.entree(second)
        services.appeler(self.d.file("MED"), medecin2, entree=entree2)
        services.prendre_en_charge(entree2, medecin2)
        services.terminer(entree2, medecin2, transferer_medecin_chef=True)
        with self.assertRaisesMessage(ErreurCircuit, "Ordre strict"):
            services.prendre_en_charge(self.d.entree(second), self.chef, oral=True)
        self._recevoir(premier)
