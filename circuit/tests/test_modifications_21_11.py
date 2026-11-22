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
from circuit.models import Appel, ReservationQuota, Ticket
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
        self.assertContains(page, f'href="{reverse("circuit:ecran_connexion")}"')
        self.assertContains(page, 'class="horloge-barre"')         # point 5 : heure dans la barre

    def test_page_de_connexion_a_l_ecran_physique(self):
        self.connecter(self.accueil, self.d.centre)
        page = self.client.get(reverse("circuit:ecran_connexion"))
        self.assertContains(page, "Écrans reliés par câble")
        self.assertContains(page, reverse("circuit:ecrans_reseau"))       # écrans du Wi-Fi
        self.assertContains(page, "getScreenDetails")             # détection des écrans physiques
        self.assertContains(page, "screenschange")                # réaffichage quand l'écran est rebranché
        self.assertContains(page, "PresentationRequest")          # TV sans fil (Cast)
        self.assertContains(page, "http://testserver" + reverse("circuit:ecran_salle", args=["TAM"]))
        # Page ouverte avec « localhost » : la TV reçoit l'adresse réseau de l'ordinateur.
        from unittest import mock

        with mock.patch("config.settings._adresses_de_cet_ordinateur", return_value=["192.168.3.212"]):
            locale = self.client.get(reverse("circuit:ecran_connexion"), HTTP_HOST="localhost:8000")
        self.assertContains(locale, "http://192.168.3.212:8000" + reverse("circuit:ecran_salle", args=["TAM"]))
        self.assertNotContains(locale, "http://localhost:8000" + reverse("circuit:ecran_salle", args=["TAM"]))
        with mock.patch("config.settings._adresses_de_cet_ordinateur", return_value=[]):
            hors_reseau = self.client.get(reverse("circuit:ecran_connexion"), HTTP_HOST="localhost:8000")
        self.assertContains(hors_reseau, "connecté à aucun réseau")

    def test_lanceur_windows_present(self):
        from pathlib import Path

        from django.conf import settings

        script = Path(settings.BASE_DIR, "scripts", "ecran_salle.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("AllScreens", script)
        self.assertIn("--kiosk", script)
        self.assertIn("DisplaySwitch.exe", script)


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


class EcransDuWifiTests(BaseVues):
    """Les écrans du même réseau sont détectés par le serveur (UPnP/DIAL) et suivis en ligne."""

    REPONSE_TV = (
        b"HTTP/1.1 200 OK\r\nST: urn:dial-multiscreen-org:service:dial:1\r\n"
        b"LOCATION: http://192.168.3.226:8008/ssdp/device-desc.xml\r\n\r\n"
    )
    FICHE_TV = (
        b'<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
        b"<deviceType>urn:dial-multiscreen-org:device:dial:1</deviceType>"
        b"<friendlyName>GoogleTV8572</friendlyName><manufacturer>TCL</manufacturer>"
        b"<modelName>Smart TV</modelName></device></root>"
    )

    def setUp(self):
        super().setUp()
        from django.core.cache import cache

        cache.clear()

    def _reseau(self, attente):
        return [
            ("192.168.3.226", self.REPONSE_TV),
            ("8.8.8.8", self.REPONSE_TV),                                   # hors réseau local : ignoré
            ("192.168.3.40", b"HTTP/1.1 200 OK\r\nST: upnp:rootdevice\r\n\r\n"),   # routeur : pas un écran
        ]

    def test_detection_d_une_tv_du_reseau(self):
        from unittest import mock

        from core.decouverte import rechercher_ecrans

        reponse = mock.MagicMock()
        reponse.__enter__.return_value.read.return_value = self.FICHE_TV
        with mock.patch("core.decouverte.urllib.request.urlopen", return_value=reponse) as ouvrir:
            ecrans = rechercher_ecrans(envoyer=self._reseau)
        self.assertEqual(ecrans, [{
            "adresse_ip": "192.168.3.226", "dial": True, "nom": "GoogleTV8572", "marque": "TCL",
            "modele": "Smart TV", "type": "urn:dial-multiscreen-org:device:dial:1",
        }])
        ouvrir.assert_called_once()                                          # seule la fiche locale est lue

    def test_fiche_hors_reseau_local_jamais_lue(self):
        from unittest import mock

        from core.decouverte import rechercher_ecrans

        piege = b"HTTP/1.1 200 OK\r\nST: urn:dial-multiscreen-org:service:dial:1\r\nLOCATION: http://8.8.8.8/x.xml\r\n\r\n"
        with mock.patch("core.decouverte.urllib.request.urlopen") as ouvrir:
            ecrans = rechercher_ecrans(envoyer=lambda attente: [("192.168.3.50", piege)])
        ouvrir.assert_not_called()
        self.assertEqual(ecrans[0]["nom"], "Écran 192.168.3.50")

    @override_settings(RESEAU_LOCAL_UNIQUEMENT=True)
    def test_choisir_un_ecran_wifi_depuis_la_plateforme(self):
        from unittest import mock

        from centres.models import AppareilEcran

        tv = {"REMOTE_ADDR": "192.168.3.226"}
        trouvees = [{"adresse_ip": "192.168.3.226", "dial": True, "nom": "GoogleTV8572", "marque": "TCL",
                     "modele": "Smart TV", "type": ""},
                    {"adresse_ip": "192.168.3.99", "dial": True, "nom": "Salon LG", "marque": "LG",
                     "modele": "", "type": ""}]
        # 1. La TV ouvre une fois …/tv/ : « Écran prêt » et un code.
        page_tv = self.client.get(reverse("ecran_tv"), **tv)
        appareil = AppareilEcran.objects.get()
        self.assertContains(page_tv, "Écran prêt")
        self.assertContains(page_tv, appareil.code)
        self.assertEqual(self.client.get(reverse("ecran_tv_etat"), **tv).status_code, 204)
        # 2. La plateforme la liste (nom trouvé sur le réseau) ; l'autre TV est « à préparer ».
        agent = self.client_class()
        agent.force_login(self.accueil)
        session = agent.session
        session["centre_actif_id"] = self.d.centre.pk
        session.save()
        with mock.patch("core.decouverte.rechercher_ecrans", return_value=trouvees):
            liste = agent.get(reverse("circuit:ecrans_reseau") + "?rechercher=1")
        self.assertContains(liste, "GoogleTV8572")
        self.assertContains(liste, "Libre")
        self.assertContains(liste, "Afficher les appels ici")
        self.assertContains(liste, "Salon LG")
        self.assertContains(liste, "http://testserver/tv/")
        # 3. « Afficher les appels ici » : la TV passe aux appels du centre, sans qu'on la touche.
        reponse = agent.post(reverse("circuit:ecran_wifi_choisir"), {"appareil": appareil.pk, "action": "afficher"})
        self.assertContains(reponse, "Affiche les appels")
        self.assertEqual(self.client.get(reverse("ecran_tv_etat"), **tv)["HX-Redirect"], reverse("ecran_tv"))
        self.assertContains(self.client.get(reverse("ecran_tv"), **tv), reverse("ecran_tv_appels"))
        self.assertEqual(self.client.get(reverse("ecran_tv_appels"), **tv).status_code, 200)
        # Un agent d'un autre centre ne peut pas le prendre.
        autre = self.client_class()
        autre.force_login(self.agent_b)
        session = autre.session
        session["centre_actif_id"] = self.b.centre.pk
        session.save()
        autre.post(reverse("circuit:ecran_wifi_choisir"), {"appareil": appareil.pk, "action": "afficher"})
        appareil.refresh_from_db()
        self.assertEqual(appareil.centre, self.d.centre)
        # 4. « Arrêter » : la TV revient à l'attente.
        agent.post(reverse("circuit:ecran_wifi_choisir"), {"appareil": appareil.pk, "action": "arreter"})
        self.assertEqual(self.client.get(reverse("ecran_tv_appels"), **tv)["HX-Redirect"], reverse("ecran_tv"))
        self.assertContains(self.client.get(reverse("ecran_tv"), **tv), "Écran prêt")

    @override_settings(RESEAU_LOCAL_UNIQUEMENT=True)
    def test_adresse_tv_jamais_depuis_internet(self):
        self.assertEqual(self.client.get(reverse("ecran_tv"), REMOTE_ADDR="8.8.8.8").status_code, 403)


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True)
class AnnonceVocaleServeurTests(BaseVues):
    """La voix des annonces est fabriquée par le serveur : les navigateurs des TV Android n'en ont pas."""

    def _appel(self):
        self.d.ticket("M")
        return services.appeler(self.d.file("ACC"), self.accueil, poste="Guichet 1")

    def test_texte_de_l_annonce(self):
        from circuit.annonce_vocale import texte_annonce

        self.assertEqual(texte_annonce(self._appel()), "M 0 0 1, Accueil, Guichet 1")

    def test_l_ecran_recoit_le_fichier_audio_de_l_annonce(self):
        import tempfile
        from pathlib import Path
        from unittest import mock

        appel = self._appel()
        with tempfile.TemporaryDirectory() as dossier:
            faux = Path(dossier, "annonce.wav")
            faux.write_bytes(b"RIFF....WAVEfmt ")
            with mock.patch("circuit.views_postes.fichier_annonce", return_value=faux) as fabriquer:
                reponse = self.client.get(reverse("circuit:ecran_annonce", args=[appel.pk]))
                self.assertEqual(reponse["Content-Type"], "audio/wav")
                self.assertEqual(reponse.content, b"RIFF....WAVEfmt ")
            fabriquer.assert_called_once_with("M 0 0 1, Accueil, Guichet 1")   # texte de l'appel, jamais du navigateur
        with mock.patch("circuit.views_postes.fichier_annonce", return_value=None):
            self.assertEqual(self.client.get(reverse("circuit:ecran_annonce", args=[appel.pk])).status_code, 404)
        reponse = self.client.get(reverse("circuit:ecran_annonce", args=[appel.pk]), REMOTE_ADDR="8.8.8.8")
        self.assertEqual(reponse.status_code, 403)

    def test_l_ecran_joue_la_voix_du_serveur(self):
        from unittest import mock

        with mock.patch("circuit.views_postes.voix_disponible", return_value=True):
            page = self.client.get(reverse("circuit:ecran_salle", args=["TAM"]))
        self.assertContains(page, "const voixServeur = true")
        self.assertContains(page, reverse("circuit:ecran_annonce", args=[0]))


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True)
class RechargementDesEcransTests(BaseVues):
    def test_l_ecran_se_recharge_quand_le_serveur_est_mis_a_jour(self):
        from circuit.views_postes import VERSION_ECRANS

        page = self.client.get(reverse("circuit:ecran_salle", args=["TAM"]))
        self.assertContains(page, f'!== "{VERSION_ECRANS}"')
        fragment = self.client.get(reverse("circuit:ecran_salle_appels", args=["TAM"]))
        self.assertContains(fragment, f'data-version-ecrans="{VERSION_ECRANS}"')


    def test_une_page_d_ecran_perimee_est_rechargee_par_le_serveur(self):
        from circuit.views_postes import VERSION_ECRANS

        url = reverse("circuit:ecran_salle_appels", args=["TAM"])
        ancienne = self.client.get(url, HTTP_HX_REQUEST="true")                      # page d'avant la mise à jour
        self.assertEqual(ancienne["HX-Redirect"], reverse("circuit:ecran_salle", args=["TAM"]))
        a_jour = self.client.get(url, {"v": VERSION_ECRANS}, HTTP_HX_REQUEST="true")
        self.assertNotIn("HX-Redirect", a_jour)
        self.assertContains(self.client.get(reverse("circuit:ecran_salle", args=["TAM"])), f"?v={VERSION_ECRANS}")



class SalleDuMedecinTests(BaseVues):
    """Le médecin indique sa salle à la connexion ; elle est annoncée à chacun de ses appels."""

    def test_salle_demandee_a_la_connexion_puis_annoncee(self):
        from circuit.annonce_vocale import texte_annonce
        from comptes.models import AffectationUtilisateur

        infirmier = self.d.agent("inf_s", "Infirmerie", ["INF"])
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", infirmier)                     # M001 attend chez les médecins
        med = self.d.svc["MED"].pk
        self.connecter(self.medecin, self.d.centre, salle_indiquee=False)
        # À la connexion, le médecin doit d'abord indiquer sa salle.
        reponse = self.client.get(reverse("circuit:guichet", args=[med]))
        self.assertRedirects(reponse, reverse("circuit:ma_salle") + "?next=" + reverse("circuit:guichet", args=[med]),
                             fetch_redirect_response=False)
        page = self.client.get(reverse("circuit:ma_salle"))
        self.assertContains(page, "Indiquer votre salle")
        self.client.post(reverse("circuit:ma_salle"), {"salle": ""})               # obligatoire
        self.assertEqual(AffectationUtilisateur.objects.get(utilisateur=self.medecin).salle, "")
        self.client.post(reverse("circuit:ma_salle"), {"salle": " 1 ", "next": reverse("circuit:guichet", args=[med])})
        self.assertEqual(AffectationUtilisateur.objects.get(utilisateur=self.medecin).salle, "1")
        guichet = self.client.get(reverse("circuit:guichet", args=[med]))
        self.assertContains(guichet, 'value="1"')
        self.assertContains(guichet, "Votre salle : <strong>1</strong>")
        # L'appel annonce la salle, sans le mot « ticket ».
        self.client.post(reverse("circuit:guichet_action", args=[med, "appeler"]), {"poste": ""})
        appel = Appel.objects.get(service__code="MED")
        self.assertEqual(appel.poste, "1")
        self.assertEqual(texte_annonce(appel), "M 0 0 1, Médecin, 1")

    def test_les_autres_agents_ne_sont_pas_obliges(self):
        self.connecter(self.accueil, self.d.centre, salle_indiquee=False)
        self.assertEqual(self.client.get(reverse("circuit:guichets")).status_code, 200)

    def test_l_ecran_n_affiche_plus_le_mot_ticket_dans_l_annonce(self):
        self.d.ticket("M")
        services.appeler(self.d.file("ACC"), self.accueil, poste="Guichet 1")
        self.connecter(self.accueil, self.d.centre)
        fragment = self.client.get(reverse("circuit:ecran_appels"))
        self.assertContains(fragment, 'data-texte="M 0 0 1, Accueil, Guichet 1"')
