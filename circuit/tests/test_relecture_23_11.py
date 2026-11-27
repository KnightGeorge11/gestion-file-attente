"""
Relecture du code du 23/11/2026, lot 1 : défauts trouvés à la relecture et
non couverts par les tests existants.
"""

from django.test import RequestFactory
from django.urls import reverse

from centres.models import Borne
from circuit import services
from circuit.views_postes import COOKIE_BORNE, _borne_de_l_appareil

from .base import activer_borne
from .test_vues import BaseVues


class AccueilVoitTousLesTicketsTests(BaseVues):
    """Avec « L'accueil délivre les tickets », le ticket part directement au premier service."""

    def setUp(self):
        super().setUp()
        self.d.centre.accueil_delivre_tickets = True
        self.d.centre.save(update_fields=["accueil_delivre_tickets"])

    def test_l_accueil_voit_et_annule_un_ticket_parti_au_premier_service(self):
        ticket = self.d.ticket("D")
        self.assertFalse(ticket.passages.filter(service__est_accueil=True).exists())
        self.connecter(self.accueil, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:tickets")), ticket.numero)
        page = self.client.get(reverse("circuit:ticket", args=[ticket.pk]))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Annuler")
        self.client.post(reverse("circuit:ticket_action", args=[ticket.pk, "annuler"]), {"motif": "Erreur"})
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, "ANNULE")

    def test_les_autres_services_restent_limites_a_leurs_patients(self):
        ticket = self.d.ticket("D")
        self.connecter(self.medecin, self.d.centre)
        self.assertEqual(self.client.get(reverse("circuit:ticket", args=[ticket.pk])).status_code, 404)
        self.connecter(self.agent_b, self.b.centre)
        self.assertEqual(self.client.get(reverse("circuit:ticket", args=[ticket.pk])).status_code, 404)


class RecevoirLePatientTests(BaseVues):
    """« Recevoir le patient » (Notifications) est un formulaire boosté : il attend une page complète."""

    def test_formulaire_booste_redirige_vers_le_guichet(self):
        ticket = self.d.ticket("M")
        entree = self.d.entree(ticket)
        self.connecter(self.accueil, self.d.centre)
        acc = self.d.svc["ACC"].pk
        reponse = self.client.post(
            reverse("circuit:guichet_action", args=[acc, "prendre"]),
            {"entree": entree.pk, "file": entree.file_id, "oral": "1"},
            headers={"HX-Request": "true", "HX-Boosted": "true"},
        )
        self.assertRedirects(reponse, f"{reverse('circuit:guichet', args=[acc])}?file={entree.file_id}",
                             fetch_redirect_response=False)

    def test_requete_htmx_du_guichet_recoit_toujours_le_fragment(self):
        ticket = self.d.ticket("M")
        entree = self.d.entree(ticket)
        services.appeler(entree.file, self.accueil, entree=entree)
        self.connecter(self.accueil, self.d.centre)
        reponse = self.client.post(
            reverse("circuit:guichet_action", args=[self.d.svc["ACC"].pk, "prendre"]),
            {"entree": entree.pk, "file": entree.file_id},
            headers={"HX-Request": "true"},
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertNotContains(reponse, "<header")


class LienChangerMaSalleTests(BaseVues):
    def test_le_lien_ramene_au_guichet_meme_dans_le_fragment(self):
        self.connecter(self.medecin, self.d.centre)
        med = self.d.svc["MED"].pk
        fragment = self.client.get(reverse("circuit:guichet_zone", args=[med]), headers={"HX-Request": "true"})
        self.assertNotContains(fragment, f"/services/{med}/patient")
        lien = reverse("circuit:guichet", args=[med])
        self.assertContains(fragment, f"?next={lien}%3Ffile%3D{self.d.file('MED').pk}")


class ActiverUneAutreBorneTests(BaseVues):
    """« Activer sur cet appareil » sur un appareil qui est déjà une autre borne."""

    def _borne_memorisee(self):
        requete = RequestFactory().get("/")
        requete.COOKIES[COOKIE_BORNE] = self.client.cookies[COOKIE_BORNE].value
        return int(_borne_de_l_appareil(requete))

    def test_remplace_la_borne_memorisee(self):
        admin = self.d.agent("admin_centre", "Administrateur de centre")
        admin.is_staff = True
        admin.save(update_fields=["is_staff"])
        ancienne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Ancienne")
        nouvelle = Borne.objects.create(centre=self.d.centre, code="B2", libelle="Nouvelle")
        activer_borne(self.client, ancienne)
        self.connecter(admin, self.d.centre)
        reponse = self.client.post(reverse("circuit:borne"), {"borne": nouvelle.pk})
        self.assertRedirects(reponse, reverse("circuit:borne"), fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self._borne_memorisee(), nouvelle.pk)

    def test_un_agent_ne_change_pas_la_borne(self):
        ancienne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Ancienne")
        nouvelle = Borne.objects.create(centre=self.d.centre, code="B2", libelle="Nouvelle")
        activer_borne(self.client, ancienne)
        self.connecter(self.accueil, self.d.centre)
        self.client.post(reverse("circuit:borne"), {"borne": nouvelle.pk})
        self.assertEqual(self._borne_memorisee(), ancienne.pk)


# ---------------------------------------------------------------------------
# Lot 2
# ---------------------------------------------------------------------------

class DemandeAutreOrienteeDepuisLaPageDemandesTests(BaseVues):
    def test_ticket_cloture_apres_l_orientation(self):
        directeur = self.d.agent("directeur", "Direction")
        autre = services.emettre_ticket(self.d.centre, "AUTRE", motif=self.d.motif)
        self.connecter(self.accueil, self.d.centre)
        self.client.post(reverse("circuit:demandes"), {"demande": autre.demande.pk, "destinataire": directeur.pk})
        autre.refresh_from_db()
        self.assertEqual(autre.statut, "TERMINE")
        self.assertFalse(autre.passages.filter(statut__in=["EN_ATTENTE", "APPELE", "EN_COURS"]).exists())
        self.assertEqual(autre.demande.notifications.get().destinataire, directeur)

    def test_ticket_tenu_au_guichet_termine_par_l_agent(self):
        directeur = self.d.agent("directeur", "Direction")
        autre = services.emettre_ticket(self.d.centre, "AUTRE", motif=self.d.motif)
        entree = self.d.entree(autre)
        services.appeler(entree.file, self.accueil, entree=entree)
        services.prendre_en_charge(entree, self.accueil)
        services.transmettre_demande(autre.demande, self.accueil, destinataire=directeur)
        autre.refresh_from_db()
        self.assertTrue(autre.ouvert)           # l'agent le termine lui-même au guichet
        services.terminer(self.d.entree(autre), self.accueil)
        autre.refresh_from_db()
        self.assertEqual(autre.statut, "TERMINE")


class ReglagesValidesTests(BaseVues):
    def test_fuseau_horaire_inconnu_refuse(self):
        from django.core.exceptions import ValidationError

        centre = self.d.centre
        centre.fuseau_horaire = "UTC+3"
        with self.assertRaises(ValidationError):
            centre.full_clean()
        centre.fuseau_horaire = " Indian/Antananarivo "
        centre.full_clean()
        self.assertEqual(centre.fuseau_horaire, "Indian/Antananarivo")


class StatistiquesRelectureTests(BaseVues):
    def test_passage_cloture_automatiquement_hors_prise_en_charge(self):
        import datetime

        from statistiques.calculs import calculer

        from .base import LUNDI_MATIN, a_heure

        with a_heure(LUNDI_MATIN):
            oublie = self.d.ticket("I")
            entree = self.d.entree(oublie)
            services.appeler(entree.file, self.accueil, entree=entree)
            services.prendre_en_charge(entree, self.accueil)
        with a_heure(LUNDI_MATIN + datetime.timedelta(minutes=4)):
            normal = self.d.ticket("I")
            entree2 = self.d.entree(normal)
        with a_heure(LUNDI_MATIN + datetime.timedelta(minutes=10)):
            services.annuler_ticket(oublie, self.accueil, "Oubli")
            services.appeler(entree2.file, self.accueil, entree=entree2)
            services.prendre_en_charge(entree2, self.accueil)
        with a_heure(LUNDI_MATIN + datetime.timedelta(minutes=12)):
            services.terminer(self.d.entree(normal), self.accueil)
        stats = calculer([self.d.centre], LUNDI_MATIN.date(), LUNDI_MATIN.date())
        accueil = next(l for l in stats["par_service"] if l["service"] == "Accueil")
        self.assertEqual(accueil["prise_en_charge_moy"], 2.0)

class IdentifiantsNonNumeriquesTests(BaseVues):
    def test_pas_d_erreur_500(self):
        self.client.raise_request_exception = False
        self.connecter(self.accueil, self.d.centre)
        acc = self.d.svc["ACC"].pk
        reponses = [
            self.client.get(reverse("circuit:guichet", args=[acc]) + "?file=abc"),
            self.client.post(reverse("circuit:guichet_action", args=[acc, "prendre"]), {"entree": "abc"}),
            self.client.post(reverse("circuit:demandes"), {"demande": "abc"}),
            self.client.post(reverse("circuit:notifications"), {"notification": "abc"}),
            self.client.post(reverse("centres:selection"), {"centre_id": "abc"}),
        ]
        for reponse in reponses:
            self.assertLess(reponse.status_code, 500, reponse.request["PATH_INFO"])
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Entrée")
        self.client.logout()
        activer_borne(self.client, borne)
        reponse = self.client.post(reverse("circuit:borne_emettre"), {"mode": "EXTERNE", "parcours": "abc"})
        self.assertLess(reponse.status_code, 500)


class ServiceSuivantSuspenduTests(BaseVues):
    """Décision du 23/11/2026 : le patient attend dans la file du service suspendu."""

    def setUp(self):
        super().setUp()
        self.infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        med = self.d.svc["MED"]
        med.suspendu = True
        med.motif_suspension = "Réunion"
        med.save(update_fields=["suspendu", "motif_suspension"])

    def test_l_infirmier_termine_et_le_patient_attend_les_medecins(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("INF"), self.infirmier, entree=entree)
        services.prendre_en_charge(entree, self.infirmier)
        self.connecter(self.infirmier, self.d.centre)
        reponse = self.client.post(
            reverse("circuit:guichet_action", args=[self.d.svc["INF"].pk, "terminer"]),
            {"entree": entree.pk, "file": entree.file_id}, follow=True,
        )
        self.assertContains(reponse, "sera appelé à la reprise")
        attente = self.d.entree(ticket)
        self.assertEqual((attente.file_id, attente.statut), (self.d.file("MED").pk, "EN_ATTENTE"))
        self.assertIsNone(services.entree_tenue_par(self.infirmier))     # l'infirmier est libre
        with self.assertRaises(services.ErreurCircuit):
            services.appeler(self.d.file("MED"), self.medecin)            # appel à la reprise seulement

    def test_service_desactive_toujours_refuse(self):
        med = self.d.svc["MED"]
        med.suspendu, med.actif = False, False
        med.save(update_fields=["suspendu", "actif"])
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("INF"), self.infirmier, entree=entree)
        services.prendre_en_charge(entree, self.infirmier)
        with self.assertRaises(services.ErreurCircuit):
            services.terminer(entree, self.infirmier)


# ---------------------------------------------------------------------------
# Points mineurs
# ---------------------------------------------------------------------------

class EcransWifiMineursTests(BaseVues):
    def test_une_tv_sans_cookie_reste_un_seul_ecran(self):
        from centres.models import AppareilEcran

        from django.test import Client

        for _ in range(3):
            Client().get(reverse("ecran_tv"))          # cookie jamais gardé
        self.assertEqual(AppareilEcran.objects.count(), 1)

    def test_tv_d_un_centre_desactive_reste_en_attente(self):
        from centres.models import AppareilEcran

        self.client.get(reverse("ecran_tv"))
        appareil = AppareilEcran.objects.get()
        appareil.centre = self.d.centre
        appareil.save(update_fields=["centre"])
        self.d.centre.actif = False
        self.d.centre.save(update_fields=["actif"])
        self.assertEqual(self.client.get(reverse("ecran_tv_etat")).status_code, 204)

    def test_page_ecran_sans_les_tv_des_autres_centres(self):
        from django.utils import timezone

        from centres.models import AppareilEcran

        AppareilEcran.objects.create(jeton="a", code="1111", nom="TV du centre B", centre=self.b.centre,
                                     derniere_activite=timezone.now())
        AppareilEcran.objects.create(jeton="b", code="2222", nom="TV libre", derniere_activite=timezone.now())
        self.connecter(self.accueil, self.d.centre)
        from unittest import mock

        with mock.patch("core.decouverte.rechercher_ecrans", return_value=[]):
            page = self.client.get(reverse("circuit:ecrans_reseau"))
        self.assertContains(page, "TV libre")
        self.assertNotContains(page, "TV du centre B")

    def test_annonce_d_un_ancien_appel_refusee(self):
        import datetime

        from .base import LUNDI_MATIN, a_heure

        with a_heure(LUNDI_MATIN):
            ticket = self.d.ticket("M")
            appel = services.appeler(self.d.file("ACC"), self.accueil, entree=self.d.entree(ticket))
        with a_heure(LUNDI_MATIN + datetime.timedelta(hours=2)):
            reponse = self.client.get(reverse("circuit:ecran_annonce", args=[appel.pk]))
        self.assertEqual(reponse.status_code, 404)


class OngletBorneCentreActifTests(BaseVues):
    def test_pas_d_onglet_dans_un_centre_ou_il_n_est_qu_agent(self):
        admin = self.d.agent("admin_a", "Administrateur de centre")
        admin.is_staff = True
        admin.save(update_fields=["is_staff"])
        from comptes.models import AffectationUtilisateur
        from django.contrib.auth.models import Group

        AffectationUtilisateur.objects.create(utilisateur=admin, centre=self.b.centre,
                                              groupe=Group.objects.get(name="Accueil"))
        self.connecter(admin, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:guichets")), reverse("circuit:bornes"))
        self.connecter(admin, self.b.centre)
        self.assertNotContains(self.client.get(reverse("circuit:guichets")), reverse("circuit:bornes"))


class MoteurMineursTests(BaseVues):
    def test_heure_de_cloture_minuit_ne_ferme_pas_la_borne(self):
        import datetime

        self.d.centre.heure_cloture_tickets = datetime.time(0, 0)
        self.assertFalse(services.emission_terminee(self.d.centre))

    def test_reponse_refusee_sur_une_demande_cloturee(self):
        directeur = self.d.agent("directeur", "Direction")
        autre = services.emettre_ticket(self.d.centre, "AUTRE", motif=self.d.motif)
        notification = services.transmettre_demande(autre.demande, self.accueil, destinataire=directeur)
        services.repondre_notification(notification, directeur, "TRAITEE")
        with self.assertRaises(services.ErreurCircuit):
            services.repondre_notification(notification, directeur, "EN_COURS")

    def test_cloture_aussi_dans_un_centre_desactive(self):
        import datetime

        from django.core.management import call_command

        from .base import LUNDI_MATIN, a_heure

        with a_heure(LUNDI_MATIN):
            ticket = self.d.ticket("M")
        self.d.centre.actif = False
        self.d.centre.save(update_fields=["actif"])
        with a_heure(LUNDI_MATIN + datetime.timedelta(days=1)):
            call_command("cloturer_tickets", stdout=__import__("io").StringIO())
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, "CLOTURE")


class ComptesEtAuditMineursTests(BaseVues):
    def test_identifiant_existant_a_la_casse_pres(self):
        from django.core.exceptions import ValidationError

        from comptes.models import Utilisateur

        doublon = Utilisateur(username="Accueil1")
        doublon.set_password("Mot-de-passe-2026!")
        with self.assertRaisesMessage(ValidationError, "existe déjà"):
            doublon.full_clean()

    def test_ancien_administrateur_redevenu_agent_sans_administration(self):
        from comptes.models import Utilisateur

        compte = Utilisateur.objects.create_user("ancien_admin", password="x",
                                                 type_compte=Utilisateur.TypeCompte.ADMIN_CENTRE)
        self.assertTrue(compte.is_staff)
        compte = Utilisateur.objects.get(pk=compte.pk)
        compte.type_compte = Utilisateur.TypeCompte.AGENT
        compte.save()
        self.assertFalse(compte.is_staff)

    def test_changement_de_salle_n_est_pas_un_changement_de_role(self):
        from audit.models import JournalAudit
        from comptes.permissions import affectation_active

        affectation = affectation_active(self.medecin, self.d.centre)
        avant = JournalAudit.objects.filter(action=JournalAudit.Action.ROLE).count()
        affectation.salle = "3"
        affectation.save(update_fields=["salle"])
        self.assertEqual(JournalAudit.objects.filter(action=JournalAudit.Action.ROLE).count(), avant)

    def test_retrait_d_un_service_autorise_et_modification_d_un_service_journalises(self):
        from audit.models import JournalAudit
        from comptes.models import AffectationService

        AffectationService.objects.filter(affectation__utilisateur=self.medecin).first().delete()
        self.assertTrue(JournalAudit.objects.filter(details__has_key="service_retire").exists())
        service = self.d.svc["DEN"]
        service.nom = "Dentisterie"
        service.save()
        self.assertTrue(JournalAudit.objects.filter(
            action=JournalAudit.Action.MODIFICATION, objet_type="centres.service", objet_id=str(service.pk),
        ).exists())


class DecouverteSansRedirectionTests(BaseVues):
    def test_une_redirection_n_est_jamais_suivie(self):
        import urllib.request

        from core.decouverte import _OUVREUR, _SansRedirection

        self.assertTrue(any(isinstance(h, _SansRedirection) for h in _OUVREUR.handlers))
        requete = urllib.request.Request("http://192.168.3.226/fiche.xml")
        self.assertIsNone(_SansRedirection().redirect_request(requete, None, 302, "Found", {}, "http://127.0.0.1/"))


class BorneDansUnNouvelOngletTests(BaseVues):
    """Décision du 23/11/2026 : la borne s'ouvre dans un autre onglet du navigateur."""

    def test_activation_et_ouverture_dans_un_nouvel_onglet(self):
        admin = self.d.agent("admin_centre", "Administrateur de centre")
        admin.is_staff = True
        admin.save(update_fields=["is_staff"])
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Tablette")
        self.connecter(admin, self.d.centre)
        page = self.client.get(reverse("circuit:bornes")).content.decode()
        formulaire = page[page.rindex("<form", 0, page.index("Activer sur cet appareil</button>")):]
        self.assertIn('target="_blank"', formulaire[:formulaire.index(">")])
        activer_borne(self.client, borne)
        page = self.client.get(reverse("circuit:bornes"))
        self.assertContains(page, 'target="_blank" rel="noopener" hx-boost="false" style="flex:1 1 100%">')
        self.assertContains(page, "Ouvrir la borne</a>")


class PharmacieActionPrincipaleTests(BaseVues):
    """Décision du 23/11/2026 : en fin de parcours, la pharmacie est l'action principale."""

    def _medecin_avec_patient(self):
        infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", infirmier)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("MED"), self.medecin, entree=entree)
        services.prendre_en_charge(entree, self.medecin)
        return infirmier

    def test_derniere_etape_pharmacie_en_principal(self):
        self._medecin_avec_patient()
        self.connecter(self.medecin, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["MED"].pk])).content.decode()
        principal = page.index('class="principal grand large"')
        self.assertIn("Terminer et transférer à la pharmacie", page[principal:principal + 300])
        self.assertIn("Terminer sans pharmacie (fin du parcours)", page)
        self.assertNotIn("<summary>", page[page.index("Terminer sans pharmacie"):page.index("Terminer sans pharmacie") + 2000])

    def test_etape_intermediaire_inchangee(self):
        infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("INF"), infirmier, entree=entree)
        services.prendre_en_charge(entree, infirmier)
        self.connecter(infirmier, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["INF"].pk]))
        self.assertContains(page, "Terminer et orienter vers Médecin")
        self.assertContains(page, "<summary>")                      # pharmacie : option repliée
        self.assertNotContains(page, "Terminer sans pharmacie")


class MessagesDeLaPlateformeTests(BaseVues):
    """
    Vérification du 23/11/2026 : chaque message saisi (motif de transfert,
    envoi au médecin en chef, orientation et réponse « Autre », annulation,
    dérogation, libération) est enregistré ET affiché là où il doit être lu.
    """

    def setUp(self):
        super().setUp()
        self.infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])

    def _au_service(self, code, agent, ticket=None):
        ticket = ticket or self.d.ticket("M")
        entree = self.d.entree(ticket)
        services.appeler(self.d.file(code), agent, entree=entree)
        services.prendre_en_charge(entree, agent)
        return ticket, entree

    def test_motif_du_medecin_lu_par_la_pharmacie(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", self.infirmier)
        _, entree = self._au_service("MED", self.medecin, ticket)
        self.connecter(self.medecin, self.d.centre)
        self.client.post(reverse("circuit:guichet_action", args=[self.d.svc["MED"].pk, "terminer"]),
                         {"entree": entree.pk, "file": entree.file_id, "destination": "pharmacie",
                          "motif": "Amoxicilline 500 mg"})
        self.connecter(self.pharma, self.d.centre)
        pha = self.d.svc["PHA"].pk
        self.assertContains(self.client.get(reverse("circuit:guichet", args=[pha])), "Amoxicilline 500 mg")   # dans la file
        services.prendre_en_charge(self.d.entree(ticket), self.pharma, oral=True)
        page = self.client.get(reverse("circuit:guichet", args=[pha]))
        self.assertContains(page, "Amoxicilline 500 mg")                                                   # patient en cours
        self.assertContains(self.client.get(reverse("circuit:ticket", args=[ticket.pk])), "Amoxicilline 500 mg")

    def test_motif_obligatoire_de_l_infirmerie_vers_la_pharmacie(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        _, entree = self._au_service("INF", self.infirmier, ticket)
        self.connecter(self.infirmier, self.d.centre)
        url = reverse("circuit:guichet_action", args=[self.d.svc["INF"].pk, "terminer"])
        reponse = self.client.post(url, {"entree": entree.pk, "file": entree.file_id, "pharmacie": "1", "motif": ""},
                                   follow=True)
        self.assertContains(reponse, "indiquez un motif")
        self.client.post(url, {"entree": entree.pk, "file": entree.file_id, "pharmacie": "1", "motif": "Pansement"})
        self.connecter(self.pharma, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:guichet", args=[self.d.svc["PHA"].pk])), "Pansement")

    def test_message_au_medecin_en_chef_affiche_une_seule_fois(self):
        chef = self.d.agent("chef1", "Médecin en chef")
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", self.infirmier)
        _, entree = self._au_service("MED", self.medecin, ticket)
        services.terminer(entree, self.medecin, envoyer_medecin_chef=True, motif="Avis cardiologique")
        self.connecter(chef, self.d.centre)
        page = self.client.get(reverse("circuit:notifications")).content.decode()
        self.assertEqual(page.count("Avis cardiologique"), 1)
        self.assertIn("medecin", page.lower())

    def test_orientation_et_reponse_autre(self):
        from circuit.models import Ticket

        directeur = self.d.agent("directeur", "Direction")
        autre = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif, precision="Pour M. Rabe")
        self.connecter(self.accueil, self.d.centre)
        self.client.post(reverse("circuit:demandes"), {"demande": autre.demande.pk, "destinataire": directeur.pk,
                                                         "message": "Il attend devant l'accueil"})
        self.connecter(directeur, self.d.centre)
        page = self.client.get(reverse("circuit:notifications"))
        self.assertContains(page, "Il attend devant l&#x27;accueil")
        self.assertContains(page, "Pour M. Rabe")
        notification = autre.demande.notifications.get()
        self.client.post(reverse("circuit:notifications"), {"notification": notification.pk, "statut": "EN_COURS",
                                                              "message": "Je le reçois dans 10 min"})
        self.assertContains(self.client.get(reverse("circuit:notifications")), "Je le reçois dans 10 min")
        self.connecter(self.accueil, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:demandes")), "Je le reçois dans 10 min")

    def test_motifs_d_annulation_derogation_et_liberation_dans_le_ticket(self):
        responsable = self.d.agent("resp", "Responsable")
        annule = self.d.ticket("M")
        services.annuler_ticket(annule, self.accueil, "Patient reparti")
        self.connecter(self.accueil, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:ticket", args=[annule.pk])), "Patient reparti")
        dentiste = self.d.ticket("D")
        services.autoriser_derogation(dentiste, responsable, "Urgence dentaire")
        self.assertContains(self.client.get(reverse("circuit:ticket", args=[dentiste.pk])), "Urgence dentaire")


class ReponseDuMedecinEnChefTests(BaseVues):
    """Décision du 23/11/2026 : motif du médecin bien visible chez le chef ; réponse du chef chez le médecin."""

    def test_aller_retour_medecin_et_medecin_en_chef(self):
        chef = self.d.agent("chef1", "Médecin en chef")
        infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", infirmier)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("MED"), self.medecin, entree=entree)
        services.prendre_en_charge(entree, self.medecin)
        services.terminer(entree, self.medecin, envoyer_medecin_chef=True, motif="Il veut tester")
        # Chez le chef : « Motif du médecin », et pas l'appel du médecin comme « dernier appel ».
        self.connecter(chef, self.d.centre)
        page = self.client.get(reverse("circuit:notifications"))
        self.assertContains(page, "Motif du médecin : <strong>Il veut tester</strong>", html=False)
        self.assertNotContains(page, "Dernier appel")
        notification = ticket.demande.notifications.get()
        reponse = self.client.post(reverse("circuit:notifications"), {
            "notification": notification.pk, "statut": "PATIENT_ATTENDU", "message": "Envoyez-le maintenant"}, follow=True)
        self.assertContains(reponse, f"Réponse envoyée à {self.medecin}")
        # Chez le médecin : pastille, puis la réponse dans ses Notifications.
        self.connecter(self.medecin, self.d.centre)
        self.assertEqual(self.client.get(reverse("circuit:badge_notifications")).content,
                         b'<span class="pastille">1</span>')
        page = self.client.get(reverse("circuit:notifications"))
        self.assertContains(page, "Patients envoyés au médecin en chef")
        self.assertContains(page, "Il veut tester")
        self.assertContains(page, "Envoyez-le maintenant")
        self.assertContains(page, "Patient attendu")
        self.assertEqual(self.client.get(reverse("circuit:badge_notifications")).content, b"")


class ServicesDeLaBorneParLAdministrateurTests(BaseVues):
    """Décision du 24/11/2026 : l'administrateur du centre choisit les services affichés sur la borne."""

    def setUp(self):
        super().setUp()
        self.admin = self.d.agent("admin_centre", "Administrateur de centre")
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])
        self.borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Tablette")
        self.url = reverse("circuit:borne_services", args=[self.borne.pk])

    def test_choix_des_services_puis_retour_a_tous(self):
        self.connecter(self.admin, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:bornes")), "Choisir les services affichés")
        dentiste = self.d.parcours["D"]
        self.client.post(self.url, {"parcours": [dentiste.pk]})
        self.assertEqual(list(self.borne.parcours_proposes()), [dentiste])
        tous = [p.pk for p in self.borne.parcours_possibles()]
        self.client.post(self.url, {"parcours": tous})
        self.assertFalse(self.borne.parcours.exists())          # tout coché : tous les services
        self.assertEqual(self.borne.parcours_proposes().count(), len(tous))

    def test_aucun_service_coche_refuse(self):
        self.connecter(self.admin, self.d.centre)
        self.client.post(self.url, {"parcours": [self.d.parcours["D"].pk]})
        self.client.post(self.url, {})
        self.assertEqual(list(self.borne.parcours.all()), [self.d.parcours["D"]])

    def test_service_d_un_autre_centre_ignore(self):
        self.connecter(self.admin, self.d.centre)
        self.client.post(self.url, {"parcours": [self.b.parcours["D"].pk, self.d.parcours["M"].pk]})
        self.assertEqual(list(self.borne.parcours.all()), [self.d.parcours["M"]])

    def test_un_agent_ne_peut_pas_modifier(self):
        self.connecter(self.accueil, self.d.centre)
        self.assertEqual(self.client.post(self.url, {"parcours": [self.d.parcours["D"].pk]}).status_code, 403)
        self.assertFalse(self.borne.parcours.exists())


class OuvrirLaBorneDeCetAppareilTests(BaseVues):
    """24/11/2026 : sur l'appareil déjà borne, « Ouvrir la borne » ouvre un nouvel onglet, sans déconnexion."""

    def test_ouvrir_la_borne_sans_etre_deconnecte(self):
        admin = self.d.agent("admin_centre", "Administrateur de centre")
        admin.is_staff = True
        admin.save(update_fields=["is_staff"])
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Tablette")
        activer_borne(self.client, borne)
        self.connecter(admin, self.d.centre)
        page = self.client.get(reverse("circuit:bornes"))
        self.assertContains(page, "Cet appareil est la borne")
        html = page.content.decode()
        lien = html[html.rindex("<a", 0, html.index("Ouvrir la borne</a>")):]
        self.assertIn('target="_blank"', lien[:lien.index(">")])
        self.assertIn(reverse("circuit:borne"), lien[:lien.index(">")])
        self.assertTemplateUsed(self.client.get(reverse("circuit:borne")), "circuit/borne.html")
        self.assertEqual(self.client.get(reverse("circuit:bornes")).status_code, 200)   # toujours connecté


class ErgonomieDu24_11Tests(BaseVues):
    """Revue d'ergonomie du 24/11/2026."""

    def test_onglet_demandes_pour_l_accueil_seulement(self):
        self.connecter(self.accueil, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:guichets")), reverse("circuit:demandes"))
        self.connecter(self.medecin, self.d.centre)
        self.assertNotContains(self.client.get(reverse("circuit:guichets")), reverse("circuit:demandes"))

    def test_borne_message_simple_pour_le_patient(self):
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Tablette")
        activer_borne(self.client, borne)
        reponse = self.client.post(reverse("circuit:borne_emettre"), {"mode": "INCONNU"}, follow=True)
        self.assertContains(reponse, "pas disponible pour le moment. Merci de vous présenter à l")
        self.assertNotContains(reponse, "Mode inconnu")

    def test_appel_depuis_les_notifications_sans_salle_hors_medecins(self):
        # Depuis le 27/11/2026, seule la salle d'un médecin est annoncée.
        from circuit.models import Appel, MotifDemande, Ticket
        from comptes.permissions import affectation_active

        directeur = self.d.agent("directeur", "Direction")
        affectation = affectation_active(directeur, self.d.centre)
        affectation.salle = "Bureau 3"
        affectation.save(update_fields=["salle"])
        motif = MotifDemande.objects.filter(centre=self.d.centre).first()
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=motif)
        notification = services.transmettre_demande(ticket.demande, self.accueil, destinataire=directeur)
        self.connecter(directeur, self.d.centre, salle_indiquee=False)
        self.assertNotContains(self.client.get(reverse("circuit:notifications")), 'name="poste"')
        self.client.post(reverse("circuit:notification_appeler", args=[notification.pk]), {"poste": "Bureau 3"})
        self.assertEqual(Appel.objects.get(ticket=ticket, agent=directeur).poste, "")
