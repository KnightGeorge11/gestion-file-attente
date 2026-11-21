"""
Corrections P1 de l'audit du 20/11/2026 : chaque test reproduit le défaut
constaté (et échouait sur l'ancien code).
"""

import datetime
from unittest import mock

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from audit.models import JournalAudit
from centres.models import Borne, ImprimanteTicket
from circuit import services
from circuit.impression import ErreurImpression, envoyer
from circuit.models import Evenement, Passage, Ticket
from circuit.services import ErreurCircuit
from core.reseau import adresse_client, est_adresse_privee
from statistiques.calculs import calculer

from .base import activer_borne, LUNDI_MATIN, Donnees, a_heure
from .test_vues import BaseVues


class AdresseClientTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    @override_settings(PROXIES_DE_CONFIANCE=())
    def test_x_real_ip_ignore_sans_proxy_de_confiance(self):
        requete = self.rf.get("/", REMOTE_ADDR="8.8.8.8", HTTP_X_REAL_IP="192.168.1.20")
        self.assertEqual(adresse_client(requete), "8.8.8.8")   # un client ne peut pas se déguiser

    @override_settings(PROXIES_DE_CONFIANCE=("127.0.0.1",))
    def test_x_real_ip_lu_derriere_nginx(self):
        requete = self.rf.get("/", REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="8.8.8.8")
        self.assertEqual(adresse_client(requete), "8.8.8.8")

    @override_settings(PROXIES_DE_CONFIANCE=("127.0.0.1",), RESEAU_LOCAL_UNIQUEMENT=True)
    def test_borne_refusee_depuis_internet_derriere_nginx(self):
        reponse = self.client.get(reverse("circuit:borne"), REMOTE_ADDR="127.0.0.1", HTTP_X_REAL_IP="8.8.8.8")
        self.assertEqual(reponse.status_code, 403)

    def test_adresses_privees(self):
        self.assertTrue(est_adresse_privee("192.168.1.50"))
        for adresse in ("8.8.8.8", "127.0.0.1", "169.254.1.1", "0.0.0.0", "pas une ip"):
            self.assertFalse(est_adresse_privee(adresse), adresse)


@override_settings(IMPRIMANTE_SIMULEE_AUTORISEE=False)
class ImprimanteReseauLocalTests(TestCase):
    def setUp(self):
        self.d = Donnees().construire()
        self.b = Donnees().construire("ANT")

    def _imprimante(self, adresse, port=9100, centre=None):
        return ImprimanteTicket(centre=centre or self.d.centre, libelle="Thermique", adresse_ip=adresse, port=port)

    def test_adresse_internet_ou_port_hors_plage_refuses(self):
        for adresse, port in (("8.8.8.8", 9100), ("127.0.0.1", 9100), ("192.168.1.50", 22), ("192.168.1.50", 70000)):
            with self.assertRaises(ValidationError, msg=f"{adresse}:{port}"):
                self._imprimante(adresse, port).full_clean()
        self._imprimante("192.168.1.50", 9100).full_clean()

    def test_le_serveur_ne_se_connecte_jamais_hors_du_reseau_local(self):
        with mock.patch("circuit.impression.socket.create_connection") as connexion:
            with self.assertRaisesMessage(ErreurImpression, "refusée"):
                envoyer(self._imprimante("8.8.8.8", 9100), b"x")
            with self.assertRaisesMessage(ErreurImpression, "refusée"):
                envoyer(self._imprimante("192.168.1.50", 3306), b"x")
            connexion.assert_not_called()

    @override_settings(IMPRIMANTE_SIMULEE_AUTORISEE=True)
    def test_imprimante_simulee_acceptee_en_developpement(self):
        self._imprimante("127.0.0.1", 9100).full_clean()

    def test_borne_et_imprimante_du_meme_centre(self):
        imprimante = self._imprimante("192.168.1.50", centre=self.b.centre)
        imprimante.save()
        borne = Borne(centre=self.d.centre, code="B9", libelle="Borne", imprimante=imprimante, cle_hash="!")
        with self.assertRaises(ValidationError):
            borne.full_clean()


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0, BORNE_MAX_TICKETS_PAR_HEURE=3)
class BorneProtectionTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.borne = Borne(centre=self.d.centre, code="B1", libelle="Borne entrée")
        cle = self.borne.generer_cle()
        self.borne.save()
        activer_borne(self.client, Borne.objects.get(centre__code="TAM", code="B1"))

    def _emettre(self):
        return self.client.post(reverse("circuit:borne_emettre"),
                                {"mode": "EXTERNE", "parcours": self.d.parcours["E"].pk}, follow=True)

    def test_plafond_de_tickets_par_heure(self):
        for _ in range(3):
            self._emettre()
        reponse = self._emettre()
        self.assertEqual(Ticket.objects.count(), 3)
        self.assertContains(reponse, "momentanément saturée")

    def test_ticket_bluetooth_deja_imprime_pas_de_second_envoi(self):
        self._emettre()
        ticket = Ticket.objects.get()
        url = reverse("circuit:borne_ticket_escpos", args=[ticket.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(reverse("circuit:borne_impression_android", args=[ticket.pk]), {"ok": "1"})
        self.assertTrue(ticket.evenements.filter(type=Evenement.Type.IMPRESSION).exists())
        self.assertEqual(self.client.get(url).status_code, 403)


@override_settings(CONNEXION_MAX_ECHECS=5, CONNEXION_MAX_ECHECS_IP=8, CONNEXION_BLOCAGE_MINUTES=15)
class BlocageParPosteTests(BaseVues):
    def _connexion(self, mot_de_passe, identifiant="accueil1", ip="192.168.1.20"):
        return self.client.post(reverse("connexion"), {"username": identifiant, "password": mot_de_passe},
                                REMOTE_ADDR=ip)

    def test_echecs_depuis_un_autre_poste_ne_bloquent_pas_le_titulaire(self):
        for _ in range(5):
            self._connexion("faux", ip="192.168.1.66")
        self.assertContains(self._connexion("mot-de-passe-1", ip="192.168.1.66"), "Trop de tentatives")
        self.assertEqual(self._connexion("mot-de-passe-1", ip="192.168.1.20").status_code, 302)

    def test_un_poste_qui_essaie_plusieurs_comptes_est_bloque(self):
        for i in range(8):
            self._connexion("faux", identifiant=f"inconnu{i}", ip="192.168.1.66")
        self.assertContains(self._connexion("mot-de-passe-1", "med1", ip="192.168.1.66"), "depuis ce poste")
        self.assertEqual(self._connexion("mot-de-passe-1", "med1", ip="192.168.1.20").status_code, 302)

    def test_l_echec_garde_l_adresse_ip(self):
        self._connexion("faux", ip="192.168.1.66")
        self.assertEqual(JournalAudit.objects.get(action="ECHEC_CONNEXION").adresse_ip, "192.168.1.66")


class JournalAuditCompletTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.admin = self.d.agent("admin_central", "Accueil")
        self.admin.is_superuser = self.admin.is_staff = True
        self.admin.save()
        self.client.force_login(self.admin)

    def test_changement_de_mot_de_passe_journalise_avec_ip_sans_le_mot_de_passe(self):
        url = reverse("admin:auth_user_password_change", args=[self.medecin.pk])
        self.client.post(url, {"password1": "Nouveau-mot-2026!", "password2": "Nouveau-mot-2026!",
                               "usable_password": "true"}, REMOTE_ADDR="192.168.1.30")
        entree = JournalAudit.objects.filter(objet_id=str(self.medecin.pk), details__mot_de_passe="modifié").get()
        self.assertEqual((entree.utilisateur, entree.adresse_ip), (self.admin, "192.168.1.30"))
        self.assertNotIn("Nouveau", str(entree.details))

    def test_borne_creee_et_cle_d_ecran_regeneree(self):
        from centres.models import EcranAffichage

        Borne.objects.create(centre=self.d.centre, code="B2", libelle="Borne 2")
        self.assertTrue(JournalAudit.objects.filter(action="CREATION", objet_type="centres.borne").exists())
        ecran = EcranAffichage(centre=self.d.centre, code="TV2", libelle="Écran 2")
        ecran.generer_cle()
        ecran.save()
        self.client.post(reverse("admin:centres_ecranaffichage_changelist"),
                         {"action": "regenerer_cle", "_selected_action": [ecran.pk]}, REMOTE_ADDR="192.168.1.31")
        entree = JournalAudit.objects.filter(action="MODIFICATION", objet_type="centres.ecranaffichage").get()
        self.assertEqual(entree.details, {"cle": "régénérée"})
        self.assertEqual(entree.adresse_ip, "192.168.1.31")
        ecran.refresh_from_db()
        self.assertNotIn(ecran.cle_hash, str(entree.details))

    def test_droits_d_un_role_journalises(self):
        groupe = Group.objects.get(name="Accueil")
        groupe.permissions.add(Permission.objects.get(codename="voir_statistiques"))
        self.assertTrue(JournalAudit.objects.filter(action="ROLE", objet_type="auth.group",
                                                    objet_id=str(groupe.pk)).exists())


class PrefixeEtModeAutreTests(BaseVues):
    def test_prefixe_a_reserve_aux_tickets_autre(self):
        parcours = self.d.parcours["E"]
        parcours.prefixe = "A"
        with self.assertRaisesMessage(ValidationError, "réservé"):
            parcours.full_clean()

    def test_duree_totale_moyenne_hors_tickets_autre(self):
        with a_heure(LUNDI_MATIN):
            ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
            entree = self.d.entree(ticket)
            services.appeler(self.d.file("ACC"), self.accueil, entree=entree)
            services.prendre_en_charge(entree, self.accueil)
            services.transmettre_demande(ticket.demande, self.accueil, destinataire=self.medecin)
        with a_heure(LUNDI_MATIN + datetime.timedelta(minutes=3)):
            services.terminer(entree, self.accueil)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        stats = calculer([self.d.centre], LUNDI_MATIN.date(), LUNDI_MATIN.date())
        self.assertIsNone(stats["duree_totale_moy"])   # un ticket « Autre » n'est pas un parcours de soins


class RolesSansGestionDesPatientsTests(BaseVues):
    def test_direction_responsable_admin_centre_sans_absence_ni_reintegration(self):
        for role in ("Direction", "Responsable", "Administrateur de centre"):
            codes = set(Group.objects.get(name=role).permissions.values_list("codename", flat=True))
            self.assertNotIn("marquer_absent", codes, role)
            self.assertNotIn("reintegrer", codes, role)
            self.assertIn("appeler", codes, role)   # accès aux écrans des services

    def test_un_responsable_ne_peut_pas_reintegrer_un_absent(self):
        file = self.d.file("ACC")
        file.delai_entre_appels_secondes = 0
        file.save()
        ticket = self.d.ticket("M")
        services.appeler(file, self.accueil)
        for _ in range(file.nb_appels_avant_absence - 1):
            services.rappeler(self.d.entree(ticket), self.accueil)
        services.marquer_absent(ticket.passages.get().entrees.get(), self.accueil)
        passage = ticket.passages.get()
        self.assertEqual(passage.statut, Passage.Statut.ABSENT)
        responsable = self.d.agent("resp_r", "Responsable", ["ACC"])
        with self.assertRaisesMessage(ErreurCircuit, "Tout passe d'abord par l'accueil"):
            services.reintegrer(passage, responsable)
        services.reintegrer(passage, self.accueil)


class FileSansRequeteParPatientTests(BaseVues):
    def _requetes(self):
        self.connecter(self.accueil, self.d.centre)
        url = reverse("circuit:guichet_liste", args=[self.d.svc["ACC"].pk])
        with CaptureQueriesContext(connection) as requetes:
            self.assertEqual(self.client.get(url).status_code, 200)
        return len(requetes)

    def test_nombre_de_requetes_independant_du_nombre_de_patients(self):
        self.d.ticket("M")
        self.d.ticket("D")
        self._requetes()   # premier affichage : clôture automatique et caches
        avec_deux = self._requetes()
        for prefixe in ("M", "D", "E", "I", "M", "E"):
            self.d.ticket(prefixe)
        self.assertEqual(self._requetes(), avec_deux)
