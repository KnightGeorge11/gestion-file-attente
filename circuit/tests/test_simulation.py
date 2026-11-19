from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from centres.models import Centre
from circuit.models import Appel, Passage, Ticket, Transfert
from comptes.management.commands.creer_comptes_test import identifiants_test
from comptes.models import Utilisateur
from comptes.permissions import a_droit


def commande(*args, **kwargs):
    call_command(*args, stdout=StringIO(), stderr=StringIO(), **kwargs)


@override_settings(DEBUG=True)
class ComptesTestTests(TestCase):
    def setUp(self):
        self.a = Centre.objects.create(code="AAA", nom="Centre A")
        self.b = Centre.objects.create(code="BBB", nom="Centre B")

    def test_deux_comptes_par_service_mot_de_passe_1234(self):
        commande("creer_comptes_test")
        noms = identifiants_test()
        self.assertEqual(len(noms), 18)   # 9 rôles x 2
        self.assertIn("medecin1", noms)
        self.assertIn("infirmier2", noms)
        self.assertEqual(Utilisateur.objects.filter(username__in=noms).count(), 18)
        for nom in ("medecin1", "infirmier2", "accueil1", "pharmacie2"):
            self.assertTrue(self.client.login(username=nom, password="1234"), nom)
            self.client.logout()
        medecin = Utilisateur.objects.get(username="medecin2")
        for centre in (self.a, self.b):
            med = centre.services.get(code="MED")
            acc = centre.services.get(code="ACC")
            self.assertTrue(a_droit(medecin, "circuit.appeler", centre, med))
            self.assertFalse(a_droit(medecin, "circuit.appeler", centre, acc))
        self.assertEqual(self.a.services.get(code="MED").destinataire_demandes.username, "medecin1")

    def test_relance_sans_doublon_et_centre_choisi(self):
        commande("creer_comptes_test", "--centres", "AAA")
        commande("creer_comptes_test", "--centres", "AAA")
        medecin = Utilisateur.objects.get(username="medecin1")
        self.assertEqual(list(medecin.affectations.values_list("centre__code", flat=True)), ["AAA"])

    @override_settings(DEBUG=False)
    def test_refuse_en_production_sans_force(self):
        with self.assertRaises(CommandError):
            commande("creer_comptes_test")
        self.assertFalse(Utilisateur.objects.filter(username="medecin1").exists())


@override_settings(DEBUG=True)
class SimulationTests(TestCase):
    def setUp(self):
        self.a = Centre.objects.create(code="AAA", nom="Centre A")
        self.b = Centre.objects.create(code="BBB", nom="Centre B")
        commande("creer_comptes_test")

    def test_simulation_avance_les_patients_dans_le_respect_des_regles(self):
        commande("simuler_patients", "AAA", "--tickets", "25", "--etapes", "60",
                 "--graine", "7")
        tickets = Ticket.objects.filter(centre=self.a)
        self.assertEqual(tickets.count(), 25)
        self.assertGreater(tickets.filter(statut="TERMINE").count(), 5)
        self.assertTrue(Appel.objects.filter(centre=self.a).exists())
        # Toute arrivée en pharmacie provient d'un transfert explicite d'un service de soins.
        for passage in Passage.objects.filter(centre=self.a, service__est_pharmacie=True):
            transfert = Transfert.objects.get(passage_destination=passage)
            self.assertFalse(transfert.passage_origine.service.est_accueil)
            self.assertIn(transfert.decide_par.username, identifiants_test())
        # Le centre B n'est pas touché.
        self.assertFalse(Ticket.objects.filter(centre=self.b).exists())
        # Les tickets terminés ont un parcours complet et horodaté.
        for ticket in tickets.filter(statut="TERMINE", mode__in=["INTERNE", "EXTERNE"]):
            self.assertTrue(ticket.passages.first().service.est_accueil)
            self.assertFalse(ticket.passages.filter(date_fin__isnull=True).exists())
            self.assertIsNone(ticket.patient)  # aucune information patient

    def test_sans_comptes_test_message_clair(self):
        Centre.objects.create(code="CCC", nom="Centre C")
        commande("initialiser_parcours", "CCC")
        with self.assertRaises(CommandError):
            commande("simuler_patients", "CCC")

    @override_settings(DEBUG=False)
    def test_refuse_en_production_sans_force(self):
        with self.assertRaises(CommandError):
            commande("simuler_patients", "AAA")


@override_settings(DEBUG=True)
class SimulationNeToucheQueSesTicketsTests(TestCase):
    """La simulation n'agit jamais sur les vrais tickets ni au nom des vrais agents."""

    def setUp(self):
        from circuit.tests.base import Donnees

        self.d = Donnees().construire("AAA")
        commande("creer_comptes_test", "--centres", "AAA")
        self.vrai_accueil = self.d.agent("vrai_accueil", "Accueil", ["ACC"])
        self.vrai_infirmier = self.d.agent("vrai_infirmier", "Infirmerie", ["INF"])

    def test_vrais_tickets_et_vrais_agents_intacts(self):
        from circuit import services
        from circuit.models import Evenement

        # Un vrai ticket tenu par un vrai agent, un autre en tête de la file d'infirmerie.
        file_accueil = self.d.file("ACC")
        file_accueil.nb_appels_simultanes = 3
        file_accueil.save()
        tenu = self.d.ticket("I")
        services.appeler(self.d.file("ACC"), self.vrai_accueil)
        services.prendre_en_charge(self.d.entree(tenu), self.vrai_accueil)
        en_tete = self.d.ticket("I")
        services.appeler(self.d.file("ACC"), self.d.agent("autre_accueil", "Accueil", ["ACC"]))
        entree = self.d.entree(en_tete)
        services.prendre_en_charge(entree, entree.agent)
        services.terminer(entree, entree.agent)  # en_tete attend en infirmerie
        avant = {t.pk: t.evenements.count() for t in (tenu, en_tete)}

        commande("simuler_patients", "AAA", "--tickets", "20", "--etapes", "40", "--graine", "3")

        for ticket in (tenu, en_tete):
            self.assertEqual(ticket.evenements.count(), avant[ticket.pk], ticket.numero)
        self.assertEqual(self.d.entree(tenu).statut, "EN_COURS")
        self.assertEqual(self.d.entree(en_tete).statut, "EN_ATTENTE")
        # Aucune action de la simulation au nom d'un compte qui n'est pas un compte de test.
        simules = Ticket.objects.filter(borne__code="SIMULATION")
        self.assertTrue(simules.exists())
        agents = set(Evenement.objects.filter(ticket__in=simules, agent__isnull=False)
                     .values_list("agent__username", flat=True))
        self.assertTrue(agents)
        self.assertTrue(agents <= set(identifiants_test()), agents - set(identifiants_test()))
