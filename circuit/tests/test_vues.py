import datetime
import re

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import JournalAudit
from centres.access import SESSION_CENTRE_KEY
from centres.models import Borne, EcranAffichage
from circuit import services
from circuit.models import Appel, EntreeFile, Ticket
from circuit.services import ErreurCircuit
from comptes.models import AffectationUtilisateur, Utilisateur

from .base import LUNDI_MATIN, Donnees, a_heure


class BaseVues(TestCase):
    def setUp(self):
        self.d = Donnees().construire("TAM")
        self.b = Donnees().construire("ANT")
        self.accueil = self.d.agent("accueil1", "Accueil", ["ACC"])
        self.medecin = self.d.agent("med1", "Médecin", ["MED"])
        self.pharma = self.d.agent("pha1", "Pharmacie", ["PHA"])
        self.agent_b = self.b.agent("accueil_b", "Accueil", ["ACC"])

    def connecter(self, utilisateur, centre):
        self.client.force_login(utilisateur)
        session = self.client.session
        session[SESSION_CENTRE_KEY] = centre.pk
        session.save()


class IsolationCentresTests(BaseVues):
    def test_agent_du_centre_b_ne_voit_pas_les_donnees_du_centre_a(self):
        ticket = self.d.ticket("M")
        self.connecter(self.agent_b, self.b.centre)
        self.assertEqual(self.client.get(reverse("circuit:ticket", args=[ticket.pk])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk])).status_code, 404
        )
        reponse = self.client.post(
            reverse("circuit:ticket_action", args=[ticket.pk, "annuler"]), {"motif": "x"}
        )
        self.assertEqual(reponse.status_code, 404)
        ticket.refresh_from_db()
        self.assertTrue(ticket.ouvert)

    def test_centre_force_en_session_non_autorise_est_refuse(self):
        self.connecter(self.agent_b, self.d.centre)  # centre A forcé dans la session
        reponse = self.client.get(reverse("circuit:guichets"))
        self.assertRedirects(reponse, reverse("centres:selection"), fetch_redirect_response=False)

    def test_entree_d_un_autre_centre_refusee_dans_une_action(self):
        ticket_a = self.d.ticket("M")
        self.b.ticket("M")
        entree_a = self.d.entree(ticket_a)
        self.connecter(self.agent_b, self.b.centre)
        self.client.post(reverse("circuit:guichet_action", args=[self.b.svc["ACC"].pk, "appeler"]),
                         {"entree": entree_a.pk})
        entree_a.refresh_from_db()
        self.assertEqual(entree_a.statut, EntreeFile.Statut.EN_ATTENTE)

    def test_agent_multi_centres_choisit_son_centre_actif(self):
        AffectationUtilisateur.objects.create(utilisateur=self.accueil, centre=self.b.centre,
                                              groupe=self.accueil.affectations.first().groupe)
        self.client.force_login(self.accueil)
        self.client.post(reverse("centres:selection"), {"centre_id": self.b.centre.pk})
        reponse = self.client.get(reverse("circuit:guichets"))
        self.assertContains(reponse, self.b.centre.nom)
        self.assertEqual(reponse.context["centre_actif"], self.b.centre)

    def test_statistiques_limitees_au_centre_actif(self):
        directeur = self.d.agent("dir", "Direction")
        self.d.ticket("M")
        self.b.ticket("M")
        self.b.ticket("M")
        self.connecter(directeur, self.d.centre)
        reponse = self.client.get(reverse("statistiques:tableau"), {"reseau": "1"})
        self.assertEqual(reponse.context["stats"]["total_tickets"], 1)


class PermissionsTests(BaseVues):
    def test_service_hors_portee_interdit(self):
        self.connecter(self.medecin, self.d.centre)
        reponse = self.client.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk]))
        self.assertEqual(reponse.status_code, 403)

    def test_pharmacie_ne_peut_pas_transferer(self):
        self.connecter(self.pharma, self.d.centre)
        reponse = self.client.post(
            reverse("circuit:guichet_action", args=[self.d.svc["PHA"].pk, "terminer"]),
            {"pharmacie": "1", "entree": 0},
        )
        self.assertEqual(reponse.status_code, 403)

    def test_direction_sans_acces_aux_services_et_medecin_sans_statistiques(self):
        self.connecter(self.medecin, self.d.centre)
        self.assertEqual(self.client.get(reverse("statistiques:tableau")).status_code, 403)

    def test_non_connecte_redirige_vers_connexion(self):
        reponse = self.client.get(reverse("circuit:guichets"))
        self.assertEqual(reponse.status_code, 302)
        self.assertIn(reverse("connexion"), reponse["Location"])

    def test_compte_desactive_ne_peut_plus_se_connecter(self):
        self.accueil.is_active = False
        self.accueil.save()
        self.assertFalse(self.client.login(username="accueil1", password="mot-de-passe-1"))

    def test_identifiant_insensible_a_la_casse(self):
        self.assertTrue(self.client.login(username="ACCUEIL1", password="mot-de-passe-1"))

    def test_aucun_role_securite(self):
        from django.contrib.auth.models import Group

        self.assertFalse(Group.objects.filter(name__icontains="sécurité").exists())


class InterfaceServiceTests(BaseVues):
    def test_parcours_complet_par_les_interfaces(self):
        ticket = self.d.ticket("M")
        infirmier = self.d.agent("inf", "Infirmerie", ["INF"])
        self.connecter(self.accueil, self.d.centre)
        acc = self.d.svc["ACC"].pk

        reponse = self.client.get(reverse("circuit:guichet", args=[acc]))
        self.assertContains(reponse, "M001")
        self.client.post(reverse("circuit:guichet_action", args=[acc, "appeler"]), {"poste": "Guichet 1"})
        entree = self.d.entree(ticket)
        self.assertEqual(entree.agent, self.accueil)
        self.assertEqual(Appel.objects.get().poste, "Guichet 1")
        self.client.post(reverse("circuit:guichet_action", args=[acc, "prendre"]), {"entree": entree.pk})

        # Aucune identification : l'accueil valide le ticket et l'oriente.
        page = self.client.get(reverse("circuit:guichet", args=[acc]))
        self.assertContains(page, "Valider le ticket et orienter vers Infirmerie")
        self.assertNotContains(page, "Identifier le patient")
        reponse = self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]),
                                   {"entree": entree.pk}, HTTP_HX_REQUEST="true")
        self.assertContains(reponse, "Infirmerie")
        ticket.refresh_from_db()
        self.assertIsNone(ticket.patient)

        self.connecter(infirmier, self.d.centre)
        inf = self.d.svc["INF"].pk
        self.client.post(reverse("circuit:guichet_action", args=[inf, "appeler"]))
        entree = self.d.entree(ticket)
        self.client.post(reverse("circuit:guichet_action", args=[inf, "prendre"]), {"entree": entree.pk})
        self.client.post(reverse("circuit:guichet_action", args=[inf, "terminer"]), {"entree": entree.pk})

        self.connecter(self.medecin, self.d.centre)
        med = self.d.svc["MED"].pk
        self.client.post(reverse("circuit:guichet_action", args=[med, "appeler"]))
        entree = self.d.entree(ticket)
        self.client.post(reverse("circuit:guichet_action", args=[med, "prendre"]), {"entree": entree.pk})
        self.client.post(reverse("circuit:guichet_action", args=[med, "terminer"]),
                         {"entree": entree.pk, "pharmacie": "1"})

        self.connecter(self.pharma, self.d.centre)
        pha = self.d.svc["PHA"].pk
        entree = self.d.entree(ticket)
        self.client.post(reverse("circuit:guichet_action", args=[pha, "prendre"]),
                         {"entree": entree.pk, "oral": "1"})
        self.client.post(reverse("circuit:guichet_action", args=[pha, "terminer"]), {"entree": entree.pk})

        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        self.assertEqual(
            list(ticket.passages.values_list("service__code", flat=True)), ["ACC", "INF", "MED", "PHA"]
        )
        detail = self.client.get(reverse("circuit:ticket", args=[ticket.pk]))
        self.assertContains(detail, "Transfert")

    def test_erreur_metier_affichee_sans_planter(self):
        self.connecter(self.accueil, self.d.centre)
        reponse = self.client.post(
            reverse("circuit:guichet_action", args=[self.d.svc["ACC"].pk, "appeler"]), follow=True
        )
        self.assertContains(reponse, "Aucun patient en attente")

    def test_quota_atteint_signale_a_l_accueil(self):
        acc = self.d.svc["ACC"].pk
        with a_heure(LUNDI_MATIN):
            quota = self.d.parcours["D"].quotas.get(jour_semaine=0, periode="MATIN")
            quota.nombre_places = 0
            quota.save()
            ticket = self.d.ticket("D")
            entree = self.d.entree(ticket)
            services.appeler(self.d.file("ACC"), self.accueil)
            services.prendre_en_charge(entree, self.accueil)
            self.connecter(self.accueil, self.d.centre)
            page = self.client.get(reverse("circuit:guichet", args=[acc]))
            self.assertContains(page, "Quota atteint")
            self.assertContains(page, "Seul un responsable peut accorder une dérogation")
            # L'accueil ne peut ni valider, ni forcer une dérogation sans le droit.
            reponse = self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]),
                                       {"entree": entree.pk}, follow=True)
            self.assertContains(reponse, "Quota atteint")
            refus = self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]),
                                     {"entree": entree.pk, "derogation": "1", "motif_derogation": "x"})
            self.assertEqual(refus.status_code, 403)
        entree.refresh_from_db()
        self.assertEqual(entree.statut, "EN_COURS")
        self.assertIsNone(ticket.reservation_active())

    def test_demandes_et_notifications(self):
        self.d.svc["MED"].destinataire_demandes = self.medecin
        self.d.svc["MED"].save()
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        self.connecter(self.accueil, self.d.centre)
        reponse = self.client.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk]))
        self.assertContains(reponse, "demande(s)")
        self.client.post(reverse("circuit:demandes"),
                         {"demande": ticket.demande.pk, "service": self.d.svc["MED"].pk, "message": "Merci"})
        self.connecter(self.medecin, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:badge_notifications")), "pastille")
        notification = self.medecin.notifications_recues.get()
        self.client.post(reverse("circuit:notifications"),
                         {"notification": notification.pk, "statut": "PATIENT_ATTENDU", "message": "Bureau 3"})
        self.assertEqual(notification.reponses.get().message, "Bureau 3")


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0)
class BorneEcranTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.borne = Borne(centre=self.d.centre, code="B1", libelle="Borne entrée")
        self.cle = self.borne.generer_cle()
        self.borne.save()
        self.ecran_poste = EcranAffichage(centre=self.d.centre, code="TV1", libelle="Salle d'attente")
        self.cle_ecran = self.ecran_poste.generer_cle()
        self.ecran_poste.save()

    def test_borne_en_libre_service_sans_compte(self):
        url = reverse("circuit:borne")
        self.assertContains(self.client.get(url), "Activer la borne")
        self.client.post(url, {"centre": "tam", "code": "B1", "cle": "mauvaise"})
        self.assertNotIn("borne_id", self.client.session)
        self.client.post(url, {"centre": "tam", "code": "B1", "cle": self.cle})
        self.assertContains(self.client.get(url), "Médecin")
        reponse = self.client.post(reverse("circuit:borne_emettre"),
                                   {"mode": "INTERNE", "parcours": self.d.parcours["E"].pk})
        ticket = Ticket.objects.get()
        self.assertRedirects(reponse, reverse("circuit:borne_ticket", args=[ticket.pk]))
        self.assertEqual((ticket.numero, ticket.mode, ticket.borne), ("E001", "INTERNE", self.borne))
        self.assertContains(self.client.get(reverse("circuit:borne_ticket", args=[ticket.pk])), "E001")
        self.client.post(reverse("circuit:borne_emettre"), {"mode": "AUTRE", "motif": self.d.motif.pk})
        self.assertEqual(Ticket.objects.get(mode="AUTRE").demande.motif, self.d.motif)

    def test_borne_refuse_un_parcours_d_un_autre_centre(self):
        self.client.post(reverse("circuit:borne"), {"centre": "TAM", "code": "B1", "cle": self.cle})
        self.client.post(reverse("circuit:borne_emettre"),
                         {"mode": "INTERNE", "parcours": self.b.parcours["M"].pk})
        self.assertFalse(Ticket.objects.exists())

    def test_borne_hors_reseau_local_refusee(self):
        reponse = self.client.get(reverse("circuit:borne"), REMOTE_ADDR="8.8.8.8")
        self.assertEqual(reponse.status_code, 403)

    def test_ecran_affiche_les_appels_de_plusieurs_services(self):
        infirmier = self.d.agent("inf", "Infirmerie", ["INF"])
        t1 = self.d.ticket("I")
        t2 = self.d.ticket("M")
        services.appeler(self.d.file("ACC"), self.accueil, poste="Guichet 1")
        services.prendre_en_charge(self.d.entree(t1), self.accueil)
        services.terminer(self.d.entree(t1), self.accueil)
        services.appeler(self.d.file("INF"), infirmier, poste="Salle 2")
        accueil2 = self.d.agent("acc2", "Accueil", ["ACC"])
        services.appeler(self.d.file("ACC"), accueil2)
        self.client.post(reverse("circuit:ecran"), {"centre": "TAM", "code": "TV1", "cle": self.cle_ecran})
        reponse = self.client.get(reverse("circuit:ecran_appels"))
        self.assertContains(reponse, "Infirmerie")
        self.assertContains(reponse, "Accueil")
        self.assertContains(reponse, "I001")
        self.assertContains(reponse, "M001")

    def test_ecran_sans_cle_ni_compte_refuse(self):
        self.assertEqual(self.client.get(reverse("circuit:ecran_appels")).status_code, 403)

    def test_ecran_n_affiche_pas_les_appels_oraux(self):
        ticket = self.d.ticket("I")
        services.appeler(self.d.file("ACC"), self.accueil)
        services.prendre_en_charge(self.d.entree(ticket), self.accueil)
        services.terminer(self.d.entree(ticket), self.accueil)
        infirmier = self.d.agent("inf", "Infirmerie", ["INF"])
        services.appeler(self.d.file("INF"), infirmier)
        services.prendre_en_charge(self.d.entree(ticket), infirmier)
        services.terminer(self.d.entree(ticket), infirmier, transferer_pharmacie=True)
        services.prendre_en_charge(self.d.entree(ticket), self.pharma, oral=True)
        self.client.post(reverse("circuit:ecran"), {"centre": "TAM", "code": "TV1", "cle": self.cle_ecran})
        reponse = self.client.get(reverse("circuit:ecran_appels"))
        self.assertNotContains(reponse, "Pharmacie")


class StatistiquesEtAuditTests(BaseVues):
    def test_exports_pdf_et_excel(self):
        directeur = self.d.agent("dir", "Direction")
        ticket = self.d.ticket("I")
        self.d.accueillir(ticket, self.accueil)
        self.connecter(directeur, self.d.centre)
        reponse = self.client.get(reverse("statistiques:tableau"))
        self.assertEqual(reponse.context["stats"]["total_tickets"], 1)
        self.assertNotContains(reponse, "RAKOTO")  # aucune donnée nominative
        pdf = self.client.get(reverse("statistiques:export_pdf"))
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))
        xlsx = self.client.get(reverse("statistiques:export_excel"))
        self.assertTrue(xlsx.content.startswith(b"PK"))
        self.assertEqual(JournalAudit.objects.filter(action="EXPORT").count(), 2)

    def test_connexion_et_desactivation_journalisees(self):
        self.client.login(username="accueil1", password="mot-de-passe-1")
        self.client.login(username="accueil1", password="faux")
        self.accueil.is_active = False
        self.accueil.save()
        actions = set(JournalAudit.objects.values_list("action", flat=True))
        self.assertTrue({"CONNEXION", "ECHEC_CONNEXION", "DESACTIVATION"} <= actions)

    def test_journal_audit_en_ajout_seulement(self):
        from django.db import DatabaseError, transaction

        self.client.login(username="accueil1", password="mot-de-passe-1")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                JournalAudit.objects.all().delete()

    def test_modification_de_quota_journalisee(self):
        from parcours.quotas import enregistrer_quota

        quota = self.d.parcours["D"].quotas.first()
        quota.nombre_places = 9
        enregistrer_quota(quota, self.accueil)
        entree = JournalAudit.objects.filter(action="QUOTA").latest("date")
        self.assertEqual(entree.details["apres"]["nombre_places"], 9)


class IsolationCompleteTests(BaseVues):
    """Un agent du centre B ne lit ni ne modifie rien du centre A."""

    def test_demandes_et_notifications_d_un_autre_centre(self):
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        notification = services.transmettre_demande(ticket.demande, self.accueil, destinataire=self.medecin)
        self.connecter(self.agent_b, self.b.centre)
        self.assertNotContains(self.client.get(reverse("circuit:demandes")), ticket.numero)
        reponse = self.client.post(reverse("circuit:demandes"),
                                   {"demande": ticket.demande.pk, "destinataire": self.agent_b.pk})
        self.assertEqual(reponse.status_code, 404)
        reponse = self.client.post(reverse("circuit:notifications"),
                                   {"notification": notification.pk, "statut": "TRAITEE"})
        self.assertEqual(reponse.status_code, 404)
        self.assertFalse(notification.reponses.exists())

    def test_ecran_et_tickets_du_jour_par_centre(self):
        self.d.ticket("M")
        services.appeler(self.d.file("ACC"), self.accueil)
        self.connecter(self.agent_b, self.b.centre)
        self.assertNotContains(self.client.get(reverse("circuit:ecran_appels")), "M001")
        self.assertNotContains(self.client.get(reverse("circuit:tickets")), "M001")

    def test_exports_limites_au_centre_et_reseau_reserve(self):
        directeur_b = self.b.agent("dir_b", "Direction")
        self.d.ticket("M")
        self.connecter(directeur_b, self.b.centre)
        reponse = self.client.get(reverse("statistiques:tableau"), {"reseau": "1"})
        self.assertEqual(reponse.context["stats"]["total_tickets"], 0)
        self.assertFalse(reponse.context["peut_reseau"])

    def test_administrateur_central_a_la_vue_transversale(self):
        admin = Utilisateur.objects.create_user("admin_central", password="x", type_compte="ADMIN")
        self.d.ticket("M")
        self.b.ticket("M")
        self.b.ticket("I")
        self.connecter(admin, self.d.centre)
        self.assertEqual(self.client.get(reverse("statistiques:tableau")).context["stats"]["total_tickets"], 1)
        reponse = self.client.get(reverse("statistiques:tableau"), {"reseau": "1"})
        self.assertEqual(reponse.context["stats"]["total_tickets"], 3)
        ticket_b = Ticket.objects.filter(centre=self.b.centre).first()
        # Données opérationnelles : consultées dans le centre actif choisi.
        self.connecter(admin, self.b.centre)
        self.assertEqual(self.client.get(reverse("circuit:ticket", args=[ticket_b.pk])).status_code, 200)

    def test_admin_django_filtre_les_tickets_par_centre(self):
        from django.contrib.auth.models import Permission

        responsable = self.d.agent("resp_staff", "Responsable")
        responsable.is_staff = True
        responsable.save()
        responsable.user_permissions.add(Permission.objects.get(codename="view_ticket"))
        self.d.ticket("D")
        self.b.ticket("E")
        self.client.force_login(responsable)
        reponse = self.client.get(reverse("admin:circuit_ticket_changelist"))
        self.assertContains(reponse, "D001")
        self.assertNotContains(reponse, "E001")


class ModeAutreInterfaceTests(BaseVues):
    def test_cloture_du_ticket_seulement_apres_orientation(self):
        directeur = self.d.agent("dir", "Direction")
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        self.connecter(self.accueil, self.d.centre)
        acc = self.d.svc["ACC"].pk
        self.client.post(reverse("circuit:guichet_action", args=[acc, "appeler"]))
        entree = self.d.entree(ticket)
        self.client.post(reverse("circuit:guichet_action", args=[acc, "prendre"]), {"entree": entree.pk})
        page = self.client.get(reverse("circuit:guichet", args=[acc]))
        self.assertContains(page, "Orienter la demande")
        reponse = self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]),
                                   {"entree": entree.pk}, follow=True)
        self.assertContains(reponse, "Orientez d")
        self.client.post(reverse("circuit:demandes"),
                         {"demande": ticket.demande.pk, "destinataire": directeur.pk, "message": "RDV DRH"})
        self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]), {"entree": entree.pk})
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        # Le destinataire répond ensuite, ticket déjà clôturé.
        self.connecter(directeur, self.d.centre)
        notification = directeur.notifications_recues.get()
        self.client.post(reverse("circuit:notifications"),
                         {"notification": notification.pk, "statut": "TRAITEE", "message": "Reçu à 10 h"})
        ticket.demande.refresh_from_db()
        self.assertEqual(ticket.demande.statut, "CLOTUREE")
        self.connecter(self.accueil, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:demandes")), "Reçu à 10 h")


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True)
class SessionPosteTests(BaseVues):
    def test_borne_reste_activee_au_dela_de_la_duree_des_sessions_agents(self):
        from django.conf import settings

        borne = Borne(centre=self.d.centre, code="B1", libelle="Borne")
        cle = borne.generer_cle()
        borne.save()
        self.client.post(reverse("circuit:borne"), {"centre": "TAM", "code": "B1", "cle": cle})
        self.assertGreater(self.client.session.get_expiry_age(), 300 * 24 * 3600)
        self.assertLessEqual(settings.SESSION_COOKIE_AGE, 24 * 3600)


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0)
class RecetteParcoursInterfacesTests(BaseVues):
    """Scénarios de recette par les interfaces : borne → accueil → service(s) → [pharmacie] → fin."""

    ETAPES = {"M": ["INF", "MED"], "D": ["DEN"], "E": ["ECH"], "I": ["INF"]}
    ROLES = {"INF": "Infirmerie", "MED": "Médecin", "DEN": "Dentiste", "ECH": "Échographie"}

    def setUp(self):
        super().setUp()
        self.agents = {code: self.d.agent(f"agent_{code.lower()}", role, [code])
                       for code, role in self.ROLES.items()}
        self.agents["PHA"] = self.pharma
        borne = Borne(centre=self.d.centre, code="B1", libelle="Borne")
        self.cle = borne.generer_cle()
        borne.save()

    def _emettre(self, prefixe, mode):
        borne_client = self.client_class()
        borne_client.post(reverse("circuit:borne"), {"centre": "TAM", "code": "B1", "cle": self.cle})
        borne_client.post(reverse("circuit:borne_emettre"),
                          {"mode": mode, "parcours": self.d.parcours[prefixe].pk})
        return Ticket.objects.latest("id")

    def _traiter(self, agent, code, ticket, **donnees):
        self.connecter(agent, self.d.centre)
        service = self.d.svc[code].pk
        if code == "PHA":
            entree = self.d.entree(ticket)
            self.client.post(reverse("circuit:guichet_action", args=[service, "prendre"]),
                             {"entree": entree.pk, "oral": "1"})
        else:
            self.client.post(reverse("circuit:guichet_action", args=[service, "appeler"]))
            entree = self.d.entree(ticket)
            self.client.post(reverse("circuit:guichet_action", args=[service, "prendre"]), {"entree": entree.pk})
        self.client.post(reverse("circuit:guichet_action", args=[service, "terminer"]),
                         {"entree": entree.pk, **donnees})

    def test_quatre_parcours_avec_et_sans_pharmacie(self):
        with a_heure(LUNDI_MATIN):
            for prefixe, etapes in self.ETAPES.items():
                for avec_pharmacie, mode in ((False, "INTERNE"), (True, "EXTERNE")):
                    with self.subTest(parcours=prefixe, pharmacie=avec_pharmacie):
                        ticket = self._emettre(prefixe, mode)
                        self.assertEqual(ticket.mode, mode)
                        self._traiter(self.accueil, "ACC", ticket)
                        for rang, code in enumerate(etapes, start=1):
                            dernier = rang == len(etapes)
                            options = {"pharmacie": "1"} if (avec_pharmacie and dernier) else {}
                            self._traiter(self.agents[code], code, ticket, **options)
                        attendus = ["ACC"] + etapes
                        if avec_pharmacie:
                            self._traiter(self.pharma, "PHA", ticket)
                            attendus.append("PHA")
                        ticket.refresh_from_db()
                        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
                        self.assertEqual(list(ticket.passages.values_list("service__code", flat=True)), attendus)
                        self.assertEqual(ticket.transferts.count(), 1 if avec_pharmacie else 0)
                        if avec_pharmacie:
                            self.assertEqual(ticket.transferts.get().decide_par,
                                             self.agents[etapes[-1]])
                        # Chaque passage est horodaté de bout en bout.
                        self.assertFalse(ticket.passages.filter(date_fin__isnull=True).exists())


class EcranPrincipalTests(BaseVues):
    def test_un_seul_appel_par_ticket_et_appels_des_autres_tickets_conserves(self):
        from circuit.views_postes import appels_a_afficher

        infirmier = self.d.agent("inf_e", "Infirmerie", ["INF"])
        t1 = self.d.ticket("I")
        t2 = self.d.ticket("D")
        services.appeler(self.d.file("ACC"), self.accueil, poste="Guichet 1")
        services.prendre_en_charge(self.d.entree(t1), self.accueil)
        services.terminer(self.d.entree(t1), self.accueil)
        services.appeler(self.d.file("INF"), infirmier, poste="Salle 2")
        accueil2 = self.d.agent("acc_e", "Accueil", ["ACC"])
        services.appeler(self.d.file("ACC"), accueil2, poste="Guichet 2")
        affiches = [(a.numero_ticket, a.nom_service) for a in appels_a_afficher(self.d.centre)]
        self.assertEqual(affiches, [("D001", "Accueil"), ("I001", "Infirmerie")])


class CorrectionsAmeliorationsTests(BaseVues):
    @override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=60)
    def test_double_appui_sur_la_borne_revient_a_l_accueil(self):
        borne = Borne(centre=self.d.centre, code="B1", libelle="Borne")
        cle = borne.generer_cle()
        borne.save()
        self.client.post(reverse("circuit:borne"), {"centre": "TAM", "code": "B1", "cle": cle})
        donnees = {"mode": "INTERNE", "parcours": self.d.parcours["M"].pk}
        self.client.post(reverse("circuit:borne_emettre"), donnees)
        reponse = self.client.post(reverse("circuit:borne_emettre"), donnees, follow=True)
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "vient d")
        self.assertContains(reponse, "Vous êtes")  # écran d'accueil de la borne

    def test_messages_remplaces_lors_d_une_action_htmx(self):
        self.d.ticket("M")
        self.connecter(self.accueil, self.d.centre)
        reponse = self.client.post(reverse("circuit:guichet_action", args=[self.d.svc["ACC"].pk, "appeler"]),
                                   HTTP_HX_REQUEST="true")
        self.assertContains(reponse, 'id="messages-page" hx-swap-oob="true"')
        self.assertContains(reponse, "M001 appelé")


@override_settings(CONNEXION_MAX_ECHECS=5, CONNEXION_BLOCAGE_MINUTES=15)
class BlocageConnexionTests(BaseVues):
    def _connexion(self, mot_de_passe, identifiant="accueil1"):
        return self.client.post(reverse("connexion"), {"username": identifiant, "password": mot_de_passe})

    def test_blocage_apres_cinq_echecs_puis_deblocage(self):
        import datetime

        for _ in range(5):
            self._connexion("faux")
        reponse = self._connexion("mot-de-passe-1")
        self.assertContains(reponse, "Trop de tentatives")
        self.assertNotIn("_auth_user_id", self.client.session)
        # Les autres comptes ne sont pas bloqués.
        self.assertEqual(self._connexion("mot-de-passe-1", "med1").status_code, 302)
        self.client.logout()
        # Après la fenêtre de blocage, la connexion fonctionne.
        from django.utils import timezone as tz
        with a_heure(tz.now() + datetime.timedelta(minutes=16)):
            self.assertEqual(self._connexion("mot-de-passe-1").status_code, 302)

    def test_connexion_reussie_remet_le_compteur_a_zero(self):
        for _ in range(4):
            self._connexion("faux")
        self.assertEqual(self._connexion("mot-de-passe-1").status_code, 302)
        self.client.logout()
        for _ in range(4):
            self._connexion("faux")
        self.assertEqual(self._connexion("mot-de-passe-1").status_code, 302)

    def test_identifiant_bloque_quelle_que_soit_la_casse(self):
        for _ in range(5):
            self._connexion("faux", "ACCUEIL1")
        self.assertContains(self._connexion("mot-de-passe-1", "accueil1"), "Trop de tentatives")


class VisibiliteParServiceTests(BaseVues):
    """Chaque agent ne voit que les patients passés par ses services."""

    def setUp(self):
        super().setUp()
        self.infirmier = self.d.agent("inf_v", "Infirmerie", ["INF"])
        self.dentiste = self.d.agent("den_v", "Dentiste", ["DEN"])
        self.echo = self.d.agent("ech_v", "Échographie", ["ECH"])
        self.directeur = self.d.agent("dir_v", "Direction")
        self.responsable = self.d.agent("resp_v", "Responsable")
        with a_heure(LUNDI_MATIN):
            # M arrivé chez les médecins, puis transféré à la pharmacie.
            self.m_pharmacie = self.d.ticket("M")
            self.d.accueillir(self.m_pharmacie, self.accueil)
            self.d.traiter(self.m_pharmacie, "INF", self.infirmier)
            self.d.traiter(self.m_pharmacie, "MED", self.medecin, transferer=True)
            # M encore à l'infirmerie (pas encore chez les médecins).
            self.m_infirmerie = self.d.ticket("M")
            self.d.accueillir(self.m_infirmerie, self.accueil)
            # D chez le dentiste ; E encore à l'accueil.
            self.dent = self.d.ticket("D")
            self.d.accueillir(self.dent, self.accueil)
            self.echo_accueil = self.d.ticket("E")
        self.tous = {self.m_pharmacie, self.m_infirmerie, self.dent, self.echo_accueil}

    def _visibles(self, agent):
        self.connecter(agent, self.d.centre)
        with a_heure(LUNDI_MATIN):
            liste = set(self.client.get(reverse("circuit:tickets")).context["tickets"])
            detail = {t for t in self.tous
                      if self.client.get(reverse("circuit:ticket", args=[t.pk])).status_code == 200}
        self.assertEqual(liste, detail, "liste et accès direct doivent concorder")
        return liste

    def test_medecin_voit_aussi_les_patients_a_venir(self):
        # m_infirmerie est à l'infirmerie : son parcours passe ensuite par les médecins.
        self.assertEqual(self._visibles(self.medecin), {self.m_pharmacie, self.m_infirmerie})

    def test_infirmerie(self):
        self.assertEqual(self._visibles(self.infirmier), {self.m_pharmacie, self.m_infirmerie})

    def test_dentiste_et_echographie(self):
        self.assertEqual(self._visibles(self.dentiste), {self.dent})
        # Le patient E est encore à l'accueil : visible à l'avance par l'échographie.
        self.assertEqual(self._visibles(self.echo), {self.echo_accueil})

    def test_pharmacie_seulement_les_patients_transferes(self):
        self.assertEqual(self._visibles(self.pharma), {self.m_pharmacie})

    def test_accueil_responsable_voient_tout(self):
        self.assertEqual(self._visibles(self.accueil), self.tous)
        self.assertEqual(self._visibles(self.responsable), self.tous)

    def test_direction_ne_voit_aucun_patient(self):
        # Décision du 19/11/2026 : la direction ne voit (et n'appelle) que les tickets « Autre ».
        self.assertEqual(self._visibles(self.directeur), set())
        with a_heure(LUNDI_MATIN):
            autre = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
            liste = set(self.client.get(reverse("circuit:tickets")).context["tickets"])
            self.assertEqual(liste, {autre})
            page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk]))
        self.assertContains(page, autre.numero)
        self.assertNotContains(page, self.echo_accueil.numero)    # patient E à l'accueil : invisible

    def test_section_a_venir_dans_l_interface_des_medecins(self):
        self.connecter(self.medecin, self.d.centre)
        with a_heure(LUNDI_MATIN):
            reponse = self.client.get(reverse("circuit:guichet", args=[self.d.svc["MED"].pk]))
        self.assertContains(reponse, "À venir")
        self.assertContains(reponse, self.m_infirmerie.numero)
        self.assertContains(reponse, "actuellement : Infirmerie")
        # Une fois arrivé chez les médecins, il passe de « à venir » à la file d'attente.
        with a_heure(LUNDI_MATIN):
            self.d.traiter(self.m_infirmerie, "INF", self.infirmier)
            reponse = self.client.get(reverse("circuit:guichet", args=[self.d.svc["MED"].pk]))
        self.assertNotContains(reponse, "actuellement : Infirmerie")
        self.assertContains(reponse, self.m_infirmerie.numero)
        self.assertIn(self.m_infirmerie, self._visibles(self.medecin))

    def test_infirmerie_du_parcours_m_sans_option_pharmacie(self):
        """Décision du 18/11/2026 : le patient M va toujours chez les médecins après l'infirmerie."""
        self.connecter(self.infirmier, self.d.centre)
        inf = self.d.svc["INF"].pk
        with a_heure(LUNDI_MATIN):
            self.client.post(reverse("circuit:guichet_action", args=[inf, "appeler"]))
            entree = self.d.entree(self.m_infirmerie)
            self.client.post(reverse("circuit:guichet_action", args=[inf, "prendre"]), {"entree": entree.pk})
            page = self.client.get(reverse("circuit:guichet", args=[inf]))
        self.assertContains(page, "Terminer et orienter vers Médecins")
        self.assertNotContains(page, "Transférer à la pharmacie")
        self.assertNotIn(self.m_infirmerie, self._visibles(self.pharma))

    def test_ticket_annule_avant_d_arriver_n_est_plus_visible(self):
        from circuit import services

        with a_heure(LUNDI_MATIN):
            services.annuler_ticket(self.echo_accueil, self.accueil, "Patient reparti")
        self.assertEqual(self._visibles(self.echo), set())

    def test_administrateur_central_voit_tout(self):
        admin = Utilisateur.objects.create_user("central_v", password="x", type_compte="ADMIN")
        self.assertEqual(self._visibles(admin), self.tous)

class AucuneInformationPatientTests(BaseVues):
    """Décision du 18/11/2026 : aucune information patient n'est collectée."""

    def test_pages_patients_retirees(self):
        responsable = self.d.agent("resp_x", "Responsable")
        self.connecter(responsable, self.d.centre)
        for url in ("/patients/recherche/", "/patients/nouveau/", "/patients/doublons/",
                    "/circuit/tickets/1/identifier/"):
            self.assertEqual(self.client.get(url).status_code, 404, url)
        menu = self.client.get(reverse("circuit:guichets"))
        self.assertNotContains(menu, "/patients/")
        self.assertNotContains(menu, ">Patients<")

    def test_aucune_mention_patient_dans_les_interfaces(self):
        ticket = self.d.ticket("I")
        self.d.accueillir(ticket, self.accueil)
        self.connecter(self.accueil, self.d.centre)
        for url in (reverse("circuit:tickets"), reverse("circuit:ticket", args=[ticket.pk])):
            reponse = self.client.get(url)
            self.assertNotContains(reponse, "non identifié")
            self.assertNotContains(reponse, "<th>Patient</th>")

    def test_roles_sans_permission_patients(self):
        from django.contrib.auth.models import Group

        for groupe in Group.objects.all():
            self.assertFalse(groupe.permissions.filter(content_type__app_label="patients").exists(),
                             groupe.name)
            self.assertFalse(groupe.permissions.filter(codename="identifier_patient").exists(), groupe.name)

    def test_responsable_peut_deroger_au_quota_a_la_validation(self):
        responsable = self.d.agent("resp_q", "Responsable")
        acc = self.d.svc["ACC"].pk
        with a_heure(LUNDI_MATIN):
            quota = self.d.parcours["D"].quotas.get(jour_semaine=0, periode="MATIN")
            quota.nombre_places = 0
            quota.save()
            ticket = self.d.ticket("D")
            entree = self.d.entree(ticket)
            # Le responsable n'appelle pas le patient (décision du 19/11/2026) :
            # il autorise la dérogation depuis la page du ticket.
            with self.assertRaises(ErreurCircuit):
                services.appeler(self.d.file("ACC"), responsable)
            self.connecter(responsable, self.d.centre)
            self.assertContains(self.client.get(reverse("circuit:ticket", args=[ticket.pk])),
                                "Autoriser une dérogation au quota")
            self.client.post(reverse("circuit:ticket_action", args=[ticket.pk, "deroger"]), {"motif": "Urgence"})
            # L'agent d'accueil voit l'autorisation et valide le ticket.
            services.appeler(self.d.file("ACC"), self.accueil)
            services.prendre_en_charge(entree, self.accueil)
            self.connecter(self.accueil, self.d.centre)
            page = self.client.get(reverse("circuit:guichet", args=[acc]))
            self.assertContains(page, "Dérogation autorisée par resp_q")
            self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]), {"entree": entree.pk})
        reservation = ticket.reservation_active()
        self.assertTrue(reservation.derogation)
        self.assertIn("Urgence", reservation.motif_derogation)
        self.assertIn("resp_q", reservation.motif_derogation)
        self.assertEqual(self.d.entree(ticket).file.service.code, "DEN")

    def test_sans_autorisation_l_accueil_ne_peut_pas_depasser_le_quota(self):
        with a_heure(LUNDI_MATIN):
            quota = self.d.parcours["D"].quotas.get(jour_semaine=0, periode="MATIN")
            quota.nombre_places = 0
            quota.save()
            ticket = self.d.ticket("D")
            entree = self.d.entree(ticket)
            services.appeler(self.d.file("ACC"), self.accueil)
            services.prendre_en_charge(entree, self.accueil)
            with self.assertRaisesMessage(ErreurCircuit, "Quota atteint"):
                services.terminer(entree, self.accueil)
            # Le motif est obligatoire pour autoriser.
            with self.assertRaisesMessage(ErreurCircuit, "motif"):
                services.autoriser_derogation(ticket, self.d.agent("resp_m", "Responsable"), "  ")


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0)
class ScenarioTabletteAccueilServiceTests(BaseVues):
    """
    Scénario demandé par l'utilisateur (18/11/2026) :
    1. le patient choisit son mode puis son service sur la tablette ;
    2. l'accueil voit le ticket tout de suite sur son écran ;
    3. l'accueil l'appelle, le valide et l'oriente vers le service choisi ;
    4. le service concerné le voit et l'appelle.
    """

    def test_scenario_complet(self):
        borne = Borne(centre=self.d.centre, code="TAB", libelle="Tablette")
        cle = borne.generer_cle()
        borne.save()
        echographe = self.d.agent("echo_s", "Échographie", ["ECH"])
        acc, ech = self.d.svc["ACC"].pk, self.d.svc["ECH"].pk

        # 1. Tablette : mode Externe puis service Échographie.
        tablette = self.client_class()
        tablette.post(reverse("circuit:borne"), {"centre": "TAM", "code": "TAB", "cle": cle})
        accueil_borne = tablette.get(reverse("circuit:borne"))
        self.assertContains(accueil_borne, "Externe")
        self.assertContains(accueil_borne, "Échographie")
        tablette.post(reverse("circuit:borne_emettre"),
                      {"mode": "EXTERNE", "parcours": self.d.parcours["E"].pk})
        ticket = Ticket.objects.get()
        self.assertEqual((ticket.numero, ticket.mode), ("E001", "EXTERNE"))

        # 2. L'accueil voit le ticket aussitôt (et le fragment rafraîchi toutes les 5 s aussi).
        self.connecter(self.accueil, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:guichet", args=[acc])), "E001")
        self.assertContains(self.client.get(reverse("circuit:guichet_liste", args=[acc])), "E001")
        # Le service choisi le voit déjà « à venir », sans pouvoir l'appeler.
        self.connecter(echographe, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[ech]))
        self.assertContains(page, "À venir")
        self.assertContains(page, "actuellement : Accueil")
        self.client.post(reverse("circuit:guichet_action", args=[ech, "appeler"]))
        self.assertFalse(Appel.objects.filter(service__code="ECH").exists())

        # 3. L'accueil appelle, prend en charge, valide et oriente vers Échographie.
        self.connecter(self.accueil, self.d.centre)
        self.client.post(reverse("circuit:guichet_action", args=[acc, "appeler"]), {"poste": "Guichet 1"})
        entree = self.d.entree(ticket)
        self.client.post(reverse("circuit:guichet_action", args=[acc, "prendre"]), {"entree": entree.pk})
        self.assertContains(self.client.get(reverse("circuit:guichet", args=[acc])),
                            "Valider le ticket et orienter vers Échographie")
        self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]), {"entree": entree.pk})

        # 4. Échographie le voit dans sa file et l'appelle ; l'écran principal l'affiche.
        self.connecter(echographe, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[ech]))
        self.assertContains(page, "1 en attente")
        self.client.post(reverse("circuit:guichet_action", args=[ech, "appeler"]), {"poste": "Salle 1"})
        appel = Appel.objects.get(service__code="ECH")
        self.assertEqual((appel.numero_ticket, appel.poste, appel.agent), ("E001", "Salle 1", echographe))
        ecran = self.client.get(reverse("circuit:ecran_appels"))
        self.assertContains(ecran, "E001")
        self.assertContains(ecran, "Salle 1")
        ticket.refresh_from_db()
        self.assertIsNone(ticket.patient)


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0)
class MotifsAutreTests(BaseVues):
    def test_motifs_par_defaut_proposes_sur_la_borne(self):
        from circuit.models import MOTIFS_AUTRE_PAR_DEFAUT, MotifDemande

        motifs = list(MotifDemande.objects.filter(centre=self.d.centre).order_by("ordre")
                      .values_list("libelle", flat=True))
        for libelle in ("Visite d'embauche", "Entretien"):
            self.assertIn(libelle, motifs)
        self.assertTrue(set(MOTIFS_AUTRE_PAR_DEFAUT) <= set(motifs))

        borne = Borne(centre=self.d.centre, code="B1", libelle="Borne")
        cle = borne.generer_cle()
        borne.save()
        self.client.post(reverse("circuit:borne"), {"centre": "TAM", "code": "B1", "cle": cle})
        page = self.client.get(reverse("circuit:borne"))
        self.assertContains(page, "Visite d&#x27;embauche")
        self.assertContains(page, "Entretien")
        embauche = MotifDemande.objects.get(centre=self.d.centre, libelle="Visite d'embauche")
        self.client.post(reverse("circuit:borne_emettre"), {"mode": "AUTRE", "motif": embauche.pk})
        ticket = Ticket.objects.get(mode="AUTRE", demande__motif=embauche)
        self.assertEqual(ticket.numero, "A001")
        self.assertIsNone(ticket.parcours)  # hors parcours de soins


class InterfaceDynamiqueTests(BaseVues):
    """Refonte visuelle : chiffres du service, mise à jour après action, état de l'imprimante."""

    def test_indicateurs_du_service(self):
        ticket = self.d.ticket("M")
        self.connecter(self.accueil, self.d.centre)
        url = reverse("circuit:guichet_indicateurs", args=[self.d.svc["ACC"].pk])
        reponse = self.client.get(url)
        self.assertContains(reponse, "en attente")
        self.assertEqual(reponse.context["indicateurs"]["en_attente"], 1)
        self.d.accueillir(ticket, self.accueil)
        indicateurs = self.client.get(url).context["indicateurs"]
        self.assertEqual(indicateurs["en_attente"], 0)
        self.assertEqual(indicateurs["traites"], 1)
        self.assertEqual(indicateurs["traites_par_moi"], 1)
        self.assertIsNotNone(indicateurs["attente_moyenne"])
        # Un agent d'un autre centre n'y a pas accès.
        self.connecter(self.agent_b, self.b.centre)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_action_htmx_declenche_la_mise_a_jour_des_chiffres(self):
        self.d.ticket("M")
        self.connecter(self.accueil, self.d.centre)
        reponse = self.client.post(
            reverse("circuit:guichet_action", args=[self.d.svc["ACC"].pk, "appeler"]),
            {"file": self.d.file("ACC").pk},
            HTTP_HX_REQUEST="true",
        )
        self.assertEqual(reponse["HX-Trigger"], "guichet-maj")
        self.assertContains(reponse, "Derniers appels du service")

    def test_icones_des_services(self):
        from core.templatetags.interface import icone, icone_service

        self.assertEqual(icone_service(self.d.svc["MED"]), "medecin")
        self.assertEqual(icone_service(self.d.svc["PHA"]), "pharmacie")
        self.assertEqual(icone_service(None), "autre")
        self.assertIn('href="#i-tickets"', icone("tickets"))

    def test_responsable_n_a_aucun_bouton_d_appel(self):
        responsable = self.d.agent("resp_vue", "Responsable")
        self.connecter(responsable, self.d.centre)
        for code in ("INF", "ACC"):
            page = self.client.get(reverse("circuit:guichet", args=[self.d.svc[code].pk]))
            self.assertContains(page, "Tout passe d'abord par l'accueil")
            self.assertNotContains(page, "Appeler le suivant")


@override_settings(RESEAU_LOCAL_UNIQUEMENT=True, BORNE_DELAI_MIN_SECONDES=0)
class ActivationBorneEnUnClicTests(BaseVues):
    """L'administrateur active ou désactive une borne en un clic (19/11/2026)."""

    def setUp(self):
        super().setUp()
        self.borne = Borne(centre=self.d.centre, code="B1", libelle="Borne entrée")
        self.cle = self.borne.generer_cle()
        self.borne.save()
        self.admin = Utilisateur.objects.create_superuser("admin_b", "", "mot-de-passe-1")
        self.tablette = self.client_class()
        self.tablette.post(reverse("circuit:borne"), {"centre": "TAM", "code": "B1", "cle": self.cle})

    def test_bouton_de_la_liste_et_reprise_sans_cle(self):
        self.client.force_login(self.admin)
        liste = reverse("admin:centres_borne_changelist")
        url = reverse("admin:centres_borne_basculer", args=[self.borne.pk])
        self.assertContains(self.client.get(liste), "Désactiver")
        self.assertEqual(self.client.get(url).status_code, 405)          # jamais par un simple lien
        self.client.post(url, HTTP_REFERER="http://testserver" + liste)
        self.borne.refresh_from_db()
        self.assertFalse(self.borne.actif)
        # La tablette affiche « borne fermée » et ne peut plus émettre de ticket.
        self.assertContains(self.tablette.get(reverse("circuit:borne")), "Borne momentanément fermée")
        self.tablette.post(reverse("circuit:borne_emettre"),
                           {"mode": "EXTERNE", "parcours": self.d.parcours["E"].pk})
        self.assertFalse(Ticket.objects.exists())
        # Réactivation en un clic : la tablette reprend sans ressaisir la clé.
        self.client.post(url)
        self.borne.refresh_from_db()
        self.assertTrue(self.borne.actif)
        self.assertContains(self.tablette.get(reverse("circuit:borne")), "Vous êtes")
        self.assertTrue(LogEntry.objects.filter(object_id=str(self.borne.pk)).exists())

    def test_actions_groupees_et_isolation_des_centres(self):
        self.client.force_login(self.admin)
        self.client.post(reverse("admin:centres_borne_changelist"),
                         {"action": "desactiver", "_selected_action": [self.borne.pk]})
        self.borne.refresh_from_db()
        self.assertFalse(self.borne.actif)
        # Un responsable d'un autre centre ne peut pas toucher cette borne.
        autre = self.b.agent("staff_b", "Responsable")
        autre.is_staff = True
        autre.save()
        autre.user_permissions.add(*Permission.objects.filter(codename__in=["view_borne", "change_borne"]))
        self.client.force_login(autre)
        reponse = self.client.post(reverse("admin:centres_borne_basculer", args=[self.borne.pk]))
        self.assertEqual(reponse.status_code, 404)
        self.borne.refresh_from_db()
        self.assertFalse(self.borne.actif)

    def test_zones_actualisees_ne_transmettent_pas_hx_select_a_leurs_liens(self):
        """
        Régression (19/11/2026) : un lien dans une zone actualisée avec
        hx-select héritait de ce hx-select ; la navigation sans rechargement
        affichait alors une page blanche (ex. ouvrir un service).
        """
        self.connecter(self.accueil, self.d.centre)
        for url in (reverse("circuit:guichets"), reverse("circuit:tickets"),
                    reverse("circuit:demandes"), reverse("circuit:notifications")):
            page = self.client.get(url).content.decode()
            for balise in re.findall(r"<[^>]*hx-select=[^>]*>", page):
                self.assertIn('hx-disinherit="hx-select hx-swap hx-target"', balise, url)


class DeuxTypesAdministrateurTests(BaseVues):
    """Décision du 19/11/2026 : administrateur de la plateforme et administrateur de centre."""

    def setUp(self):
        super().setUp()
        self.borne_a = Borne.objects.create(centre=self.d.centre, code="BA", libelle="Borne A", cle_hash="x")
        self.borne_b = Borne.objects.create(centre=self.b.centre, code="BB", libelle="Borne B", cle_hash="x")
        self.admin_centre = self.d.agent("admincentre_a", "Administrateur de centre")
        self.admin_centre.type_compte = Utilisateur.TypeCompte.ADMIN_CENTRE
        self.admin_centre.save()
        self.plateforme = Utilisateur.objects.create_user("chef_plateforme", password="mot-de-passe-1",
                                                         type_compte=Utilisateur.TypeCompte.ADMINISTRATEUR)

    def test_administrateur_de_centre_limite_a_son_centre(self):
        self.assertTrue(self.admin_centre.is_staff)          # accès à l'administration
        self.client.force_login(self.admin_centre)
        bornes = self.client.get(reverse("admin:centres_borne_changelist"))
        self.assertContains(bornes, "Borne A")
        self.assertNotContains(bornes, "Borne B")
        # Les centres et le journal d'audit restent à l'administrateur de la plateforme.
        self.assertEqual(self.client.get(reverse("admin:centres_centre_changelist")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:audit_journalaudit_changelist")).status_code, 403)
        # Il ne peut pas modifier le compte d'un administrateur de la plateforme.
        AffectationUtilisateur.objects.create(utilisateur=self.plateforme, centre=self.d.centre)
        comptes = self.client.get(reverse("admin:comptes_utilisateur_changelist"))
        self.assertContains(comptes, "accueil1")
        self.assertNotContains(comptes, "chef_plateforme")

    def test_administrateur_de_la_plateforme_voit_tous_les_centres(self):
        self.assertTrue(self.plateforme.is_staff)
        self.client.force_login(self.plateforme)
        bornes = self.client.get(reverse("admin:centres_borne_changelist"))
        self.assertContains(bornes, "Borne A")
        self.assertContains(bornes, "Borne B")

    def test_les_deux_administrateurs_n_appellent_aucun_ticket(self):
        from comptes.permissions import appel_limite_aux_autres, voit_autres_seulement

        file = self.d.file("ACC")
        file.nb_appels_simultanes = 4
        file.save()
        patient = self.d.ticket("M")
        for admin in (self.admin_centre, self.plateforme):
            self.assertTrue(appel_limite_aux_autres(admin, self.d.centre))
            with self.assertRaisesMessage(ErreurCircuit, "Tout passe d'abord par l'accueil"):
                services.appeler(file, admin, entree=self.d.entree(patient))
        # L'administrateur de centre, comme la direction, ne voit aucun patient.
        self.assertTrue(voit_autres_seulement(self.admin_centre, self.d.centre))
        self.connecter(self.admin_centre, self.d.centre)
        self.assertEqual(self.client.get(reverse("circuit:ticket", args=[patient.pk])).status_code, 404)
        autre = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        with self.assertRaisesMessage(ErreurCircuit, "Tout passe d'abord par l'accueil"):
            services.appeler(file, self.admin_centre, entree=self.d.entree(autre))


class AppelVisiteurDepuisNotificationsTests(BaseVues):
    def test_bouton_appeler_le_visiteur(self):
        directeur = self.d.agent("dir_notif", "Direction")
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil, entree=entree)
        services.prendre_en_charge(entree, self.accueil)
        notification = services.transmettre_demande(ticket.demande, self.accueil, destinataire=directeur)
        services.terminer(entree, self.accueil)
        self.connecter(directeur, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:notifications")), "Appeler le visiteur à l")
        url = reverse("circuit:notification_appeler", args=[notification.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        page = self.client.post(url, {"poste": "Bureau 1"}, follow=True)
        self.assertContains(page, f"{ticket.numero} appelé à l")
        self.assertTrue(Appel.objects.filter(ticket=ticket, agent=directeur, poste="Bureau 1").exists())
        # Un autre agent ne peut pas utiliser cette notification.
        self.connecter(self.medecin, self.d.centre)
        self.assertEqual(self.client.post(url).status_code, 404)
