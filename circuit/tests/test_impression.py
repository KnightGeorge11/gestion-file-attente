"""
Impression réseau des tickets (option A) : une vraie connexion TCP est
ouverte vers une imprimante simulée lancée dans le test.
"""

import socket
import socketserver
import threading
import time

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from centres.models import Borne, ImprimanteTicket
from circuit.impression import contenu_ticket, etat_imprimantes, imprimer_test
from circuit.management.commands.imprimante_simulee import texte_lisible
from circuit.models import Evenement, Ticket

from .base import Donnees


class ImprimanteFactice:
    """Serveur TCP local qui enregistre les octets reçus (comme une imprimante sur le port 9100)."""

    def __init__(self):
        recus = self.recus = []

        class Gestionnaire(socketserver.BaseRequestHandler):
            def handle(self):
                morceaux = []
                while True:
                    morceau = self.request.recv(4096)
                    if not morceau:
                        break
                    morceaux.append(morceau)
                recus.append(b"".join(morceaux))

        self.serveur = socketserver.TCPServer(("127.0.0.1", 0), Gestionnaire)
        self.port = self.serveur.server_address[1]
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

    def attendre(self, nombre, delai=3.0):
        """Attend que `nombre` tickets aient été reçus (le serveur tourne dans un autre fil)."""
        fin = time.monotonic() + delai
        while len(self.recus) < nombre and time.monotonic() < fin:
            time.sleep(0.02)
        return self.recus

    def arreter(self):
        self.serveur.shutdown()
        self.serveur.server_close()


def port_ferme():
    """Un port local libre sur lequel personne n'écoute (imprimante éteinte)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0)
class ImpressionReseauTests(TestCase):
    def setUp(self):
        self.d = Donnees().construire()
        self.imprimante_reelle = ImprimanteFactice()
        self.addCleanup(self.imprimante_reelle.arreter)
        self.imprimante = ImprimanteTicket.objects.create(
            centre=self.d.centre, libelle="Thermique entrée", adresse_ip="127.0.0.1",
            port=self.imprimante_reelle.port,
        )
        self.borne = Borne(centre=self.d.centre, code="TAB", libelle="Tablette entrée", imprimante=self.imprimante)
        self.cle = self.borne.generer_cle()
        self.borne.save()
        self.client.post(reverse("circuit:borne"), {"centre": "TAM", "code": "TAB", "cle": self.cle})

    def _prendre_ticket(self, prefixe="E"):
        return self.client.post(
            reverse("circuit:borne_emettre"),
            {"mode": "EXTERNE", "parcours": self.d.parcours[prefixe].pk},
            follow=True,
        )

    def test_le_serveur_imprime_directement_sans_boite_de_dialogue(self):
        reponse = self._prendre_ticket()
        self.assertEqual(len(self.imprimante_reelle.attendre(1)), 1)
        texte = texte_lisible(self.imprimante_reelle.recus[0])
        for attendu in ("Centre TAM", "E001", "Échographie", "Externe", "Présentez-vous à l'accueil",
                        "0 personne(s) avant vous"):
            self.assertIn(attendu, texte)
        self.assertTrue(self.imprimante_reelle.recus[0].endswith(b"\x1dVB\x00"))  # coupe du papier
        self.assertContains(reponse, "Votre ticket s'imprime")
        self.assertNotContains(reponse, "window.print()")
        ticket = Ticket.objects.get()
        self.assertTrue(ticket.evenements.filter(type=Evenement.Type.IMPRESSION).exists())

    def test_imprimante_injoignable_le_ticket_reste_valable(self):
        self.imprimante.port = port_ferme()
        self.imprimante.save()
        reponse = self._prendre_ticket()
        ticket = Ticket.objects.get()
        self.assertTrue(ticket.ouvert)
        self.assertContains(reponse, "Notez votre numéro")
        self.assertContains(reponse, "E001")
        self.assertContains(reponse, "Réessayer l'impression")
        echec = ticket.evenements.get(type=Evenement.Type.ECHEC_IMPRESSION)
        self.assertIn("injoignable", echec.details["erreur"])
        # L'accueil voit l'alerte...
        accueil = self.d.agent("acc_imp", "Accueil", ["ACC"])
        self.client.force_login(accueil)
        session = self.client.session
        session["centre_actif_id"] = self.d.centre.pk
        session.save()
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk]))
        self.assertContains(page, "Imprimante de la borne « Tablette entrée » injoignable")
        # ... et elle disparaît dès qu'une impression réussit (nouvelle tentative).
        self.imprimante.port = self.imprimante_reelle.port
        self.imprimante.save()
        borne_client = self.client_class()
        borne_client.post(reverse("circuit:borne"), {"centre": "TAM", "code": "TAB", "cle": self.cle})
        borne_client.post(reverse("circuit:borne_reimprimer", args=[ticket.pk]))
        self.assertEqual(len(self.imprimante_reelle.attendre(1)), 1)
        self.assertEqual(etat_imprimantes(self.d.centre), [])
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk]))
        self.assertNotContains(page, "injoignable")

    def test_pas_de_reimpression_d_un_ticket_deja_imprime(self):
        self._prendre_ticket()
        self.imprimante_reelle.attendre(1)
        ticket = Ticket.objects.get()
        self.client.post(reverse("circuit:borne_reimprimer", args=[ticket.pk]))
        self.assertEqual(len(self.imprimante_reelle.attendre(2, delai=0.5)), 1)

    def test_sans_imprimante_reseau_impression_par_le_navigateur(self):
        self.borne.imprimante = None
        self.borne.save()
        reponse = self._prendre_ticket()
        self.assertContains(reponse, "window.print()")
        self.assertEqual(self.imprimante_reelle.attendre(1, delai=0.5), [])

    def test_accents_et_pages_de_codes(self):
        self._prendre_ticket()
        ticket = Ticket.objects.get()
        pc858 = contenu_ticket(ticket, self.imprimante)
        self.assertIn(b"\x1bt\x13", pc858)                       # table PC858 sélectionnée
        self.assertIn("Échographie".encode("cp858"), pc858)
        self.imprimante.page_codes = "ascii"
        sans_accents = contenu_ticket(ticket, self.imprimante)
        self.assertIn(b"Echographie", sans_accents)
        self.assertNotIn(b"\x1bt", sans_accents)

    def test_ticket_autre_imprime_le_motif(self):
        embauche = self.d.centre.circuit_motifdemande.get(libelle="Visite d'embauche")
        self.client.post(reverse("circuit:borne_emettre"), {"mode": "AUTRE", "motif": embauche.pk})
        self.assertIn("Visite d'embauche", texte_lisible(self.imprimante_reelle.attendre(1)[0]))

    def test_impression_de_test_depuis_l_administration(self):
        imprimer_test(self.imprimante)
        self.assertIn("TEST IMPRIMANTE", texte_lisible(self.imprimante_reelle.attendre(1)[0]))
        admin = self.d.agent("resp_admin", "Responsable")
        admin.is_staff = True
        admin.save()
        admin.user_permissions.add(*Permission.objects.filter(codename__in=["view_imprimanteticket",
                                                                            "change_imprimanteticket"]))
        self.client.force_login(admin)
        self.client.post(reverse("admin:centres_imprimanteticket_changelist"),
                         {"action": "imprimer_test", "_selected_action": [self.imprimante.pk]})
        self.assertEqual(len(self.imprimante_reelle.attendre(2)), 2)
