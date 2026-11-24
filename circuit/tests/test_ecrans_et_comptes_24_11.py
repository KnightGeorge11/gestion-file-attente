"""
Décisions du 24/11/2026 :
- plusieurs écrans en même temps, chacun avec ce qu'il affiche, réglé par l'administrateur ;
- comptes connectés et profils pour l'administrateur de la plateforme ;
- infirmerie en relais de l'accueil quand aucun agent d'accueil n'est connecté.
"""

from django.urls import reverse

from centres.models import AppareilEcran, EcranAffichage
from circuit import services
from circuit.views_postes import appels_ecran
from comptes.models import Utilisateur
from comptes.presence import marquer_present

from .test_vues import BaseVues


class EcransReglesParLAdministrateurTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.admin = self.d.agent("admin_centre", "Administrateur de centre")
        self.admin.is_staff = True
        self.admin.save(update_fields=["is_staff"])

    def _appeler(self, code_service, agent):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        if code_service != "ACC":
            entree = self.d.entree(ticket)
            services.appeler(self.d.file(code_service), agent, entree=entree)
        return ticket

    def test_l_admin_cree_un_affichage_avec_ses_services(self):
        self.connecter(self.admin, self.d.centre, salle_indiquee=False)
        self.assertContains(self.client.get(reverse("circuit:ecran_connexion")), "Ajouter un affichage")
        self.client.post(reverse("circuit:ecran_affichage"), {
            "libelle": "TV pharmacie", "services": [self.d.svc["PHA"].pk, self.b.svc["MED"].pk],
            "nb_appels_affiches": "5", "annonce_vocale": "1",
        })
        affichage = EcranAffichage.objects.get(centre=self.d.centre, libelle="TV pharmacie")
        self.assertEqual(list(affichage.services.all()), [self.d.svc["PHA"]])   # service d'un autre centre ignoré
        self.assertEqual(affichage.nb_appels_affiches, 5)
        self.assertTrue(affichage.annonce_vocale)
        self.client.post(reverse("circuit:ecran_affichage"), {"affichage": affichage.pk, "action": "retirer"})
        affichage.refresh_from_db()
        self.assertFalse(affichage.actif)

    def test_un_agent_ne_regle_pas_les_ecrans(self):
        self.connecter(self.accueil, self.d.centre)
        self.assertNotContains(self.client.get(reverse("circuit:ecran_connexion")), "Ajouter un affichage")
        reponse = self.client.post(reverse("circuit:ecran_affichage"), {"libelle": "TV"})
        self.assertEqual(reponse.status_code, 403)

    def test_chaque_ecran_n_affiche_que_ses_services(self):
        pharmacie = EcranAffichage.objects.create(centre=self.d.centre, code="E1", libelle="TV pharmacie")
        pharmacie.services.set([self.d.svc["MED"]])
        ticket = self._appeler("ACC", self.accueil)
        en_cours, precedents = appels_ecran(self.d.centre, 8)
        self.assertTrue(any(a.ticket_id == ticket.pk for a in en_cours + precedents))   # écran sans réglage : tout
        en_cours, precedents = appels_ecran(self.d.centre, 8, list(pharmacie.services.all()))
        self.assertFalse(any(a.ticket_id == ticket.pk for a in en_cours + precedents))  # appel de l'accueil masqué

    def test_ecran_branche_avec_son_affichage(self):
        affichage = EcranAffichage.objects.create(centre=self.d.centre, code="E1", libelle="TV médecins")
        url = reverse("circuit:ecran_salle", args=[self.d.centre.code])
        page = self.client.get(url, {"affichage": affichage.pk})
        self.assertContains(page, f"&affichage={affichage.pk}")
        appels = self.client.get(reverse("circuit:ecran_salle_appels", args=[self.d.centre.code]),
                                 {"affichage": affichage.pk})
        self.assertEqual(appels.status_code, 200)
        # Réglage d'un autre centre : ignoré.
        autre = EcranAffichage.objects.create(centre=self.b.centre, code="E1", libelle="Autre")
        self.assertNotContains(self.client.get(url, {"affichage": autre.pk}), f"&affichage={autre.pk}")

    def test_tv_wifi_avec_son_affichage(self):
        from django.utils import timezone

        affichage = EcranAffichage.objects.create(centre=self.d.centre, code="E1", libelle="TV médecins")
        tv = AppareilEcran.objects.create(jeton="j", code="1234", derniere_activite=timezone.now())
        self.connecter(self.admin, self.d.centre, salle_indiquee=False)
        self.client.post(reverse("circuit:ecran_wifi_choisir"),
                         {"appareil": tv.pk, "action": "afficher", "affichage": affichage.pk})
        tv.refresh_from_db()
        self.assertEqual((tv.centre, tv.affichage), (self.d.centre, affichage))
        self.client.post(reverse("circuit:ecran_wifi_choisir"), {"appareil": tv.pk, "action": "regler", "affichage": ""})
        tv.refresh_from_db()
        self.assertIsNone(tv.affichage)
        # Un agent peut afficher les appels sur une TV, mais pas choisir ce qu'elle affiche.
        tv.centre = None
        tv.save()
        self.connecter(self.accueil, self.d.centre)
        self.client.post(reverse("circuit:ecran_wifi_choisir"),
                         {"appareil": tv.pk, "action": "afficher", "affichage": affichage.pk})
        tv.refresh_from_db()
        self.assertEqual((tv.centre, tv.affichage), (self.d.centre, None))
        self.assertEqual(self.client.post(reverse("circuit:ecran_wifi_choisir"),
                                          {"appareil": tv.pk, "action": "regler", "affichage": affichage.pk}).status_code, 403)


class ComptesConnectesEtProfilsTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.plateforme = Utilisateur.objects.create_user(
            username="plateforme", password="x", type_compte=Utilisateur.TypeCompte.ADMINISTRATEUR, is_staff=True
        )

    def test_liste_des_comptes_connectes_et_profil(self):
        marquer_present(self.medecin, self.d.centre)
        self.client.force_login(self.plateforme)
        page = self.client.get(reverse("comptes:comptes"))
        self.assertEqual(page.status_code, 200)
        self.assertIn(self.medecin, [l["utilisateur"] for l in page.context["lignes"]])
        self.assertNotIn(self.pharma, [l["utilisateur"] for l in page.context["lignes"]])
        tous = self.client.get(reverse("comptes:comptes"), {"vue": "tous"})
        self.assertIn(self.pharma, [l["utilisateur"] for l in tous.context["lignes"]])
        profil = self.client.get(reverse("comptes:profil", args=[self.medecin.pk]))
        self.assertContains(profil, "Connecté")
        self.assertContains(profil, self.d.centre.nom)

    def test_reserve_a_l_administrateur_de_la_plateforme(self):
        admin_centre = self.d.agent("admin_c", "Administrateur de centre")
        for utilisateur in (self.accueil, admin_centre):
            self.client.force_login(utilisateur)
            self.assertEqual(self.client.get(reverse("comptes:comptes")).status_code, 403)
            self.assertEqual(self.client.get(reverse("comptes:profil", args=[self.medecin.pk])).status_code, 403)


class InfirmerieEnRelaisDeLAccueilTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])

    def test_l_infirmerie_appelle_et_valide_a_l_accueil(self):
        ticket = self.d.ticket("M")
        self.connecter(self.infirmier, self.d.centre)
        page = self.client.get(reverse("circuit:guichets"))
        self.assertContains(page, "Aucun agent d'accueil n'est connecté")
        acc = self.d.svc["ACC"].pk
        self.assertEqual(self.client.get(reverse("circuit:guichet", args=[acc])).status_code, 200)
        self.client.post(reverse("circuit:guichet_action", args=[acc, "appeler"]))
        entree = self.d.entree(ticket)
        self.assertEqual(entree.agent, self.infirmier)
        self.client.post(reverse("circuit:guichet_action", args=[acc, "prendre"]), {"entree": entree.pk})
        self.client.post(reverse("circuit:guichet_action", args=[acc, "terminer"]), {"entree": entree.pk})
        self.assertEqual(self.d.entree(ticket).file.service.code, "INF")         # validé et orienté
        self.assertEqual(self.client.get(reverse("circuit:demandes")).status_code, 200)

    def test_plus_de_relais_quand_l_accueil_est_connecte(self):
        marquer_present(self.accueil, self.d.centre)
        self.connecter(self.infirmier, self.d.centre)
        self.assertNotContains(self.client.get(reverse("circuit:guichets")), "Aucun agent d'accueil n'est connecté")
        self.assertEqual(self.client.get(reverse("circuit:guichet", args=[self.d.svc["ACC"].pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("circuit:demandes")).status_code, 403)
