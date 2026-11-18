import datetime

from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import JournalAudit
from centres.access import SESSION_CENTRE_KEY
from centres.models import Borne, EcranAffichage
from circuit import services
from circuit.models import Appel, EntreeFile, Ticket
from comptes.models import AffectationUtilisateur, Utilisateur
from patients.models import FusionPatient, Patient, PatientCentre, SignalementDoublon

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
        self.assertEqual(self.client.get(reverse("circuit:identifier", args=[ticket.pk])).status_code, 404)
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

        reponse = self.client.post(
            reverse("circuit:identifier", args=[ticket.pk]),
            {"nouveau": "1", "nom": "RAZAFY", "prenom": "Lova"},
        )
        self.assertEqual(reponse.status_code, 302)
        ticket.refresh_from_db()
        self.assertEqual(ticket.patient.nom, "RAZAFY")

        reponse = self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]),
                                   {"entree": entree.pk}, HTTP_HX_REQUEST="true")
        self.assertContains(reponse, "Infirmerie")

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
        with a_heure(LUNDI_MATIN):
            quota = self.d.parcours["D"].quotas.get(jour_semaine=0, periode="MATIN")
            quota.nombre_places = 0
            quota.save()
            ticket = self.d.ticket("D")
            entree = self.d.entree(ticket)
            services.appeler(self.d.file("ACC"), self.accueil)
            services.prendre_en_charge(entree, self.accueil)
            self.connecter(self.accueil, self.d.centre)
            reponse = self.client.post(reverse("circuit:identifier", args=[ticket.pk]),
                                       {"nouveau": "1", "nom": "RABE"}, follow=True)
        self.assertContains(reponse, "Quota atteint")
        ticket.refresh_from_db()
        self.assertIsNone(ticket.patient)

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
        services.identifier_patient(t1, self.d.patient("A"), self.accueil)
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
        services.identifier_patient(ticket, self.d.patient(), self.accueil)
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


class FusionPatientsTests(BaseVues):
    def test_fusion_reversible_sans_reattribution(self):
        responsable = self.d.agent("resp", "Responsable")
        p1 = Patient.objects.create(nom="RAKOTO", prenom="Jean")
        p2 = Patient.objects.create(nom="RAKOTOO", prenom="Jean")
        PatientCentre.objects.create(patient=p1, centre=self.d.centre)
        PatientCentre.objects.create(patient=p2, centre=self.d.centre)
        ticket = self.d.ticket("I")
        self.d.accueillir(ticket, self.accueil, patient=p2)
        signalement = SignalementDoublon.objects.create(patient_1=p1, patient_2=p2)
        self.connecter(responsable, self.d.centre)
        self.client.post(reverse("patients:doublons"),
                         {"signalement": signalement.pk, "action": "fusionner", "garder": p1.pk, "motif": "Même personne"})
        p2.refresh_from_db()
        self.assertEqual(p2.fusionne_dans, p1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.patient, p2)  # aucun ticket réattribué
        fusion = FusionPatient.objects.get()
        self.client.post(reverse("patients:doublons"), {"fusion": fusion.pk, "action": "annuler_fusion"})
        p2.refresh_from_db()
        self.assertIsNone(p2.fusionne_dans)
        self.assertEqual(JournalAudit.objects.filter(action__in=["FUSION", "ANNULATION_FUSION"]).count(), 2)

    def test_agent_sans_permission_ne_peut_pas_fusionner(self):
        self.connecter(self.medecin, self.d.centre)
        self.assertEqual(self.client.get(reverse("patients:doublons")).status_code, 403)


class IsolationCompleteTests(BaseVues):
    """Un agent du centre B ne lit ni ne modifie rien du centre A."""

    def test_recherche_patient_limitee_au_centre(self):
        patient = Patient.objects.create(nom="RANDRIA", prenom="Tiana")
        PatientCentre.objects.create(patient=patient, centre=self.d.centre)
        self.connecter(self.agent_b, self.b.centre)
        reponse = self.client.get(reverse("patients:recherche"), {"q": "RANDRIA"})
        self.assertNotContains(reponse, "Tiana")
        reponse = self.client.get(reverse("patients:recherche"), {"q": "TAM-000001"})
        self.assertNotContains(reponse, "Tiana")

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
    NOMS = iter(["RAKOTO", "BEZARA", "NOMENJANAHARY", "FALY", "ANDRIAMIHAJA", "SOA", "TSIRY", "VOLOLONA"])
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
        if code == "ACC":
            self.client.post(reverse("circuit:identifier", args=[ticket.pk]),
                             {"nouveau": "1", "nom": next(self.NOMS)})
            ticket.refresh_from_db()
            self.assertIsNotNone(ticket.patient, "identification à l'accueil")
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
        services.identifier_patient(t1, self.d.patient("UN"), self.accueil)
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

    def test_pages_patients_dans_la_mise_en_page_commune(self):
        responsable = self.d.agent("resp_p", "Responsable")
        self.connecter(responsable, self.d.centre)
        for url in (reverse("patients:recherche") + "?q=x", reverse("patients:creer")):
            reponse = self.client.get(url)
            self.assertContains(reponse, "Déconnexion")
            self.assertContains(reponse, reverse("patients:doublons"))
        # L'accueil voit le fichier patients mais pas le lien Doublons ; un médecin n'y a pas accès.
        self.connecter(self.accueil, self.d.centre)
        self.assertNotContains(self.client.get(reverse("patients:recherche")), reverse("patients:doublons"))
        self.connecter(self.medecin, self.d.centre)
        self.assertEqual(self.client.get(reverse("patients:recherche")).status_code, 403)

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
            self.d.accueillir(self.m_pharmacie, self.accueil, patient=self.d.patient("MEDECIN", "Pharma"))
            self.d.traiter(self.m_pharmacie, "INF", self.infirmier)
            self.d.traiter(self.m_pharmacie, "MED", self.medecin, transferer=True)
            # M encore à l'infirmerie (pas encore chez les médecins).
            self.m_infirmerie = self.d.ticket("M")
            self.d.accueillir(self.m_infirmerie, self.accueil, patient=self.d.patient("ENCORE", "Infirmerie"))
            # D chez le dentiste ; E encore à l'accueil.
            self.dent = self.d.ticket("D")
            self.d.accueillir(self.dent, self.accueil, patient=self.d.patient("DENT", "Seul"))
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

    def test_medecin(self):
        self.assertEqual(self._visibles(self.medecin), {self.m_pharmacie})

    def test_infirmerie(self):
        self.assertEqual(self._visibles(self.infirmier), {self.m_pharmacie, self.m_infirmerie})

    def test_dentiste_et_echographie(self):
        self.assertEqual(self._visibles(self.dentiste), {self.dent})
        self.assertEqual(self._visibles(self.echo), set())

    def test_pharmacie_seulement_les_patients_transferes(self):
        self.assertEqual(self._visibles(self.pharma), {self.m_pharmacie})

    def test_accueil_responsable_voient_tout(self):
        self.assertEqual(self._visibles(self.accueil), self.tous)
        self.assertEqual(self._visibles(self.responsable), self.tous)

    def test_direction_ne_voit_aucun_patient(self):
        self.assertEqual(self._visibles(self.directeur), set())
        self.assertEqual(self.client.get(reverse("patients:recherche")).status_code, 403)
        self.assertNotContains(self.client.get(reverse("circuit:guichets")), "Tickets du jour")

    def test_fichier_patients_reserve_a_l_accueil_et_aux_responsables(self):
        for agent, attendu in ((self.medecin, 403), (self.pharma, 403), (self.infirmier, 403),
                               (self.accueil, 200), (self.responsable, 200)):
            self.connecter(agent, self.d.centre)
            self.assertEqual(self.client.get(reverse("patients:recherche"), {"q": "DENT"}).status_code,
                             attendu, agent.username)
            self.assertEqual(self.client.get(reverse("patients:creer")).status_code, attendu, agent.username)
        self.connecter(self.medecin, self.d.centre)
        menu = self.client.get(reverse("circuit:guichets"))
        self.assertNotContains(menu, reverse("patients:recherche"))

    def test_medecin_voit_le_patient_des_son_arrivee_dans_sa_file(self):
        with a_heure(LUNDI_MATIN):
            self.d.traiter(self.m_infirmerie, "INF", self.infirmier)
        self.assertIn(self.m_infirmerie, self._visibles(self.medecin))
        # Et la page de son service affiche bien ce patient en attente.
        reponse = self.client.get(reverse("circuit:guichet", args=[self.d.svc["MED"].pk]))
        self.assertContains(reponse, self.m_infirmerie.numero)

    def test_administrateur_central_voit_tout(self):
        admin = Utilisateur.objects.create_user("central_v", password="x", type_compte="ADMIN")
        self.assertEqual(self._visibles(admin), self.tous)
