"""
Vérification des chronométrages avec une horloge contrôlée : chaque
action a lieu à une heure connue et les durées attendues sont calculées
à la main.
"""

import datetime
from unittest import mock

from django.test import TestCase

from circuit import services
from circuit.models import Passage, Ticket
from statistiques.calculs import calculer

from .base import LUNDI_MATIN, Donnees

T0 = LUNDI_MATIN  # lundi 09:00 heure du centre


class Horloge:
    """Fixe timezone.now() à T0 + n minutes."""

    def __init__(self):
        self.patch = mock.patch("django.utils.timezone.now", return_value=T0)

    def __enter__(self):
        self.mock = self.patch.__enter__()
        return self

    def __exit__(self, *args):
        self.patch.__exit__(*args)

    def a(self, minutes):
        self.mock.return_value = T0 + datetime.timedelta(minutes=minutes)


def minutes(n):
    return datetime.timedelta(minutes=n)


class ChronometrageTests(TestCase):
    def setUp(self):
        self.d = Donnees().construire()
        self.accueil = self.d.agent("acc", "Accueil", ["ACC"])
        self.infirmier = self.d.agent("inf", "Infirmerie", ["INF"])
        self.medecin = self.d.agent("med", "Médecin", ["MED"])
        self.pharma = self.d.agent("pha", "Pharmacie", ["PHA"])

    def _passage(self, ticket, code):
        return ticket.passages.get(service__code=code)

    def test_parcours_medecin_avec_pharmacie_durees_exactes(self):
        with Horloge() as h:
            ticket = self.d.ticket("M")                       # 0 : arrivée accueil
            h.a(5); services.appeler(self.d.file("ACC"), self.accueil)
            h.a(6); services.prendre_en_charge(self.d.entree(ticket), self.accueil)
            h.a(10); services.terminer(self.d.entree(ticket), self.accueil)   # arrivée infirmerie
            h.a(25); services.appeler(self.d.file("INF"), self.infirmier)
            h.a(26); services.prendre_en_charge(self.d.entree(ticket), self.infirmier)
            h.a(36); services.terminer(self.d.entree(ticket), self.infirmier)  # arrivée médecins
            h.a(60); services.appeler(self.d.file("MED"), self.medecin)
            h.a(62); services.prendre_en_charge(self.d.entree(ticket), self.medecin)
            h.a(80); services.terminer(self.d.entree(ticket), self.medecin, transferer_pharmacie=True)
            h.a(90); services.prendre_en_charge(self.d.entree(ticket), self.pharma, oral=True)
            h.a(95); services.terminer(self.d.entree(ticket), self.pharma)

        ticket.refresh_from_db()
        attendu = {  # service : (attente, prise en charge)
            "ACC": (minutes(6), minutes(4)),
            "INF": (minutes(16), minutes(10)),
            "MED": (minutes(26), minutes(18)),
            "PHA": (minutes(10), minutes(5)),
        }
        for code, (attente, pec) in attendu.items():
            passage = self._passage(ticket, code)
            self.assertEqual(passage.duree_attente, attente, code)
            self.assertEqual(passage.duree_prise_en_charge, pec, code)
        self.assertEqual(ticket.duree_totale, minutes(95))
        # Pas de trou ni de chevauchement : chaque arrivée = fin du passage précédent.
        passages = list(ticket.passages.order_by("date_arrivee"))
        for precedent, suivant in zip(passages, passages[1:]):
            self.assertEqual(suivant.date_arrivee, precedent.date_fin)
        # Le transfert est horodaté à la fin de la consultation.
        self.assertEqual(ticket.transferts.get().date, T0 + minutes(80))

    def test_absence_rappels_reintegration_sans_double_comptage(self):
        with Horloge() as h:
            ticket = self.d.ticket("I")
            h.a(1); services.appeler(self.d.file("ACC"), self.accueil)
            h.a(2); services.rappeler(self.d.entree(ticket), self.accueil)
            h.a(3); services.rappeler(self.d.entree(ticket), self.accueil)
            premiere = self.d.entree(ticket)
            h.a(4); services.marquer_absent(premiere, self.accueil)
            h.a(20); nouvelle = services.reintegrer(premiere.passage, self.accueil)
            h.a(30); services.appeler(self.d.file("ACC"), self.accueil)
            h.a(31); services.prendre_en_charge(self.d.entree(ticket), self.accueil)
            h.a(35); services.terminer(self.d.entree(ticket), self.accueil)

        passage = self._passage(ticket, "ACC")
        # Un seul passage ; attente = arrivée → prise en charge (absence incluse).
        self.assertEqual(Passage.objects.filter(ticket=ticket, service__code="ACC").count(), 1)
        self.assertEqual(passage.duree_attente, minutes(31))
        self.assertEqual(passage.duree_prise_en_charge, minutes(4))
        # L'historique de la première entrée est conservé.
        premiere.refresh_from_db()
        self.assertEqual((premiere.nb_appels, premiere.date_absence), (3, T0 + minutes(4)))
        self.assertEqual(premiere.appels.count(), 3)
        nouvelle.refresh_from_db()
        self.assertEqual((nouvelle.date_entree, nouvelle.date_debut), (T0 + minutes(20), T0 + minutes(31)))

        # Statistiques : un seul passage compté, attente non pondérée par les entrées.
        stats = calculer([self.d.centre], T0.date(), T0.date())
        ligne = next(l for l in stats["par_service"] if l["service"] == "Accueil")
        self.assertEqual(ligne["passages"], 1)
        self.assertEqual(ligne["absences"], 1)
        self.assertEqual(ligne["attente_moy"], 31.0)
        self.assertEqual(ligne["prise_en_charge_moy"], 4.0)

    def test_statistiques_moyennes_par_service_et_totales(self):
        with Horloge() as h:
            t1 = self.d.ticket("I")
            h.a(10); services.appeler(self.d.file("ACC"), self.accueil)
            services.prendre_en_charge(self.d.entree(t1), self.accueil)
            h.a(12); services.terminer(self.d.entree(t1), self.accueil)
            t2 = self.d.ticket("I")
            h.a(32); services.appeler(self.d.file("ACC"), self.accueil)
            services.prendre_en_charge(self.d.entree(t2), self.accueil)
            h.a(36); services.terminer(self.d.entree(t2), self.accueil)
            h.a(40); services.appeler(self.d.file("INF"), self.infirmier)
            services.prendre_en_charge(self.d.entree(t1), self.infirmier)
            h.a(50); services.terminer(self.d.entree(t1), self.infirmier)

        stats = calculer([self.d.centre], T0.date(), T0.date())
        accueil = next(l for l in stats["par_service"] if l["service"] == "Accueil")
        # Attentes 10 et 20 min ; prises en charge 2 et 4 min.
        self.assertEqual((accueil["passages"], accueil["attente_moy"], accueil["attente_max"],
                          accueil["prise_en_charge_moy"]), (2, 15.0, 20.0, 3.0))
        # Durée totale moyenne : seul t1 est terminé (50 min).
        self.assertEqual(stats["duree_totale_moy"], 50.0)
        self.assertEqual(stats["par_statut"]["Terminé"], 1)
        self.assertEqual(stats["total_tickets"], 2)

    def test_ticket_annule_n_entre_pas_dans_les_durees_terminees(self):
        with Horloge() as h:
            ticket = self.d.ticket("M")
            h.a(7); services.annuler_ticket(ticket, self.accueil, "Doublon")
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.ANNULE)
        passage = ticket.passages.get()
        self.assertIsNone(passage.duree_attente)
        self.assertIsNone(passage.duree_prise_en_charge)
        self.assertIsNone(calculer([self.d.centre], T0.date(), T0.date())["duree_totale_moy"])
