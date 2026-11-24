"""
Impression réseau des tickets (option A) : une vraie connexion TCP est
ouverte vers une imprimante simulée lancée dans le test.
"""

import base64
import socket
import socketserver
import threading
import time
from datetime import timedelta

from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from centres.models import Borne, ImprimanteTicket
from circuit.impression import contenu_ticket, etat_imprimantes, imprimer_test
from circuit.management.commands.imprimante_simulee import texte_lisible
from circuit.models import Evenement, Ticket

from .base import activer_borne, Donnees


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


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0, IMPRIMANTE_SIMULEE_AUTORISEE=True)
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
        activer_borne(self.client, Borne.objects.get(centre__code="TAM", code="TAB"))

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
        activer_borne(borne_client, Borne.objects.get(centre__code="TAM", code="TAB"))
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

    def test_application_android_imprimante_bluetooth(self):
        """Dernier recours : l'application Android récupère les octets ESC/POS du ticket."""
        self.borne.imprimante = None
        self.borne.save()
        reponse = self._prendre_ticket()
        self.assertContains(reponse, "window.BorneAndroid")
        ticket = Ticket.objects.get()
        url = reverse("circuit:borne_ticket_escpos", args=[ticket.pk])
        donnees = self.client.get(url, {"largeur": "32"}).json()
        self.assertEqual(donnees["numero"], ticket.numero)
        octets = base64.b64decode(donnees["escpos"])
        self.assertIn(ticket.numero.encode(), octets)
        self.assertIn(b"Echographie", octets)                   # sans accents par défaut
        self.assertIn(b"-" * 32 + b"\n", octets)
        self.assertNotIn(b"-" * 33, octets)
        self.assertIn("Échographie".encode("cp858"), base64.b64decode(self.client.get(url, {"page": "cp858"}).json()["escpos"]))
        # Pas pour une autre session (agent ou visiteur sans borne activée).
        self.assertEqual(self.client_class().get(url).status_code, 403)
        # Pas une fois le ticket ancien (pas de réimpression à volonté).
        Ticket.objects.filter(pk=ticket.pk).update(date_emission=ticket.date_emission - timedelta(minutes=5))
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_application_android_alerte_l_accueil_si_le_bluetooth_echoue(self):
        self.borne.imprimante = None
        self.borne.save()
        self._prendre_ticket()
        ticket = Ticket.objects.get()
        url = reverse("circuit:borne_impression_android", args=[ticket.pk])
        self.assertEqual(self.client.post(url, {"ok": "0", "erreur": "Imprimante injoignable"}).status_code, 204)
        echec = ticket.evenements.get(type=Evenement.Type.ECHEC_IMPRESSION)
        self.assertTrue(echec.details["bluetooth"])
        alertes = etat_imprimantes(self.d.centre)
        self.assertEqual(len(alertes), 1)
        self.assertTrue(alertes[0]["bluetooth"])
        accueil = self.d.agent("acc_bt", "Accueil", ["ACC"])
        agent = self.client_class()
        agent.force_login(accueil)
        session = agent.session
        session["centre_actif_id"] = self.d.centre.pk
        session.save()
        page = agent.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk]))
        self.assertContains(page, "le Bluetooth de la tablette")
        # Nouvelle tentative réussie : l'alerte disparaît.
        self.client.post(url, {"ok": "1"})
        self.assertEqual(etat_imprimantes(self.d.centre), [])
        # Seulement la borne qui a émis le ticket, et seulement en POST.
        self.assertEqual(self.client_class().post(url, {"ok": "1"}).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 405)

    def test_application_android_inutile_avec_imprimante_reseau(self):
        self._prendre_ticket()
        self.imprimante_reelle.attendre(1)
        ticket = Ticket.objects.get()
        self.assertEqual(self.client.get(reverse("circuit:borne_ticket_escpos", args=[ticket.pk])).status_code, 403)

    def test_la_borne_ferme_les_tickets_de_la_veille(self):
        from django.core.cache import cache

        cache.clear()
        self._prendre_ticket()
        vieux = Ticket.objects.get()
        Ticket.objects.filter(pk=vieux.pk).update(jour=vieux.jour - timedelta(days=1))
        cache.clear()
        Ticket.objects.filter(pk=vieux.pk).update(date_emission=vieux.date_emission - timedelta(days=1))
        self._prendre_ticket()
        vieux.refresh_from_db()
        self.assertEqual(vieux.statut, Ticket.Statut.CLOTURE)
        self.assertEqual(Ticket.objects.filter(statut__in=Ticket.STATUTS_OUVERTS).count(), 1)

    def test_etat_de_l_imprimante_visible_sur_la_borne(self):
        self.assertContains(self.client.get(reverse("circuit:borne")), "Imprimante prête")
        self.imprimante.port = port_ferme()
        self.imprimante.save()
        self._prendre_ticket()
        self.assertContains(self.client.get(reverse("circuit:borne")), "Imprimante indisponible")
        self.borne.imprimante = None
        self.borne.save()
        # Impression par la tablette : rien de technique n'est montré au patient (24/11/2026).
        self.assertNotContains(self.client.get(reverse("circuit:borne")), "Impression par la tablette")

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
        # Liste revue le 24/11/2026 : titre simple, colonnes lisibles, bouton de test par ligne (POST).
        liste = self.client.get(reverse("admin:centres_imprimanteticket_changelist"))
        self.assertContains(liste, "<h1>Imprimantes de tickets</h1>", html=True)
        self.assertNotContains(liste, "Sélectionnez")
        self.assertContains(liste, "Adresse réseau")
        url_test = reverse("admin:centres_imprimanteticket_tester", args=[self.imprimante.pk])
        self.assertContains(liste, f'formaction="{url_test}"')
        self.assertEqual(self.client.get(url_test).status_code, 405)          # jamais par un simple lien
        self.client.post(url_test)
        self.assertEqual(len(self.imprimante_reelle.attendre(3)), 3)
