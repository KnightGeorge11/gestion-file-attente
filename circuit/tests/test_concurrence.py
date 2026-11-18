import threading

from django.db import connection
from django.test import TransactionTestCase

from circuit import services
from circuit.models import EntreeFile, Ticket
from circuit.services import ErreurCircuit

from .base import Donnees


def executer_en_parallele(fonction, nombre):
    """Lance `nombre` appels simultanés, chacun avec sa connexion PostgreSQL."""
    depart = threading.Barrier(nombre)
    resultats, erreurs = [], []

    def tache(i):
        try:
            depart.wait()
            resultats.append(fonction(i))
        except Exception as erreur:  # noqa: BLE001 - collecte pour vérification
            erreurs.append(erreur)
        finally:
            connection.close()

    fils = [threading.Thread(target=tache, args=(i,)) for i in range(nombre)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    return resultats, erreurs


class ConcurrenceTests(TransactionTestCase):
    def setUp(self):
        self.d = Donnees().construire()

    def test_numerotation_sans_doublon_avec_creations_simultanees(self):
        parcours = self.d.parcours["M"]
        resultats, erreurs = executer_en_parallele(
            lambda i: services.emettre_ticket(self.d.centre, Ticket.Mode.INTERNE, parcours=parcours).numero,
            12,
        )
        self.assertEqual(erreurs, [])
        self.assertEqual(sorted(resultats), [f"M{n:03d}" for n in range(1, 13)])

    def test_deux_appels_simultanes_sur_le_meme_patient(self):
        self.d.ticket("M")
        file = self.d.file("ACC")
        file.nb_appels_simultanes = 2
        file.save()
        agents = [self.d.agent(f"acc{i}", "Accueil", ["ACC"]) for i in range(2)]
        resultats, erreurs = executer_en_parallele(
            lambda i: services.appeler(file, agents[i]), 2
        )
        self.assertEqual(len(resultats), 1)
        self.assertEqual(len(erreurs), 1)
        self.assertIsInstance(erreurs[0], ErreurCircuit)
        self.assertEqual(EntreeFile.objects.filter(statut="APPELE").count(), 1)

    def test_file_commune_medecins_appels_simultanes_patients_distincts(self):
        accueil = self.d.agent("acc", "Accueil", ["ACC"])
        inf = self.d.agent("inf", "Infirmerie", ["INF"])
        for nom in ("A", "B"):
            ticket = self.d.ticket("M")
            self.d.accueillir(ticket, accueil, patient=self.d.patient(nom))
            self.d.traiter(ticket, "INF", inf)
        medecins = [self.d.agent(f"med{i}", "Médecin", ["MED"]) for i in range(2)]
        file = self.d.file("MED")
        resultats, erreurs = executer_en_parallele(lambda i: services.appeler(file, medecins[i]), 2)
        self.assertEqual(erreurs, [])
        self.assertEqual(len({appel.ticket_id for appel in resultats}), 2)
