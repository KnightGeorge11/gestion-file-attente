"""
Décisions du 27/11/2026 : création de comptes en une page (ou par liste),
ajout d'un service à la borne en une étape, administration rangée.
"""

from django.urls import reverse

from centres.models import Borne
from circuit import services
from circuit.models import Ticket
from comptes.models import AffectationUtilisateur, Utilisateur
from parcours.simple import ajouter_service_borne, circuit

from .test_vues import BaseVues


class CreationDeComptesTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.admin = self.d.agent("admin_c", "Administrateur de centre")
        self.connecter(self.admin, self.d.centre)
        from django.contrib.auth.models import Group

        self.medecin_role = Group.objects.get(name="Médecin")

    def test_un_compte_en_une_page_avec_service_propose(self):
        reponse = self.client.post(reverse("comptes:nouveau"), {
            "prenom": "Jean", "nom": "Rakoto", "role": self.medecin_role.pk, "salle": "Salle 2",
        })
        self.assertContains(reponse, "jean.rakoto")
        compte = Utilisateur.objects.get(username="jean.rakoto")
        affectation = AffectationUtilisateur.objects.get(utilisateur=compte, centre=self.d.centre)
        self.assertEqual(affectation.groupe, self.medecin_role)
        self.assertEqual(affectation.salle, "Salle 2")
        self.assertEqual([s.service.code for s in affectation.services_autorises.all()], ["MED"])
        self.assertEqual(compte.type_compte, Utilisateur.TypeCompte.AGENT)
        # Le mot de passe affiché permet de se connecter.
        mot_de_passe = reponse.context["crees"][0]["mot_de_passe"]
        self.assertTrue(compte.check_password(mot_de_passe))

    def test_plusieurs_comptes_d_un_coup(self):
        self.client.post(reverse("comptes:plusieurs"), {
            "noms": "Jean Rakoto\nMarie Rasoa\n\nJean Rakoto", "role": self.medecin_role.pk,
        })
        self.assertTrue(Utilisateur.objects.filter(username="jean.rakoto").exists())
        self.assertTrue(Utilisateur.objects.filter(username="marie.rasoa").exists())
        self.assertTrue(Utilisateur.objects.filter(username="jean.rakoto2").exists())   # homonyme

    def test_l_admin_de_centre_ne_cree_pas_d_administrateur(self):
        from django.contrib.auth.models import Group

        admin_role = Group.objects.get(name="Administrateur de centre")
        reponse = self.client.post(reverse("comptes:nouveau"), {"nom": "Pirate", "role": admin_role.pk})
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(Utilisateur.objects.filter(last_name="Pirate").exists())

    def test_un_agent_ne_cree_pas_de_compte(self):
        self.connecter(self.accueil, self.d.centre)
        self.assertEqual(self.client.get(reverse("comptes:nouveau")).status_code, 403)
        self.assertEqual(self.client.get(reverse("comptes:comptes")).status_code, 403)

    def test_liste_limitee_au_centre_et_onglet_visible(self):
        page = self.client.get(reverse("comptes:comptes") + "?vue=tous")
        self.assertContains(page, "accueil1")
        self.assertNotContains(page, "accueil_b")
        self.assertContains(self.client.get(reverse("circuit:guichets")), reverse("comptes:comptes"))

    def test_modifier_mot_de_passe_et_desactiver(self):
        url = reverse("comptes:modifier", args=[self.medecin.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.client.post(url, {"prenom": "Paul", "nom": "Rabe", "role": self.medecin_role.pk,
                               "services": [self.d.svc["MED"].pk], "salle": "Salle 4"})
        self.medecin.refresh_from_db()
        self.assertEqual(self.medecin.last_name, "Rabe")
        reponse = self.client.post(reverse("comptes:action", args=[self.medecin.pk, "mot_de_passe"]))
        self.medecin.refresh_from_db()
        self.assertTrue(self.medecin.check_password(reponse.context["crees"][0]["mot_de_passe"]))
        self.client.post(reverse("comptes:action", args=[self.medecin.pk, "desactiver"]))
        self.medecin.refresh_from_db()
        self.assertFalse(self.medecin.is_active)

    def test_pas_de_compte_d_un_autre_centre_ni_le_sien(self):
        self.assertEqual(self.client.get(reverse("comptes:modifier", args=[self.agent_b.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse("comptes:modifier", args=[self.admin.pk])).status_code, 404)
        self.assertEqual(
            self.client.post(reverse("comptes:action", args=[self.agent_b.pk, "desactiver"])).status_code, 404
        )


class ServicesDeLaBorneTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.admin = self.d.agent("admin_c", "Administrateur de centre")
        self.connecter(self.admin, self.d.centre)

    def test_ajouter_un_service_en_une_etape(self):
        self.client.post(reverse("circuit:borne_service_ajouter"), {"nom": "Kinésithérapie", "par_infirmerie": "1"})
        from parcours.models import Parcours

        parcours = Parcours.objects.get(centre=self.d.centre, libelle_borne="Kinésithérapie")
        self.assertEqual(parcours.prefixe, "K")
        self.assertEqual(circuit(parcours), "Accueil → Infirmerie → Kinésithérapie")
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.EXTERNE, parcours=parcours)
        self.assertEqual(ticket.numero[0], "K")
        page = self.client.get(reverse("circuit:bornes"))
        self.assertContains(page, "Accueil → Infirmerie → Kinésithérapie")

    def test_lettre_deja_prise_refusee(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            ajouter_service_borne(self.d.centre, "Kiné", lettre="M")

    def test_retirer_puis_remettre_a_la_borne(self):
        parcours = self.d.parcours["E"]
        url = reverse("circuit:borne_service_basculer", args=[parcours.pk])
        self.client.post(url)
        parcours.refresh_from_db()
        self.assertFalse(parcours.visible_borne)
        self.client.post(url)
        parcours.refresh_from_db()
        self.assertTrue(parcours.visible_borne)

    def test_ajouter_une_borne(self):
        self.client.post(reverse("circuit:borne_ajouter"), {"libelle": "Tablette entrée"})
        self.assertTrue(Borne.objects.filter(centre=self.d.centre, libelle="Tablette entrée").exists())

    def test_un_agent_ne_regle_pas_la_borne(self):
        self.connecter(self.accueil, self.d.centre)
        self.assertEqual(self.client.post(reverse("circuit:borne_service_ajouter"), {"nom": "X"}).status_code, 403)


class AdministrationRangeeTests(BaseVues):
    def test_accueil_range_par_tache(self):
        plateforme = Utilisateur.objects.create_superuser("plateforme", password="mot-de-passe-1")
        self.client.force_login(plateforme)
        page = self.client.get(reverse("admin:index"))
        self.assertContains(page, "Le plus courant")
        self.assertContains(page, "Services de la borne")
        self.assertContains(page, "Consultation et historique")
        self.assertNotContains(page, 'id="nav-sidebar"')
        liste = self.client.get(reverse("admin:parcours_parcours_changelist"))
        self.assertContains(liste, "Chemin du patient")


class NavigationEtSalleTests(BaseVues):
    def test_fil_de_navigation(self):
        admin = self.d.agent("admin_c", "Administrateur de centre")
        self.connecter(admin, self.d.centre)
        page = self.client.get(reverse("comptes:modifier", args=[self.medecin.pk])).content.decode()
        self.assertIn('aria-label="Fil de navigation"', page)
        self.assertIn(f'href="{reverse("comptes:comptes")}"', page)
        self.assertIn(f'href="{reverse("comptes:profil", args=[self.medecin.pk])}"', page)
        self.connecter(self.medecin, self.d.centre)
        guichet = self.client.get(reverse("circuit:guichet", args=[self.d.svc["MED"].pk])).content.decode()
        self.assertIn(f'href="{reverse("circuit:guichets")}"', guichet)
        self.assertIn('<span aria-current="page">Médecin</span>', guichet)

    def test_salle_seulement_pour_les_medecins(self):
        from django.contrib.auth.models import Group

        admin = self.d.agent("admin_c", "Administrateur de centre")
        self.connecter(admin, self.d.centre)
        self.client.post(reverse("comptes:nouveau"), {
            "nom": "Rasoa", "role": Group.objects.get(name="Accueil").pk, "salle": "Salle 9",
        })
        compte = Utilisateur.objects.get(last_name="Rasoa")
        self.assertEqual(AffectationUtilisateur.objects.get(utilisateur=compte).salle, "")
