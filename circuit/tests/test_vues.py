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
