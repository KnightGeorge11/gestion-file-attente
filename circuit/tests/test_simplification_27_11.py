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


class ReglagesTests(BaseVues):
    """27/11/2026 : motifs, imprimantes, places du dentiste et centres sans l'administration."""

    def setUp(self):
        super().setUp()
        self.admin = self.d.agent("admin_c", "Administrateur de centre")
        self.connecter(self.admin, self.d.centre)

    def test_page_et_onglet(self):
        page = self.client.get(reverse("centres:reglages"))
        self.assertContains(page, "Motifs « Autre » de la borne")
        self.assertContains(page, "Places « Dentiste » par demi-journée")
        self.assertNotContains(page, "Nouveau centre")            # plateforme seulement
        self.assertContains(self.client.get(reverse("circuit:guichets")), reverse("centres:reglages"))
        self.connecter(self.accueil, self.d.centre)
        self.assertEqual(self.client.get(reverse("centres:reglages")).status_code, 403)

    def test_motif_ajoute_puis_retire(self):
        from circuit.models import MotifDemande

        self.client.post(reverse("centres:reglages_motif"), {"libelle": "Retrait de résultats"})
        motif = MotifDemande.objects.get(centre=self.d.centre, libelle="Retrait de résultats")
        self.client.post(reverse("centres:reglages_motif"), {"motif": motif.pk})
        motif.refresh_from_db()
        self.assertFalse(motif.actif)

    def test_imprimante_ajoutee_puis_choisie_pour_une_borne(self):
        from centres.models import ImprimanteTicket

        self.client.post(reverse("centres:reglages_imprimante"), {"libelle": "Accueil", "adresse_ip": "pas une ip"})
        self.assertFalse(ImprimanteTicket.objects.exists())
        self.client.post(reverse("centres:reglages_imprimante"),
                         {"libelle": "Accueil", "adresse_ip": "192.168.1.50", "papier": "58"})
        imprimante = ImprimanteTicket.objects.get(centre=self.d.centre)
        self.assertEqual((imprimante.port, imprimante.largeur_caracteres), (9100, 32))
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Tablette")
        self.client.post(reverse("circuit:borne_imprimante", args=[borne.pk]), {"imprimante": imprimante.pk})
        borne.refresh_from_db()
        self.assertEqual(borne.imprimante, imprimante)
        self.client.post(reverse("circuit:borne_imprimante", args=[borne.pk]), {"imprimante": ""})
        borne.refresh_from_db()
        self.assertIsNone(borne.imprimante)

    def test_places_du_dentiste(self):
        from centres.reglages import grille_quotas
        from core.timeutils import maintenant_centre

        dentiste = self.d.parcours["D"]
        aujourd_hui = maintenant_centre(self.d.centre).date()
        donnees = {}
        for ligne in grille_quotas(dentiste, aujourd_hui):
            for periode in ("MATIN", "APRES_MIDI"):
                valeur = ligne[periode]
                donnees[f"q-{ligne['jour']}-{periode}"] = "" if valeur is None else str(valeur)
        self.assertEqual(donnees["q-0-MATIN"], "7")
        donnees["q-0-MATIN"] = "5"        # lundi matin : 5 places
        donnees["q-5-MATIN"] = ""         # samedi matin : fermé
        donnees["q-6-MATIN"] = "3"        # dimanche matin : ouvert
        self.client.post(reverse("centres:reglages_quotas", args=[dentiste.pk]), donnees)
        grille = grille_quotas(dentiste, aujourd_hui)
        self.assertEqual((grille[0]["MATIN"], grille[5]["MATIN"], grille[6]["MATIN"]), (5, None, 3))
        self.assertEqual(grille[1]["MATIN"], 7)                                   # inchangé
        # Le passé garde ses places (statistiques).
        hier = grille_quotas(dentiste, aujourd_hui - __import__("datetime").timedelta(days=1))
        self.assertEqual(hier[0]["MATIN"], 7)

    def test_nouveau_centre_par_la_plateforme(self):
        from centres.models import Centre, Service
        from parcours.models import Parcours

        self.assertEqual(self.client.post(reverse("centres:reglages_nouveau_centre"),
                                          {"nom": "X", "code": "XX"}).status_code, 403)
        plateforme = Utilisateur.objects.create_superuser("plateforme", password="mot-de-passe-1")
        self.connecter(plateforme, self.d.centre)
        self.client.post(reverse("centres:reglages_nouveau_centre"), {"nom": "Majunga", "code": "maj"})
        centre = Centre.objects.get(code="MAJ")
        self.assertTrue(Service.objects.filter(centre=centre, est_accueil=True).exists())
        self.assertEqual(Parcours.objects.filter(centre=centre).count(), 4)
        from centres.access import SESSION_CENTRE_KEY

        self.assertEqual(self.client.session[SESSION_CENTRE_KEY], centre.pk)   # devient le centre actif
        # Code déjà pris : refusé.
        self.client.post(reverse("centres:reglages_nouveau_centre"), {"nom": "Autre", "code": "MAJ"})
        self.assertEqual(Centre.objects.filter(code="MAJ").count(), 1)


class ServiceNonPublieTests(BaseVues):
    def test_service_sans_chemin_publie_absent_de_la_borne(self):
        from parcours.models import Parcours

        brouillon = Parcours.objects.create(centre=self.d.centre, code="KINE", prefixe="K", libelle_borne="Kiné")
        borne = Borne.objects.create(centre=self.d.centre, code="B1", libelle="Tablette")
        self.assertNotIn(brouillon, borne.parcours_proposes())
        self.assertIn(self.d.parcours["M"], borne.parcours_proposes())
        admin = self.d.agent("admin_c", "Administrateur de centre")
        self.connecter(admin, self.d.centre)
        self.assertContains(self.client.get(reverse("circuit:bornes")), "chemin pas encore publié")


class FormulairesAdministrationSimplesTests(BaseVues):
    """27/11/2026 : formulaires de l'administration simplifiés (l'essentiel visible, le reste replié)."""

    def test_tous_les_formulaires_s_ouvrent(self):
        from django.contrib import admin as admin_django

        from centres.models import Borne, EcranAffichage
        from comptes.models import AffectationUtilisateur

        plateforme = Utilisateur.objects.create_superuser("plateforme", password="mot-de-passe-1")
        self.client.force_login(plateforme)
        Borne.objects.create(centre=self.d.centre, code="B1", libelle="Tablette")
        EcranAffichage.objects.create(centre=self.d.centre, code="E1", libelle="TV")
        for modele, model_admin in admin_django.site._registry.items():
            nom = f"admin:{modele._meta.app_label}_{modele._meta.model_name}"
            objet = modele.objects.first()
            if model_admin.has_add_permission(self._requete(plateforme)):
                self.assertEqual(self.client.get(reverse(f"{nom}_add")).status_code, 200, nom)
            if objet is not None:
                self.assertEqual(self.client.get(reverse(f"{nom}_change", args=[objet.pk])).status_code, 200, nom)

        affectation = AffectationUtilisateur.objects.filter(centre=self.d.centre).first()
        page = self.client.get(reverse("admin:comptes_affectationutilisateur_change", args=[affectation.pk]))
        self.assertNotContains(page, "Can add log entry")              # plus la liste technique de Django
        self.assertNotContains(page, 'name="role"')                    # plus l'ancien champ Agent / Responsable
        self.assertContains(page, "Avancé : droits en plus du rôle")
        self.assertContains(page, 'type="checkbox" name="permissions"')

    def _requete(self, utilisateur):
        from django.test import RequestFactory

        requete = RequestFactory().get("/")
        requete.user = utilisateur
        return requete


class SallesMedecinsSeulementTests(BaseVues):
    """27/11/2026 : la salle ne concerne que les médecins."""

    def test_pas_de_salle_hors_medecins(self):
        affectation = AffectationUtilisateur.objects.get(utilisateur=self.accueil, centre=self.d.centre)
        affectation.salle = "Bureau 2"
        affectation.save()
        affectation.refresh_from_db()
        self.assertEqual(affectation.salle, "")
        medecin = AffectationUtilisateur.objects.get(utilisateur=self.medecin, centre=self.d.centre)
        medecin.salle = "Salle 1"
        medecin.save()
        medecin.refresh_from_db()
        self.assertEqual(medecin.salle, "Salle 1")

    def test_guichet_et_ma_salle(self):
        self.connecter(self.accueil, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk]))
        self.assertNotContains(page, 'name="poste"')
        self.assertNotContains(page, "indiquer ma salle")
        self.assertRedirects(self.client.get(reverse("circuit:ma_salle")), reverse("circuit:guichets"),
                             fetch_redirect_response=False)
        # Un poste envoyé quand même n'est pas annoncé.
        ticket = self.d.ticket("M")
        self.client.post(reverse("circuit:guichet_action", args=[self.d.svc["ACC"].pk, "appeler"]),
                         {"poste": "Bureau 2"})
        from circuit.models import Appel

        self.assertEqual(Appel.objects.filter(ticket=ticket).latest("date").poste, "")
        self.connecter(self.medecin, self.d.centre)
        page = self.client.get(reverse("circuit:guichet", args=[self.d.svc["MED"].pk]))
        self.assertContains(page, 'name="poste"')
