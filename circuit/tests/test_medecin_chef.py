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


class PlusDEnvoiParLesMedecinsTests(BaseMedecinChef):
    def test_pas_de_bouton_au_guichet_du_medecin(self):
        infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", infirmier)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("MED"), self.medecin, entree=entree)
        services.prendre_en_charge(entree, self.medecin)
        self.connecter(self.medecin, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["MED"].pk]))
        self.assertNotContains(page, "médecin en chef")
        self.assertContains(page, "Transférer à la pharmacie")
