"""
Médecin en chef (décision du 23/11/2026, remplace celle du 22/11/2026) : ce
n'est plus un service ni un parcours. Comme la direction, l'administrateur du
centre ou les RH, il reçoit les demandes « Autre » que l'accueil lui
transmet (motif « Rencontrer le médecin en chef ») : il n'appelle aucun
patient, appelle le visiteur depuis ses Notifications et répond. Le patient ne
peut pas le choisir directement à la borne, et les médecins n'ont plus de
bouton pour lui envoyer un patient.
"""

from django.urls import reverse

from centres.models import Borne
from circuit import services
from circuit.models import Appel, MotifDemande, Ticket
from circuit.services import ErreurCircuit

from .base import activer_borne
from .test_vues import BaseVues


class BaseMedecinChef(BaseVues):
    def setUp(self):
        super().setUp()
        self.chef = self.d.agent("chef1", "Médecin en chef")
        self.motif_chef = MotifDemande.objects.get(centre=self.d.centre, libelle="Rencontrer le médecin en chef")


class PasDeMedecinEnChefALaBorneTests(BaseMedecinChef):
    def test_ni_service_ni_parcours(self):
        self.assertNotIn("MEC", self.d.svc)
        self.assertNotIn("C", self.d.parcours)

    def test_borne_de_l_accueil_sans_medecin_en_chef_mais_motif_autre(self):
        self.d.centre.accueil_delivre_tickets = True
        self.d.centre.save(update_fields=["accueil_delivre_tickets"])
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Accueil")
        activer_borne(self.client, borne)
        page = self.client.get(reverse("circuit:borne"))
        self.assertContains(page, "Rencontrer le médecin en chef")       # motif du mode « Autre »
        self.assertFalse(borne.parcours_proposes().filter(libelle_borne__icontains="chef").exists())


class DemandeAuMedecinEnChefTests(BaseMedecinChef):
    def _demande_transmise(self):
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.motif_chef)
        notification = services.transmettre_demande(ticket.demande, self.accueil, destinataire=self.chef,
                                                    message="Patient de M. Rakoto")
        return ticket, notification

    def test_l_accueil_oriente_vers_le_chef_qui_appelle_le_visiteur_et_repond(self):
        ticket, notification = self._demande_transmise()
        self.connecter(self.chef, self.d.centre, salle_indiquee=False)
        self.assertEqual(self.client.get(reverse("circuit:badge_notifications")).content,
                         b'<span class="pastille">1</span>')
        page = self.client.get(reverse("circuit:notifications"))
        self.assertContains(page, ticket.numero)
        self.assertContains(page, "Rencontrer le médecin en chef")
        self.client.post(reverse("circuit:notification_appeler", args=[notification.pk]), {"poste": ""})
        appel = Appel.objects.get(ticket=ticket, agent=self.chef)
        self.assertEqual(appel.nom_service, "Médecin en chef")           # affiché sur l'écran
        services.repondre_notification(notification, self.chef, "TRAITEE", "Reçu")
        ticket.demande.refresh_from_db()
        self.assertEqual(ticket.demande.statut, "CLOTUREE")

    def test_le_chef_n_appelle_ni_ne_voit_aucun_patient(self):
        patient = self.d.ticket("M")
        with self.assertRaises(ErreurCircuit):
            services.appeler(self.d.file("ACC"), self.chef)
        self.connecter(self.chef, self.d.centre, salle_indiquee=False)
        self.assertEqual(self.client.get(reverse("circuit:ticket", args=[patient.pk])).status_code, 404)
        guichets = self.client.get(reverse("circuit:guichets"))
        self.assertNotEqual(guichets.status_code, 500)
        self.assertFalse(guichets.get("Location", "").startswith(reverse("circuit:ma_salle")))


class EnvoiParLeMedecinTests(BaseMedecinChef):
    """
    Décision du 23/11/2026 (suite) : en fin de parcours, le médecin peut
    « Envoyer vers médecin en chef » (motif obligatoire). Le médecin en chef
    reçoit le patient dans ses Notifications, l'appelle à l'écran et répond.
    """

    def _au_medecin(self):
        infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", infirmier)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("MED"), self.medecin, entree=entree)
        services.prendre_en_charge(entree, self.medecin)
        return ticket, entree, infirmier

    def test_bouton_coupe_en_deux_au_guichet_du_medecin(self):
        self._au_medecin()
        self.connecter(self.medecin, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["MED"].pk]))
        self.assertContains(page, "Terminer et transférer à la pharmacie")
        self.assertContains(page, "Terminer sans pharmacie (fin du parcours)")
        self.assertContains(page, "Envoyer vers médecin en chef")

    def test_envoi_reel_vers_le_medecin_en_chef(self):
        ticket, entree, _ = self._au_medecin()
        self.connecter(self.medecin, self.d.centre)
        url = reverse("circuit:guichet_action", args=[self.d.svc["MED"].pk, "terminer"])
        self.client.post(url, {"entree": entree.pk, "file": entree.file_id, "destination": "medecin_chef", "motif": ""})
        ticket.refresh_from_db()
        self.assertTrue(ticket.ouvert)                                   # motif obligatoire : refusé
        self.client.post(url, {"entree": entree.pk, "file": entree.file_id, "destination": "medecin_chef",
                               "motif": "Avis sur douleur thoracique"})
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        self.assertFalse(ticket.passages.filter(service__code="PHA").exists())
        notification = ticket.demande.notifications.get()
        self.assertEqual(notification.destinataire, self.chef)
        self.assertEqual(notification.transmise_par, self.medecin)
        # Le médecin en chef le voit dans ses Notifications, l'appelle à l'écran et répond.
        self.connecter(self.chef, self.d.centre, salle_indiquee=False)
        page = self.client.get(reverse("circuit:notifications"))
        self.assertContains(page, ticket.numero)
        self.assertContains(page, "Avis sur douleur thoracique")
        self.client.post(reverse("circuit:notification_appeler", args=[notification.pk]), {"poste": ""})
        self.assertEqual(Appel.objects.get(ticket=ticket, agent=self.chef).nom_service, "Médecin en chef")
        services.repondre_notification(notification, self.chef, "TRAITEE")
        # L'accueil n'a pas à s'en occuper : pas dans sa page Demandes.
        self.connecter(self.accueil, self.d.centre)
        demandes = self.client.get(reverse("circuit:demandes")).context["demandes"]
        self.assertNotIn(ticket.demande, list(demandes))

    def test_pas_depuis_l_infirmerie_ni_sans_medecin_en_chef(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("INF"), infirmier, entree=entree)
        services.prendre_en_charge(entree, infirmier)
        self.assertFalse(services.peut_envoyer_medecin_chef(entree.passage))
        with self.assertRaises(ErreurCircuit):
            services.terminer(entree, infirmier, envoyer_medecin_chef=True, motif="x")
        self.chef.is_active = False
        self.chef.save(update_fields=["is_active"])
        self.assertIsNone(services.medecin_chef(self.d.centre))
