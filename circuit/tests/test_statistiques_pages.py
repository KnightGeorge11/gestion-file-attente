"""
Pages de statistiques du 24/11/2026 : tableau du jour en direct, affluence
par heure, évolution de l'attente, demandes et médecin en chef, quotas par
période.
"""

import datetime

from django.urls import reverse
from django.utils import timezone

from circuit import services
from circuit.models import MotifDemande, Ticket
from statistiques import calculs

from .test_vues import BaseVues


class PagesStatistiquesTests(BaseVues):
    def setUp(self):
        super().setUp()
        self.direction = self.d.agent("direction1", "Direction")
        self.jour = timezone.localdate()

    def test_tableau_du_jour_en_direct(self):
        premier = self.d.ticket("M")
        self.d.ticket("M")
        self.d.accueillir(premier, self.accueil)
        self.connecter(self.direction, self.d.centre, salle_indiquee=False)
        page = self.client.get(reverse("statistiques:direct"))
        self.assertEqual(page.status_code, 200)
        direct = page.context["direct"]
        self.assertEqual(direct["tickets"], 2)
        self.assertEqual(direct["en_attente"], 2)            # un à l'accueil, un à l'infirmerie
        self.assertIsNotNone(direct["plus_longue"])
        self.assertContains(page, "Patients en attente par service")

    def test_direct_reserve_a_qui_voit_les_statistiques(self):
        self.connecter(self.medecin, self.d.centre)
        self.assertEqual(self.client.get(reverse("statistiques:direct")).status_code, 403)

    def test_direct_limite_au_centre_actif(self):
        self.b.ticket("M")
        self.assertEqual(calculs.direct(self.d.centre)["tickets"], 0)

    def test_affluence_par_jour_et_par_heure(self):
        ticket = self.d.ticket("M")
        resultat = calculs.affluence([self.d.centre], self.jour, self.jour, self.d.centre.fuseau_horaire)
        total = sum(l["total"] for l in resultat["lignes"])
        self.assertEqual(total, 1)
        self.assertEqual(resultat["pointe"]["n"], 1)
        heure = timezone.localtime(ticket.date_emission, calculs._fuseau(self.d.centre.fuseau_horaire)).hour
        self.assertEqual(resultat["pointe"]["heure"], heure)

    def test_evolution_garde_les_jours_sans_activite(self):
        hier = self.d.ticket("M")
        Ticket.objects.filter(pk=hier.pk).update(jour=self.jour - datetime.timedelta(days=2))
        self.d.ticket("M")
        evolution = calculs.evolution([self.d.centre], self.jour - datetime.timedelta(days=6), self.jour)
        self.assertEqual(len(evolution["points"]), 3)        # avant-hier, hier (vide), aujourd'hui
        self.assertEqual(evolution["points"][1]["tickets"], 0)
        self.assertIsNone(calculs.evolution([self.d.centre], self.jour, self.jour))

    def test_demandes_delai_de_reponse_et_sans_reponse(self):
        motif = MotifDemande.objects.filter(centre=self.d.centre).first()
        un = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=motif)
        deux = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=motif)
        notif = services.transmettre_demande(un.demande, self.accueil, destinataire=self.direction)
        services.transmettre_demande(deux.demande, self.accueil, destinataire=self.direction)
        services.repondre_notification(notif, self.direction, "RECUE", "Vu")
        resultat = calculs.demandes([self.d.centre], self.jour, self.jour)
        self.assertEqual(resultat["demandes_autre"], 2)
        self.assertEqual(resultat["transmises"], 2)
        self.assertEqual(resultat["sans_reponse"], 1)
        self.assertEqual(resultat["vers_chef"], 0)
        self.assertIsNotNone(resultat["delai_moy"])
        self.assertEqual(resultat["par_destinataire"][0]["recues"], 2)

    def test_page_statistiques_affiche_les_nouvelles_sections(self):
        self.d.ticket("M")
        self.connecter(self.direction, self.d.centre, salle_indiquee=False)
        page = self.client.get(reverse("statistiques:tableau"))
        for titre in ("Temps par service", "Affluence par jour et par heure",
                      "Demandes « Autre » et médecin en chef", "Aujourd'hui en direct", "30 jours"):
            self.assertContains(page, titre)
        # Courbe : nombres au format SVG (point décimal), même en français.
        du = (self.jour - datetime.timedelta(days=3)).isoformat()
        Ticket.objects.filter(centre=self.d.centre).update(jour=self.jour - datetime.timedelta(days=3))
        self.d.ticket("M")
        page = self.client.get(reverse("statistiques:tableau"), {"du": du, "au": self.jour.isoformat()})
        self.assertContains(page, "Évolution de l'attente moyenne")
        self.assertNotRegex(page.content.decode().split('class="courbe"')[1].split("</svg>")[0], r'(x|y|width|height)="\d+,\d')
