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
from comptes.permissions import a_droit, appel_limite_aux_autres

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
        # « transférer » n'est pas un droit du rôle Accueil.
        self.assertFalse(a_droit(agent, "circuit.transferer", self.a, self.acc_a))
        affectation.permissions.add(Permission.objects.get(codename="transferer"))
        agent = Utilisateur.objects.get(pk=agent.pk)
        self.assertTrue(a_droit(agent, "circuit.transferer", self.a, self.acc_a))
        # La permission individuelle ne vaut que pour ce centre.
        self.affecter(agent, self.b, "Accueil", [self.acc_b])
        self.assertFalse(a_droit(agent, "circuit.transferer", self.b, self.acc_b))

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
        # Décision du 19/11/2026 : la direction peut appeler, mais seulement les
        # tickets « Autre » (contrôlé dans circuit.services.appeler).
        directeur = self.utilisateur("dir")
        self.affecter(directeur, self.a, "Direction")
        self.assertTrue(a_droit(directeur, "circuit.voir_statistiques", self.a))
        self.assertTrue(appel_limite_aux_autres(directeur, self.a))
        for action in ("circuit.transferer", "circuit.orienter_demande",
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


class AdministrateurDeCentreSecuriteTests(BaseComptes):
    """
    Audit du 20/11/2026 : un administrateur de centre ne doit ni s'attribuer
    des droits sur la plateforme, ni prendre la main sur un compte d'un
    autre centre, ni administrer un centre où il n'est qu'agent.
    """

    def setUp(self):
        self.admin_a = self.utilisateur("admin_a", type_compte=Utilisateur.TypeCompte.ADMIN_CENTRE)
        self.aff_admin = self.affecter(self.admin_a, self.a, "Administrateur de centre")
        self.agent_a = self.utilisateur("agent_a")
        self.affecter(self.agent_a, self.a, "Accueil")
        self.agent_b = self.utilisateur("agent_b")
        self.affecter(self.agent_b, self.b, "Accueil")
        self.client.force_login(self.admin_a)

    def formulaire_affectation(self, **valeurs):
        donnees = {
            "role": "AGENT", "actif": "on",
            "services_autorises-TOTAL_FORMS": "0", "services_autorises-INITIAL_FORMS": "0",
        }
        donnees.update(valeurs)
        return donnees

    def test_ne_modifie_pas_sa_propre_affectation(self):
        url = reverse("admin:comptes_affectationutilisateur_change", args=[self.aff_admin.pk])
        changement_centre = Permission.objects.get(codename="change_centre")
        self.client.post(url, self.formulaire_affectation(
            utilisateur=self.admin_a.pk, centre=self.a.pk,
            groupe=Group.objects.get(name="Administrateur de centre").pk,
            permissions=[changement_centre.pk],
        ))
        self.assertFalse(self.aff_admin.permissions.exists())
        self.assertFalse(self.admin_a.has_perm("centres.change_centre"))

    def test_permissions_proposees_uniquement_metier(self):
        reponse = self.client.get(reverse("admin:comptes_affectationutilisateur_add"))
        choix = reponse.context["adminform"].form.fields["permissions"].queryset
        codes = {f"{p.content_type.app_label}.{p.codename}" for p in choix}
        self.assertIn("circuit.appeler", codes)
        self.assertNotIn("centres.change_centre", codes)
        self.assertNotIn("auth.change_group", codes)
        self.assertNotIn("audit.view_journalaudit", codes)
        roles = reponse.context["adminform"].form.fields["groupe"].queryset
        self.assertTrue(roles.filter(name="Accueil").exists())
        self.assertFalse(roles.filter(name="Administrateur de centre").exists())
        # Un envoi forgé avec une permission hors liste est refusé.
        changement_groupe = Permission.objects.get(codename="change_group")
        self.client.post(reverse("admin:comptes_affectationutilisateur_add"), self.formulaire_affectation(
            utilisateur=self.agent_a.pk, centre=self.a.pk, groupe=Group.objects.get(name="Accueil").pk,
            permissions=[changement_groupe.pk],
        ))
        self.assertFalse(Permission.objects.filter(codename="change_group", affectations__isnull=False).exists())

    def test_ne_prend_pas_la_main_sur_un_compte_d_un_autre_centre(self):
        reponse = self.client.get(reverse("admin:comptes_affectationutilisateur_add"))
        comptes = reponse.context["adminform"].form.fields["utilisateur"].queryset
        self.assertIn(self.agent_a, comptes)
        self.assertNotIn(self.agent_b, comptes)
        # Affecter l'agent du centre B au centre A est refusé…
        self.client.post(reverse("admin:comptes_affectationutilisateur_add"), self.formulaire_affectation(
            utilisateur=self.agent_b.pk, centre=self.a.pk, groupe=Group.objects.get(name="Accueil").pk,
        ))
        self.assertFalse(AffectationUtilisateur.objects.filter(utilisateur=self.agent_b, centre=self.a).exists())
        # … et un compte rattaché aux deux centres reste hors de sa portée.
        self.affecter(self.agent_b, self.a, "Accueil")
        self.assertNotContains(self.client.get(reverse("admin:comptes_utilisateur_changelist")), "agent_b")
        url_mdp = reverse("admin:auth_user_password_change", args=[self.agent_b.pk])
        reponse = self.client.post(url_mdp, {"password1": "Nouveau-mdp-2026", "password2": "Nouveau-mdp-2026"})
        self.assertNotEqual(reponse.status_code, 302)
        self.agent_b.refresh_from_db()
        self.assertTrue(self.agent_b.check_password(MDP))

    def test_centres_roles_et_journal_reserves_a_la_plateforme(self):
        for nom in ("admin:centres_centre_changelist", "admin:auth_group_changelist",
                    "admin:audit_journalaudit_changelist"):
            self.assertEqual(self.client.get(reverse(nom)).status_code, 403, nom)
        self.assertEqual(self.client.get(reverse("admin:centres_centre_change", args=[self.b.pk])).status_code, 403)

    def test_n_administre_pas_un_centre_ou_il_n_est_qu_agent(self):
        from centres.models import Borne

        self.affecter(self.admin_a, self.b, "Accueil")
        Borne.objects.create(centre=self.a, code="BA", libelle="Borne du centre A", cle_hash="x")
        Borne.objects.create(centre=self.b, code="BB", libelle="Borne du centre B", cle_hash="x")
        bornes = self.client.get(reverse("admin:centres_borne_changelist"))
        self.assertContains(bornes, "Borne du centre A")
        self.assertNotContains(bornes, "Borne du centre B")

    def test_actions_d_administration_exigent_le_droit_de_modifier(self):
        from centres.admin import BorneAdmin, ImprimanteTicketAdmin
        from parcours.admin import ParcoursAdmin, ParcoursVersionAdmin

        for admin_classe, action in ((BorneAdmin, "regenerer_cle"), (ImprimanteTicketAdmin, "imprimer_test"),
                                     (ParcoursAdmin, "creer_brouillon"), (ParcoursVersionAdmin, "publier")):
            self.assertEqual(list(getattr(admin_classe, action).allowed_permissions), ["change"], action)
