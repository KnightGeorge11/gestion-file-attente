"""
Tests des comptes : authentification, rôles, permissions par service,
affectations multi-centres, changement de centre actif, désactivation,
refus d'accès hors périmètre et protection de l'administration.
"""

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from centres.access import SESSION_CENTRE_KEY, centres_autorises
from centres.models import Centre, Service
from comptes.management.commands.initialiser_roles import ROLES, initialiser_roles
from comptes.models import AffectationService, AffectationUtilisateur, Utilisateur
from comptes.permissions import a_droit

MDP = "mot-de-passe-1"


class BaseComptes(TestCase):
    @classmethod
    def setUpTestData(cls):
        initialiser_roles()
        cls.a = Centre.objects.create(code="AAA", nom="Centre A")
        cls.b = Centre.objects.create(code="BBB", nom="Centre B")
        cls.acc_a = Service.objects.create(centre=cls.a, code="ACC", nom="Accueil", est_accueil=True)
        cls.inf_a = Service.objects.create(centre=cls.a, code="INF", nom="Infirmerie")
        cls.acc_b = Service.objects.create(centre=cls.b, code="ACC", nom="Accueil", est_accueil=True)

    @staticmethod
    def utilisateur(nom, **kwargs):
        return Utilisateur.objects.create_user(username=nom, password=MDP, **kwargs)

    @staticmethod
    def affecter(utilisateur, centre, role, services=(), **kwargs):
        affectation = AffectationUtilisateur.objects.create(
            utilisateur=utilisateur, centre=centre, groupe=Group.objects.get(name=role), **kwargs
        )
        for service in services:
            AffectationService.objects.create(affectation=affectation, service=service)
        return affectation


class AuthentificationTests(BaseComptes):
    def test_connexion_par_le_formulaire_et_identifiant_en_minuscules(self):
        self.utilisateur("Agent.Accueil")
        self.assertTrue(Utilisateur.objects.filter(username="agent.accueil").exists())
        reponse = self.client.post(reverse("connexion"), {"username": "AGENT.ACCUEIL", "password": MDP})
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("_auth_user_id", self.client.session)

    def test_mauvais_mot_de_passe_refuse(self):
        self.utilisateur("agent")
        reponse = self.client.post(reverse("connexion"), {"username": "agent", "password": "faux"})
        self.assertEqual(reponse.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_compte_desactive_refuse_et_session_existante_coupee(self):
        agent = self.utilisateur("agent")
        self.affecter(agent, self.a, "Accueil", [self.acc_a])
        self.client.force_login(agent)
        agent.is_active = False
        agent.save()
        self.assertFalse(self.client.login(username="agent", password=MDP))
        reponse = self.client.get(reverse("circuit:guichets"))
        self.assertEqual(reponse.status_code, 302)
        self.assertIn(reverse("connexion"), reponse["Location"])

    def test_compte_jamais_supprime(self):
        agent = self.utilisateur("agent")
        with self.assertRaises(ValidationError):
            agent.delete()
        self.assertTrue(Utilisateur.objects.filter(pk=agent.pk).exists())

    def test_deconnexion(self):
        self.client.force_login(self.utilisateur("agent"))
        self.client.post(reverse("deconnexion"))
        self.assertNotIn("_auth_user_id", self.client.session)


class RolesEtPermissionsTests(BaseComptes):
    def test_roles_de_reference_sans_role_securite(self):
        noms = set(Group.objects.values_list("name", flat=True))
        self.assertEqual(noms, set(ROLES))
        self.assertFalse(any("curit" in nom.lower() for nom in noms))

    def test_permission_du_role_dans_la_portee_du_service(self):
        agent = self.utilisateur("inf")
        self.affecter(agent, self.a, "Infirmerie", [self.inf_a])
        self.assertTrue(a_droit(agent, "circuit.appeler", self.a, self.inf_a))
        self.assertTrue(a_droit(agent, "circuit.transferer", self.a, self.inf_a))
        # Service hors de la liste de l'affectation.
        self.assertFalse(a_droit(agent, "circuit.appeler", self.a, self.acc_a))
        # Permission absente du rôle.
        self.assertFalse(a_droit(agent, "circuit.annuler_ticket", self.a, self.inf_a))
        self.assertFalse(a_droit(agent, "parcours.deroger_quota", self.a, self.inf_a))

    def test_affectation_sans_liste_couvre_tous_les_services_du_centre(self):
        agent = self.utilisateur("resp")
        self.affecter(agent, self.a, "Responsable")
        self.assertTrue(a_droit(agent, "circuit.appeler", self.a, self.acc_a))
        self.assertTrue(a_droit(agent, "circuit.appeler", self.a, self.inf_a))

    def test_service_d_un_autre_centre_refuse(self):
        agent = self.utilisateur("acc")
        self.affecter(agent, self.a, "Accueil")
        self.assertFalse(a_droit(agent, "circuit.appeler", self.a, self.acc_b))
        self.assertFalse(a_droit(agent, "circuit.appeler", self.b, self.acc_b))

    def test_permission_individuelle_ajoutee_a_l_affectation(self):
        agent = self.utilisateur("acc")
        affectation = self.affecter(agent, self.a, "Accueil", [self.acc_a])
        self.assertFalse(a_droit(agent, "parcours.deroger_quota", self.a, self.acc_a))
        affectation.permissions.add(Permission.objects.get(codename="deroger_quota"))
        agent = Utilisateur.objects.get(pk=agent.pk)
        self.assertTrue(a_droit(agent, "parcours.deroger_quota", self.a, self.acc_a))
        # La permission individuelle ne vaut que pour ce centre.
        self.affecter(agent, self.b, "Accueil", [self.acc_b])
        self.assertFalse(a_droit(agent, "parcours.deroger_quota", self.b, self.acc_b))

    def test_affectation_desactivee_ou_centre_inactif(self):
        agent = self.utilisateur("acc")
        affectation = self.affecter(agent, self.a, "Accueil", [self.acc_a])
        affectation.actif = False
        affectation.save()
        self.assertFalse(a_droit(agent, "circuit.appeler", self.a, self.acc_a))
        affectation.actif = True
        affectation.save()
        Centre.objects.filter(pk=self.a.pk).update(actif=False)
        self.a.refresh_from_db()
        self.assertFalse(a_droit(agent, "circuit.appeler", self.a, self.acc_a))

    def test_direction_lecture_seule(self):
        directeur = self.utilisateur("dir")
        self.affecter(directeur, self.a, "Direction")
        self.assertTrue(a_droit(directeur, "circuit.voir_statistiques", self.a))
        for action in ("circuit.appeler", "circuit.transferer", "circuit.orienter_demande",
                       "circuit.annuler_ticket", "parcours.liberer_quota"):
            self.assertFalse(a_droit(directeur, action, self.a, self.acc_a), action)

    def test_administrateur_central(self):
        admin = self.utilisateur("admin", type_compte="ADMIN")
        root = Utilisateur.objects.create_superuser("root", password=MDP)
        for utilisateur in (admin, root):
            self.assertTrue(a_droit(utilisateur, "circuit.appeler", self.b, self.acc_b))
            self.assertEqual(set(centres_autorises(utilisateur)), {self.a, self.b})

    def test_backend_has_perm_avec_objet_centre_ou_service(self):
        agent = self.utilisateur("acc")
        self.affecter(agent, self.a, "Accueil", [self.acc_a])
        self.assertTrue(agent.has_perm("circuit.appeler", self.acc_a))
        self.assertFalse(agent.has_perm("circuit.appeler", self.acc_b))
        self.assertTrue(agent.has_perm("circuit.annuler_ticket", self.a))
        self.assertFalse(agent.has_perm("circuit.annuler_ticket", self.b))
        # Sans objet : sert uniquement aux menus (union des affectations).
        self.assertTrue(agent.has_perm("circuit.appeler"))
        self.assertFalse(agent.has_perm("circuit.voir_statistiques"))


class MultiCentresTests(BaseComptes):
    def setUp(self):
        self.agent = self.utilisateur("multi")
        self.affecter(self.agent, self.a, "Accueil", [self.acc_a])
        self.affecter(self.agent, self.b, "Infirmerie")

    def test_roles_differents_selon_le_centre(self):
        self.assertTrue(a_droit(self.agent, "circuit.annuler_ticket", self.a, self.acc_a))
        self.assertFalse(a_droit(self.agent, "circuit.annuler_ticket", self.b, self.acc_b))
        self.assertTrue(a_droit(self.agent, "circuit.transferer", self.b, self.acc_b))

    def test_selection_et_changement_du_centre_actif(self):
        self.client.force_login(self.agent)
        # Deux centres : pas de sélection automatique.
        self.assertRedirects(self.client.get(reverse("circuit:guichets")), reverse("centres:selection"),
                             fetch_redirect_response=False)
        self.client.post(reverse("centres:selection"), {"centre_id": self.a.pk})
        self.assertEqual(self.client.get(reverse("circuit:guichets")).context["centre_actif"], self.a)
        self.client.post(reverse("centres:selection"), {"centre_id": self.b.pk})
        self.assertEqual(self.client.get(reverse("circuit:guichets")).context["centre_actif"], self.b)

    def test_selection_d_un_centre_non_affecte_refusee(self):
        c = Centre.objects.create(code="CCC", nom="Centre C")
        self.client.force_login(self.agent)
        self.client.post(reverse("centres:selection"), {"centre_id": c.pk})
        self.assertNotEqual(self.client.session.get(SESSION_CENTRE_KEY), c.pk)

    def test_retrait_d_une_affectation_coupe_l_acces_au_centre(self):
        self.client.force_login(self.agent)
        session = self.client.session
        session[SESSION_CENTRE_KEY] = self.b.pk
        session.save()
        AffectationUtilisateur.objects.filter(utilisateur=self.agent, centre=self.b).update(actif=False)
        reponse = self.client.get(reverse("circuit:guichets"))
        # Un seul centre restant : il devient le centre actif, B n'est plus accessible.
        self.assertNotEqual(self.client.session.get(SESSION_CENTRE_KEY), self.b.pk)
        self.assertEqual(reponse.status_code, 302)


class AdministrationTests(BaseComptes):
    """Un responsable ayant accès à l'administration reste limité à ses centres."""

    def setUp(self):
        self.resp = self.utilisateur("resp", is_staff=True)
        self.affecter(self.resp, self.a, "Responsable")
        self.resp.user_permissions.add(*Permission.objects.filter(
            codename__in=["view_utilisateur", "change_utilisateur", "add_utilisateur",
                          "view_affectationutilisateur", "change_affectationutilisateur",
                          "add_affectationutilisateur"]
        ))
        self.agent_b = self.utilisateur("agent_b")
        self.affecter(self.agent_b, self.b, "Accueil")
        self.agent_a = self.utilisateur("agent_a")
        self.affectation_a = self.affecter(self.agent_a, self.a, "Accueil")
        self.client.force_login(self.resp)

    def test_ne_voit_que_les_comptes_et_affectations_de_ses_centres(self):
        liste = self.client.get(reverse("admin:comptes_utilisateur_changelist"))
        self.assertContains(liste, "agent_a")
        self.assertNotContains(liste, "agent_b")
        url_b = reverse("admin:comptes_utilisateur_change", args=[self.agent_b.pk])
        self.assertNotEqual(self.client.get(url_b).status_code, 200)
        affectations = self.client.get(reverse("admin:comptes_affectationutilisateur_changelist"))
        self.assertContains(affectations, "agent_a")
        self.assertNotContains(affectations, "agent_b")

    def test_ne_peut_pas_s_attribuer_de_droits_transversaux(self):
        url = reverse("admin:comptes_utilisateur_change", args=[self.agent_a.pk])
        formulaire = self.client.get(url)
        self.assertEqual(formulaire.status_code, 200)
        self.assertNotContains(formulaire, 'name="is_superuser"')
        self.assertNotContains(formulaire, 'name="type_compte"')
        self.assertNotContains(formulaire, 'name="groups"')

    def test_ne_peut_pas_affecter_a_un_centre_non_autorise(self):
        url = reverse("admin:comptes_affectationutilisateur_add")
        reponse = self.client.post(url, {
            "utilisateur": self.agent_a.pk, "centre": self.b.pk,
            "groupe": Group.objects.get(name="Accueil").pk, "role": "AGENT", "actif": "on",
            "services_autorises-TOTAL_FORMS": "0", "services_autorises-INITIAL_FORMS": "0",
        })
        self.assertEqual(reponse.status_code, 200)  # formulaire réaffiché avec erreur
        self.assertFalse(AffectationUtilisateur.objects.filter(utilisateur=self.agent_a, centre=self.b).exists())

    def test_agent_sans_is_staff_n_accede_pas_a_l_administration(self):
        self.client.force_login(self.agent_a)
        reponse = self.client.get(reverse("admin:index"))
        self.assertEqual(reponse.status_code, 302)
