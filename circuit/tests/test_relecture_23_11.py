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
    def test_un_seul_medecin_en_chef_par_centre(self):
        from django.core.exceptions import ValidationError

        from centres.models import Service

        second = Service(centre=self.d.centre, code="MEC2", nom="Second chef", est_medecin_chef=True)
        with self.assertRaisesMessage(ValidationError, "déjà un médecin en chef"):
            second.full_clean()

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

    def test_envoi_au_medecin_en_chef_pas_compte_comme_pharmacie(self):
        from statistiques.calculs import calculer

        infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", infirmier)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("MED"), self.medecin, entree=entree)
        services.prendre_en_charge(entree, self.medecin)
        services.terminer(entree, self.medecin, transferer_medecin_chef=True)
        stats = calculer([self.d.centre], ticket.jour, ticket.jour)
        self.assertEqual(stats["transferts"], [])


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
