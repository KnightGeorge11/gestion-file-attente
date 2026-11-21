"""
Modifications demandées le 21/11/2026 :
1. écran de la salle ouvert depuis l'ordinateur, sans clé ni Internet ;
2. bornes sans clé, activées une fois par un administrateur, jamais déconnectées ;
3. clôture et remise à zéro des numéros à 23 h 59 ;
4. durée de chaque passage affichée ;
5. heure exacte (serveur, heure du centre) affichée ;
6. services proposés choisis par borne (administrateur de la plateforme) ;
7. l'accueil délivre les tickets : le patient va directement au service.
"""

import datetime

from django.contrib.admin.sites import site
from django.test import RequestFactory, override_settings
from django.urls import reverse

from centres.models import Borne, Centre
from circuit import services
from circuit.impression import contenu_ticket
from circuit.management.commands.imprimante_simulee import texte_lisible
from circuit.models import ReservationQuota, Ticket
from circuit.services import ErreurCircuit
from circuit.views_postes import COOKIE_BORNE

from .base import LUNDI_MATIN, a_heure, activer_borne
from .test_vues import BaseVues


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0)
class BorneSansCleTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Borne accueil")
        self.borne_b = Borne.objects.create(centre=self.b.centre, code="B9", libelle="Borne Antsirabe")

    def test_borne_non_activee_demande_un_administrateur(self):
        page = self.client.get(reverse("circuit:borne"))
        self.assertContains(page, "Connexion administrateur")
        self.assertNotContains(page, "Clé")
        self.client.force_login(self.accueil)
        self.assertContains(self.client.get(reverse("circuit:borne")), "n'administre aucune borne")
        # Un agent ne peut pas activer une borne en envoyant le formulaire.
        self.client.post(reverse("circuit:borne"), {"borne": self.borne.pk})
        self.assertNotIn(COOKIE_BORNE, self.client.cookies)

    def test_admin_de_centre_active_la_borne_qui_reste_activee(self):
        admin = self.d.agent("admin_c", "Administrateur de centre")
        self.client.force_login(admin)
        page = self.client.get(reverse("circuit:borne"))
        self.assertContains(page, "Borne accueil")
        self.assertNotContains(page, "Borne Antsirabe")          # autre centre : pas proposé
        self.client.post(reverse("circuit:borne"), {"borne": self.borne_b.pk})
        self.assertNotIn(COOKIE_BORNE, self.client.cookies)       # refusé
        reponse = self.client.post(reverse("circuit:borne"), {"borne": self.borne.pk})
        self.assertRedirects(reponse, reverse("circuit:borne"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)    # l'administrateur est déconnecté
        self.assertContains(self.client.get(reverse("circuit:borne")), "Échographie")
        # Un agent qui se connecte puis se déconnecte sur l'appareil ne désactive pas la borne.
        self.client.force_login(self.accueil)
        self.client.post(reverse("deconnexion"))
        self.assertContains(self.client.get(reverse("circuit:borne")), "Échographie")
        cookie = self.client.cookies[COOKIE_BORNE]
        self.assertGreaterEqual(int(cookie["max-age"]), 9 * 365 * 24 * 3600)   # 10 ans

    def test_borne_activee_autrefois_avec_une_cle_reste_activee(self):
        session = self.client.session
        session["borne_id"] = self.borne.pk
        session.save()
        self.assertContains(self.client.get(reverse("circuit:borne")), "Échographie")
        self.assertIn(COOKIE_BORNE, self.client.cookies)           # reportée dans le cookie

    def test_plus_de_cle_dans_l_administration_des_bornes(self):
        modele_admin = site._registry[Borne]
        requete = RequestFactory().get("/")
        requete.user = self.d.agent("super", "Accueil")
        requete.user.is_superuser = requete.user.is_staff = True
        self.assertNotIn("regenerer_cle", modele_admin.get_actions(requete))


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True)
class EcranDeLaSalleTests(BaseVues):
    def test_ecran_de_la_salle_sans_compte_ni_cle(self):
        self.d.ticket("M")
        services.appeler(self.d.file("ACC"), self.accueil)
        page = self.client.get(reverse("circuit:ecran_salle", args=["tam"]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, reverse("circuit:ecran_salle_appels", args=["TAM"]))
        self.assertContains(page, "Plein écran et son")
        self.assertContains(self.client.get(reverse("circuit:ecran_salle_appels", args=["TAM"])), "M001")

    def test_jamais_depuis_internet(self):
        reponse = self.client.get(reverse("circuit:ecran_salle", args=["TAM"]), REMOTE_ADDR="8.8.8.8")
        self.assertEqual(reponse.status_code, 403)
        self.assertEqual(self.client.get(reverse("circuit:ecran_salle", args=["XYZ"])).status_code, 404)

    def test_lien_du_menu_vers_l_ecran_de_la_salle(self):
        self.connecter(self.accueil, self.d.centre)
        page = self.client.get(reverse("circuit:guichets"))
        self.assertContains(page, f'href="{reverse("circuit:ecran_salle", args=["TAM"])}"')
        self.assertContains(page, "data-ecran-salle")
        self.assertContains(page, 'class="horloge-barre"')         # point 5 : heure dans la barre


class ClotureA23h59Tests(BaseVues):
    def test_cloture_et_remise_a_zero_a_23h59(self):
        centre = Centre.objects.create(code="NEW", nom="Nouveau")
        self.assertEqual(centre.heure_cloture_tickets, datetime.time(23, 59))
        self.d.centre.heure_cloture_tickets = datetime.time(23, 59)
        self.d.centre.save()
        lundi_21h = LUNDI_MATIN.replace(hour=18)         # 21 h à Antananarivo
        lundi_23h59 = LUNDI_MATIN.replace(hour=20, minute=59)
        with a_heure(lundi_21h):
            self.assertFalse(services.emission_terminee(self.d.centre))   # avant : borne fermée dès 20 h
            self.assertEqual(self.d.ticket("M").numero, "M001")
        with a_heure(lundi_23h59):
            self.assertTrue(services.emission_terminee(self.d.centre))
            services.cloturer_tickets(self.d.centre)
        with a_heure(LUNDI_MATIN + datetime.timedelta(days=1)):
            self.assertEqual(self.d.ticket("M").numero, "M001")            # nouveau jour


class DureesDesPassagesTests(BaseVues):
    def test_duree_de_chaque_passage_sur_le_ticket_et_au_guichet(self):
        infirmier = self.d.agent("inf_d", "Infirmerie", ["INF"])
        with a_heure(LUNDI_MATIN):
            ticket = self.d.ticket("M")
        with a_heure(LUNDI_MATIN + datetime.timedelta(minutes=4)):
            services.appeler(self.d.file("ACC"), self.accueil)
            services.prendre_en_charge(self.d.entree(ticket), self.accueil)
        with a_heure(LUNDI_MATIN + datetime.timedelta(minutes=7)):
            services.terminer(self.d.entree(ticket), self.accueil)
            services.appeler(self.d.file("INF"), infirmier)
        accueil = ticket.passages.get(service__code="ACC")
        self.assertEqual(accueil.duree_passage, datetime.timedelta(minutes=7))
        # Pages ouvertes le jour même (sinon la clôture automatique fermerait le ticket).
        with a_heure(LUNDI_MATIN + datetime.timedelta(minutes=9)):
            self.connecter(self.accueil, self.d.centre)
            page = self.client.get(reverse("circuit:ticket", args=[ticket.pk]))
            self.assertContains(page, "Durée du passage")
            self.assertContains(page, "7 min")
            self.connecter(infirmier, self.d.centre)
            panneau = self.client.get(reverse("circuit:guichet", args=[self.d.svc["INF"].pk]))
            self.assertContains(panneau, "Passages précédents")
            self.assertContains(panneau, "7 min")


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0)
class ServicesParBorneTests(BaseVues):
    def test_la_borne_ne_propose_que_ses_services(self):
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Borne dentaire")
        borne.parcours.set([self.d.parcours["D"]])
        activer_borne(self.client, borne)
        page = self.client.get(reverse("circuit:borne"))
        self.assertContains(page, "Dentiste")
        self.assertNotContains(page, "Échographie")
        self.client.post(reverse("circuit:borne_emettre"), {"mode": "EXTERNE", "parcours": self.d.parcours["E"].pk})
        self.assertFalse(Ticket.objects.exists())                 # service non proposé : refusé

    def test_seul_l_admin_de_la_plateforme_choisit_les_services(self):
        modele_admin = site._registry[Borne]
        requete = RequestFactory().get("/")
        requete.user = self.d.agent("admin_c", "Administrateur de centre")
        self.assertIn("parcours", modele_admin.get_readonly_fields(requete))
        requete.user = self.d.agent("super", "Accueil")
        requete.user.is_superuser = True
        self.assertNotIn("parcours", modele_admin.get_readonly_fields(requete))


class AccueilDelivreLesTicketsTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.d.centre.accueil_delivre_tickets = True
        self.d.centre.save()

    def test_le_patient_va_directement_au_service(self):
        ticket = self.d.ticket("M")
        premier = ticket.passages.get()
        self.assertEqual(premier.service.code, "INF")             # pas de file à l'accueil
        self.assertEqual(premier.ordre_etape, 2)
        self.assertEqual(self.d.entree(ticket).file.service.code, "INF")
        texte = texte_lisible(contenu_ticket(ticket, _Imprimante()))
        self.assertIn("Présentez-vous au service Infirmerie", texte)
        # Le parcours continue normalement ensuite.
        infirmier = self.d.agent("inf_a", "Infirmerie", ["INF"])
        suite = self.d.traiter(ticket, "INF", infirmier)
        self.assertEqual(suite.service.code, "MED")

    def test_quota_dentiste_reserve_au_ticket_et_refus_si_atteint(self):
        with a_heure(LUNDI_MATIN):
            ticket = self.d.ticket("D")
            reservation = ticket.reservation_active()
            self.assertIsNotNone(reservation)
            self.assertIsNone(reservation.reservee_par)            # réservée par la borne
            quota = self.d.parcours["D"].quotas.get(jour_semaine=0, periode="MATIN")
            quota.nombre_places = 1
            quota.save()
            with self.assertRaisesMessage(ErreurCircuit, "Quota atteint"):
                self.d.ticket("D")
        self.assertEqual(Ticket.objects.filter(prefixe="D").count(), 1)   # aucun ticket ni numéro consommé
        self.assertEqual(ReservationQuota.objects.count(), 1)

    def test_les_demandes_autre_restent_orientees_par_l_accueil(self):
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        self.assertEqual(ticket.passages.get().service.code, "ACC")


class _Imprimante:
    page_codes = "cp858"
    largeur_caracteres = 48
