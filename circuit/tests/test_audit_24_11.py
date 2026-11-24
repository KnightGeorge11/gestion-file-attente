"""
Audit du 24/11/2026 : petits défauts corrigés (session, menus, journal,
contrôles de centre dans l'administration, horaires, onglet Borne).
"""

from datetime import timedelta

from django.db import connection
from django.forms import inlineformset_factory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from audit.models import JournalAudit
from centres.admin import HorairesFormSet
from centres.models import AppareilEcran, Borne, EcranAffichage, HoraireService, Service
from comptes.models import AffectationService, AffectationUtilisateur
from comptes.presence import CLE_VU
from core.admin import erreurs_autre_centre

from .test_vues import BaseVues


class SessionTests(BaseVues):
    def test_la_presence_ne_repousse_pas_la_deconnexion(self):
        self.connecter(self.accueil, self.d.centre)
        self.client.get(reverse("circuit:guichets"))
        echeance = self.client.session.get_expiry_date()
        self.assertLess(abs((echeance - timezone.now()) - timedelta(hours=12)), timedelta(minutes=1))
        # Une minute plus tard, la présence est notée de nouveau : l'échéance ne bouge pas.
        session = self.client.session
        session[CLE_VU] = (timezone.now() - timedelta(minutes=2)).timestamp()
        session.save()
        self.client.get(reverse("circuit:guichets"))
        self.assertEqual(self.client.session.get_expiry_date(), echeance)


class MenusTests(BaseVues):
    def test_menus_communs_sans_requetes_repetees(self):
        infirmier = self.d.agent("inf1", "Infirmerie", ["INF"]) if "INF" in self.d.svc else self.medecin
        self.connecter(infirmier, self.d.centre)
        self.client.get(reverse("circuit:guichets"))
        from django.test import RequestFactory

        from core.context_processors import navigation

        requete = RequestFactory().get("/")
        requete.user = infirmier
        requete.centre_actif = self.d.centre
        with CaptureQueriesContext(connection) as requetes:
            navigation(requete)
        self.assertLess(len(requetes), 20)

    def test_onglet_borne_selon_le_role_et_non_is_staff(self):
        admin = self.d.agent("admin_c", "Administrateur de centre")   # is_staff faux
        self.connecter(admin, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:guichets")), reverse("circuit:bornes"))


class JournalTests(BaseVues):
    def test_bascule_de_borne_journalisee_une_seule_fois(self):
        admin = self.d.agent("admin_c", "Administrateur de centre")
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Tablette")
        self.connecter(admin, self.d.centre)
        avant = JournalAudit.objects.filter(objet_type="centres.borne", objet_id=str(borne.pk)).count()
        self.client.post(reverse("circuit:borne_basculer", args=[borne.pk]))
        self.assertEqual(JournalAudit.objects.filter(objet_type="centres.borne", objet_id=str(borne.pk)).count(), avant + 1)

    def test_services_d_une_borne_changes_hors_application_journalises(self):
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Tablette")
        avant = JournalAudit.objects.count()
        borne.parcours.set([self.d.parcours["M"]])
        self.assertEqual(JournalAudit.objects.count(), avant + 1)

    def test_resauvegarde_sans_changement_non_journalisee(self):
        service = self.d.svc["MED"]
        avant = JournalAudit.objects.count()
        service.save()
        self.d.centre.save()
        self.assertEqual(JournalAudit.objects.count(), avant)


class ControlesDeCentreTests(BaseVues):
    def test_choix_d_un_autre_centre_refuse(self):
        erreurs = erreurs_autre_centre({"services": Service.objects.filter(pk=self.b.svc["MED"].pk)}, self.d.centre)
        self.assertIn("services", erreurs)
        self.assertIn("destinataire_demandes", erreurs_autre_centre({"destinataire_demandes": self.agent_b},
                                                                     self.d.centre))
        self.assertEqual(erreurs_autre_centre({"services": [self.d.svc["MED"]], "agent": self.accueil},
                                              self.d.centre), {})

    def test_service_d_une_nouvelle_affectation_controle(self):
        from django.core.exceptions import ValidationError

        affectation = AffectationUtilisateur(utilisateur=self.accueil, centre=self.d.centre)
        with self.assertRaises(ValidationError):
            AffectationService(affectation=affectation, service=self.b.svc["MED"]).clean()

    def test_tv_avec_un_affichage_d_un_autre_centre_refusee(self):
        from django.core.exceptions import ValidationError

        autre = EcranAffichage.objects.create(centre=self.b.centre, code="E1", libelle="Autre")
        tv = AppareilEcran(jeton="j", code="1234", centre=self.d.centre, affichage=autre,
                           derniere_activite=timezone.now())
        with self.assertRaises(ValidationError):
            tv.clean()


class HorairesTests(BaseVues):
    def test_deux_plages_qui_se_chevauchent_saisies_ensemble(self):
        FormSet = inlineformset_factory(Service, HoraireService, formset=HorairesFormSet,
                                        fields=("jour_semaine", "heure_debut", "heure_fin"), extra=0)
        donnees = {
            "horaires-TOTAL_FORMS": "2", "horaires-INITIAL_FORMS": "0",
            "horaires-0-jour_semaine": "0", "horaires-0-heure_debut": "08:00", "horaires-0-heure_fin": "12:00",
            "horaires-1-jour_semaine": "0", "horaires-1-heure_debut": "10:00", "horaires-1-heure_fin": "14:00",
        }
        formset = FormSet(donnees, instance=self.d.svc["MED"], prefix="horaires")
        self.assertFalse(formset.is_valid())
        donnees["horaires-1-heure_debut"] = "13:00"
        donnees["horaires-1-heure_fin"] = "16:00"
        self.assertTrue(FormSet(donnees, instance=self.d.svc["MED"], prefix="horaires").is_valid())


class MoteurTests(BaseVues):
    def test_l_infirmiere_termine_le_patient_appele_si_l_accueil_arrive(self):
        from comptes.presence import marquer_present

        infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        ticket = self.d.ticket("M")
        acc = self.d.svc["ACC"].pk
        self.connecter(infirmier, self.d.centre)
        self.client.post(reverse("circuit:guichet_action", args=[acc, "appeler"]))
        entree = self.d.entree(ticket)
        self.assertEqual(entree.agent, infirmier)
        marquer_present(self.accueil, self.d.centre)        # un agent d'accueil arrive
        self.client.post(reverse("circuit:guichet_action", args=[acc, "prendre"]), {"entree": entree.pk})
        self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]), {"entree": entree.pk})
        self.assertEqual(self.d.entree(ticket).file.service.code, "INF")    # validé et orienté
        # Ticket terminé : le relais s'arrête.
        self.assertEqual(self.client.get(reverse("circuit:guichet", args=[acc])).status_code, 403)

    def test_demandes_nouvelles_avant_les_cloturees(self):
        from circuit.models import DemandeAutre, Ticket

        from circuit import services

        premiere = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE)
        services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE)
        DemandeAutre.objects.filter(ticket=premiere).update(statut=DemandeAutre.Statut.CLOTUREE)
        self.connecter(self.accueil, self.d.centre)
        page = self.client.get(reverse("circuit:demandes")).content.decode()
        self.assertLess(page.index("Nouvelle"), page.index("Clôturée"))

    def test_retour_de_ma_salle_garde_toute_l_adresse(self):
        medecin = self.medecin
        self.client.force_login(medecin)
        session = self.client.session
        from centres.access import SESSION_CENTRE_KEY

        session[SESSION_CENTRE_KEY] = self.d.centre.pk
        session.save()
        url = reverse("circuit:guichet", args=[self.d.svc["MED"].pk])
        reponse = self.client.get(url, {"file": self.d.file("MED").pk, "x": "1"})
        self.assertIn("next=%2F", reponse["Location"])
        self.assertIn("%26x%3D1", reponse["Location"])
