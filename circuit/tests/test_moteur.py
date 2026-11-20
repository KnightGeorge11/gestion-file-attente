import datetime

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import TestCase

from centres.models import Centre, Service
from circuit import services
from circuit.models import (
    Appel,
    DemandeAutre,
    EntreeFile,
    Evenement,
    Notification,
    Passage,
    ReponseNotification,
    ReservationQuota,
    Ticket,
    Transfert,
)
from circuit.services import ErreurCircuit
from comptes.models import Utilisateur

from .base import LUNDI_MATIN, Donnees, a_heure, maintenant_plus


class BaseCircuit(TestCase):
    def setUp(self):
        self.d = Donnees().construire()
        self.accueil = self.d.agent("accueil1", "Accueil", ["ACC"])
        self.infirmier = self.d.agent("inf1", "Infirmerie", ["INF"])
        self.medecin = self.d.agent("med1", "Médecin", ["MED"])
        self.medecin2 = self.d.agent("med2", "Médecin", ["MED"])
        self.dentiste = self.d.agent("den1", "Dentiste", ["DEN"])
        self.echo = self.d.agent("ech1", "Échographie", ["ECH"])
        self.pharma = self.d.agent("pha1", "Pharmacie", ["PHA"])


class NumerotationTests(BaseCircuit):
    def test_format_et_compteurs_independants_par_prefixe(self):
        m1 = self.d.ticket("M")
        m2 = self.d.ticket("M")
        d1 = self.d.ticket("D")
        i1 = self.d.ticket("I")
        e1 = self.d.ticket("E")
        self.assertEqual([m1.numero, m2.numero, d1.numero, i1.numero, e1.numero],
                         ["M001", "M002", "D001", "I001", "E001"])

    def test_remise_a_zero_chaque_jour(self):
        with a_heure(LUNDI_MATIN):
            self.assertEqual(self.d.ticket("M").numero, "M001")
            self.assertEqual(self.d.ticket("M").numero, "M002")
        with a_heure(LUNDI_MATIN + datetime.timedelta(days=1)):
            self.assertEqual(self.d.ticket("M").numero, "M001")

    def test_jour_calcule_avec_le_fuseau_du_centre(self):
        # 22:30 UTC dimanche = 01:30 lundi à Antananarivo.
        dimanche_soir_utc = datetime.datetime(2026, 11, 15, 22, 30, tzinfo=datetime.timezone.utc)
        with a_heure(dimanche_soir_utc):
            ticket = self.d.ticket("M")
        self.assertEqual(ticket.jour, datetime.date(2026, 11, 16))

    def test_ticket_annule_conserve_son_numero(self):
        t1 = self.d.ticket("M")
        services.annuler_ticket(t1, self.accueil, "Ticket en double")
        t2 = self.d.ticket("M")
        self.assertEqual(t2.numero, "M002")
        t1.refresh_from_db()
        self.assertEqual(t1.statut, Ticket.Statut.ANNULE)

    def test_numerotation_independante_par_centre(self):
        autre = Donnees().construire("ANT")
        self.assertEqual(self.d.ticket("M").numero, "M001")
        self.assertEqual(autre.ticket("M").numero, "M001")

    def test_emission_place_le_patient_a_l_accueil(self):
        ticket = self.d.ticket("D")
        passage = ticket.passages.get()
        self.assertTrue(passage.service.est_accueil)
        self.assertEqual(passage.statut, Passage.Statut.EN_ATTENTE)
        self.assertEqual(ticket.parcours_version, self.d.parcours["D"].version_publiee())
        self.assertEqual(
            list(ticket.evenements.values_list("type", flat=True)),
            [Evenement.Type.EMISSION, Evenement.Type.ENTREE_FILE],
        )

    def test_parcours_non_visible_refuse(self):
        parcours = self.d.parcours["E"]
        parcours.visible_borne = False
        parcours.save()
        with self.assertRaises(ErreurCircuit):
            self.d.ticket("E")


class ParcoursCompletsTests(BaseCircuit):
    def _durees_renseignees(self, ticket):
        for passage in ticket.passages.all():
            self.assertIsNotNone(passage.duree_attente, passage)
            self.assertIsNotNone(passage.duree_prise_en_charge, passage)
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.duree_totale)

    def test_parcours_medecin_sans_pharmacie(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", self.infirmier)
        self.d.traiter(ticket, "MED", self.medecin)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        self.assertEqual(
            list(ticket.passages.values_list("service__code", flat=True)),
            ["ACC", "INF", "MED"],
        )
        self.assertFalse(Transfert.objects.exists())
        self.assertEqual(ticket.passages.get(service__code="MED").pris_en_charge_par, self.medecin)
        self._durees_renseignees(ticket)

    def test_parcours_medecin_avec_pharmacie(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", self.infirmier)
        self.d.traiter(ticket, "MED", self.medecin, transferer=True)
        transfert = Transfert.objects.get()
        self.assertEqual(transfert.decide_par, self.medecin)
        self.assertEqual(transfert.service_destination.code, "PHA")
        self.d.traiter(ticket, "PHA", self.pharma)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        self._durees_renseignees(ticket)

    def test_parcours_dentiste_echographie_infirmerie_avec_et_sans_pharmacie(self):
        for prefixe, code, agent in (("D", "DEN", self.dentiste), ("E", "ECH", self.echo),
                                     ("I", "INF", self.infirmier)):
            with a_heure(LUNDI_MATIN):
                sans = self.d.ticket(prefixe)
                self.d.accueillir(sans, self.accueil)
                self.d.traiter(sans, code, agent)
                avec = self.d.ticket(prefixe)
                self.d.accueillir(avec, self.accueil)
                self.d.traiter(avec, code, agent, transferer=True)
                self.d.traiter(avec, "PHA", self.pharma)
            sans.refresh_from_db()
            avec.refresh_from_db()
            self.assertEqual(sans.statut, Ticket.Statut.TERMINE)
            self.assertEqual(avec.statut, Ticket.Statut.TERMINE)
            self.assertFalse(sans.passages.filter(service__est_pharmacie=True).exists())
            self.assertTrue(avec.passages.filter(service__est_pharmacie=True).exists())

    def test_aucun_transfert_automatique_vers_la_pharmacie(self):
        for prefixe in "MDEI":
            with a_heure(LUNDI_MATIN):
                ticket = self.d.ticket(prefixe)
                self.d.accueillir(ticket, self.accueil)
                for passage_code in {"M": ["INF", "MED"], "D": ["DEN"], "E": ["ECH"], "I": ["INF"]}[prefixe]:
                    agent = {"INF": self.infirmier, "MED": self.medecin, "DEN": self.dentiste,
                             "ECH": self.echo}[passage_code]
                    self.d.traiter(ticket, passage_code, agent)
        self.assertFalse(Passage.objects.filter(service__est_pharmacie=True).exists())
        self.assertFalse(Ticket.objects.filter(statut__in=Ticket.STATUTS_OUVERTS).exists())

    def test_pharmacie_depuis_une_etape_intermediaire_avec_motif(self):
        """Décision du 20/11/2026 : la pharmacie est possible depuis tout service de soins."""
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        # Sans motif : refusé, rien n'est modifié (tout ou rien).
        with self.assertRaises(ErreurCircuit) as erreur:
            self.d.traiter(ticket, "INF", self.infirmier, transferer=True)
        self.assertIn("motif", " ".join(erreur.exception.messages))
        entree = self.d.entree(ticket)
        self.assertEqual(entree.statut, EntreeFile.Statut.EN_COURS)
        self.assertTrue(services.peut_transferer_pharmacie(entree.passage))
        # Avec motif : pharmacie, le médecin n'est pas vu.
        suite = services.terminer(entree, self.infirmier, transferer_pharmacie=True, motif="Renouvellement")
        self.assertTrue(suite.service.est_pharmacie)
        transfert = Transfert.objects.get()
        self.assertEqual((transfert.passage_origine.service.code, transfert.motif), ("INF", "Renouvellement"))
        self.assertFalse(ticket.passages.filter(service__code="MED").exists())
        services.prendre_en_charge(self.d.entree(ticket), self.pharma, oral=True)
        self.assertIsNone(services.terminer(self.d.entree(ticket), self.pharma))
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)

    def test_pharmacie_en_fin_de_parcours_sans_motif(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        self.d.traiter(ticket, "INF", self.infirmier)
        self.d.traiter(ticket, "MED", self.medecin, transferer=True)
        self.assertEqual(Transfert.objects.get().passage_origine.service.code, "MED")

    def test_accueil_et_pharmacie_ne_transferent_pas(self):
        ticket = self.d.ticket("I")
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil)
        services.prendre_en_charge(entree, self.accueil)
        with self.assertRaises(ErreurCircuit):
            services.terminer(entree, self.accueil, transferer_pharmacie=True, motif="x")

    def test_accueil_valide_le_ticket_sans_aucune_information_patient(self):
        ticket = self.d.ticket("M")
        suite = self.d.accueillir(ticket, self.accueil)
        ticket.refresh_from_db()
        self.assertEqual(suite.service.code, "INF")
        self.assertIsNone(ticket.patient)
        self.assertFalse(ticket.evenements.filter(type=Evenement.Type.IDENTIFICATION).exists())

    def test_version_du_parcours_conservee_apres_publication(self):
        ancien = self.d.ticket("I")
        version_initiale = ancien.parcours_version
        brouillon = self.d.parcours["I"].creer_brouillon()
        brouillon.publier()
        nouveau = self.d.ticket("I")
        ancien.refresh_from_db()
        self.assertEqual(ancien.parcours_version, version_initiale)
        self.assertNotEqual(nouveau.parcours_version, version_initiale)


class AppelsTests(BaseCircuit):
    def test_ordre_strict(self):
        t1 = self.d.ticket("M")
        t2 = self.d.ticket("M")
        with self.assertRaises(ErreurCircuit):
            services.appeler(self.d.file("ACC"), self.accueil, entree=self.d.entree(t2))
        appel = services.appeler(self.d.file("ACC"), self.accueil)
        self.assertEqual(appel.ticket, t1)
        self.assertEqual(appel.mode, Appel.Mode.ECRAN)

    def test_appel_d_un_ticket_annule_entre_lecture_et_verrou(self):
        # Audit du 20/11/2026 : la file est lue sans verrou ; si le ticket est
        # annulé entre-temps, l'appel doit être refusé au lieu de le rouvrir.
        from unittest import mock

        ticket = self.d.ticket("M")
        entree = self.d.entree(ticket)
        services.annuler_ticket(ticket, self.accueil, "Annulé pendant l'appel")
        lecture_perimee = mock.patch.object(
            services, "file_en_attente", return_value=EntreeFile.objects.filter(pk=entree.pk)
        )
        with lecture_perimee, self.assertRaises(ErreurCircuit):
            services.appeler(self.d.file("ACC"), self.accueil)
        entree.refresh_from_db()
        self.assertEqual(entree.statut, EntreeFile.Statut.ANNULE)
        self.assertFalse(Appel.objects.filter(ticket=ticket).exists())

    def test_un_agent_ne_tient_qu_un_patient(self):
        self.d.ticket("M")
        self.d.ticket("M")
        file = self.d.file("ACC")
        file.nb_appels_simultanes = 2
        file.save()
        services.appeler(file, self.accueil)
        with self.assertRaises(ErreurCircuit):
            services.appeler(file, self.accueil)

    def test_file_commune_des_medecins(self):
        tickets = []
        for nom in ("A", "B", "C"):
            ticket = self.d.ticket("M")
            self.d.accueillir(ticket, self.accueil)
            self.d.traiter(ticket, "INF", self.infirmier)
            tickets.append(ticket)
        file = self.d.file("MED")
        a1 = services.appeler(file, self.medecin)
        a2 = services.appeler(file, self.medecin2)
        self.assertEqual([a1.ticket, a2.ticket], tickets[:2])
        self.assertEqual(self.d.entree(tickets[0]).agent, self.medecin)
        self.assertEqual(self.d.entree(tickets[1]).agent, self.medecin2)
        # Capacité de la file commune : 2 médecins simultanés.
        troisieme = self.d.agent("med3", "Médecin", ["MED"])
        with self.assertRaises(ErreurCircuit):
            services.appeler(file, troisieme)

    def test_absence_apres_trois_appels_espaces_puis_reintegration(self):
        t1 = self.d.ticket("M")
        self.d.ticket("M")
        file = self.d.file("ACC")
        services.appeler(file, self.accueil)
        entree = self.d.entree(t1)
        with self.assertRaises(ErreurCircuit):
            services.marquer_absent(entree, self.accueil)
        with self.assertRaises(ErreurCircuit):
            services.rappeler(entree, self.accueil)  # délai non écoulé
        with maintenant_plus(61):
            services.rappeler(entree, self.accueil)
        with maintenant_plus(122):
            services.rappeler(entree, self.accueil)
            with self.assertRaises(ErreurCircuit):
                services.rappeler(entree, self.accueil)  # 3 appels maximum
            with self.assertRaises(ErreurCircuit):
                services.marquer_absent(entree, self.accueil)  # délai après le 3e appel
        with maintenant_plus(183):
            services.marquer_absent(entree, self.accueil)
        entree.refresh_from_db()
        self.assertEqual(entree.statut, EntreeFile.Statut.ABSENT)
        self.assertEqual(entree.appels.count(), 3)
        passage = entree.passage
        passage.refresh_from_db()
        self.assertEqual(passage.statut, Passage.Statut.ABSENT)

        nouvelle = services.reintegrer(passage, self.accueil)
        self.assertTrue(nouvelle.reintegration)
        self.assertGreater(nouvelle.rang, EntreeFile.objects.filter(passage__ticket__numero="M002").get().rang)
        # Historique intact : l'ancienne entrée et ses appels sont conservés.
        self.assertEqual(passage.entrees.count(), 2)
        # Maximum d'une réintégration (paramètre du centre).
        file.ordre_strict = False
        file.save()
        services.appeler(file, self.accueil, entree=nouvelle)
        nouvelle.refresh_from_db()
        nouvelle.nb_appels = 3
        nouvelle.date_dernier_appel -= datetime.timedelta(minutes=5)
        nouvelle.save()
        services.marquer_absent(nouvelle, self.accueil)
        passage.refresh_from_db()
        with self.assertRaises(ErreurCircuit):
            services.reintegrer(passage, self.accueil)

    def test_pharmacie_appel_oral(self):
        ticket = self.d.ticket("D")
        with a_heure(LUNDI_MATIN):
            self.d.accueillir(ticket, self.accueil)
            self.d.traiter(ticket, "DEN", self.dentiste, transferer=True)
        entree = self.d.entree(ticket)
        services.prendre_en_charge(entree, self.pharma, oral=True)
        appel = Appel.objects.get(entree=entree)
        self.assertEqual(appel.mode, Appel.Mode.ORAL)
        services.terminer(entree, self.pharma)
        passage = entree.passage
        passage.refresh_from_db()
        self.assertIsNotNone(passage.date_debut)
        self.assertIsNotNone(passage.date_fin)

    def test_appel_oral_refuse_hors_pharmacie(self):
        ticket = self.d.ticket("M")
        with self.assertRaises(ErreurCircuit):
            services.prendre_en_charge(self.d.entree(ticket), self.accueil, oral=True)


class QuotaTests(BaseCircuit):
    def _valider_dentiste(self, **kwargs):
        """L'accueil valide un ticket D : la place de quota est réservée à ce moment."""
        ticket = self.d.ticket("D")
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil, entree=entree)
        services.prendre_en_charge(entree, self.accueil)
        try:
            services.terminer(entree, self.accueil, **kwargs)
        except ErreurCircuit:
            entree.refresh_from_db()
            # Tout ou rien : le ticket est toujours en cours à l'accueil, sans réservation.
            assert entree.statut == "EN_COURS" and ticket.reservation_active() is None
            services.annuler_ticket(ticket, self.accueil, "Quota atteint ou service fermé")
            raise
        return ticket

    def test_quota_matin_puis_blocage_et_derogation(self):
        with a_heure(LUNDI_MATIN):
            for n in range(7):
                self._valider_dentiste()
            quota, reservees, restantes = services.etat_quota(self.d.parcours["D"])
            self.assertEqual((quota.nombre_places, reservees, restantes), (7, 7, 0))
            with self.assertRaises(ErreurCircuit):
                self._valider_dentiste()
            with self.assertRaises(ErreurCircuit):
                self._valider_dentiste(derogation=True, motif_derogation="")
            self._valider_dentiste(derogation=True, motif_derogation="Urgence dentaire")
        reservation = ReservationQuota.objects.get(derogation=True)
        self.assertEqual(reservation.motif_derogation, "Urgence dentaire")
        self.assertTrue(Evenement.objects.filter(type=Evenement.Type.DEROGATION_QUOTA).exists())
        # La dérogation ne modifie pas le quota configuré.
        self.assertEqual(reservation.quota.nombre_places, 7)

    def test_apres_midi_quatre_places(self):
        with a_heure(LUNDI_MATIN.replace(hour=10)):  # 13:00 locale
            quota, _, restantes = services.etat_quota(self.d.parcours["D"])
        self.assertEqual((quota.periode, restantes), ("APRES_MIDI", 4))

    def test_samedi_apres_midi_et_dimanche_fermes(self):
        samedi_14h = datetime.datetime(2026, 11, 21, 11, 0, tzinfo=datetime.timezone.utc)
        dimanche = datetime.datetime(2026, 11, 22, 6, 0, tzinfo=datetime.timezone.utc)
        samedi_matin = datetime.datetime(2026, 11, 21, 6, 0, tzinfo=datetime.timezone.utc)
        for moment in (samedi_14h, dimanche):
            with a_heure(moment):
                with self.assertRaises(ErreurCircuit):
                    self._valider_dentiste()
        with a_heure(samedi_matin):
            self.assertEqual(services.etat_quota(self.d.parcours["D"])[2], 4)

    def test_liberation_avec_motif_obligatoire(self):
        with a_heure(LUNDI_MATIN):
            ticket = self._valider_dentiste()
            with self.assertRaises(ErreurCircuit):
                services.liberer_quota(ticket, self.accueil, " ")
            services.liberer_quota(ticket, self.accueil, "Patient parti")
            self.assertEqual(services.etat_quota(self.d.parcours["D"])[2], 7)
        reservation = ReservationQuota.objects.get()
        self.assertEqual(reservation.statut, ReservationQuota.Statut.LIBEREE)
        self.assertEqual(reservation.liberee_par, self.accueil)

    def test_quota_consomme_a_la_prise_en_charge_dentiste(self):
        with a_heure(LUNDI_MATIN):
            ticket = self._valider_dentiste()
            entree = self.d.entree(ticket)
            services.appeler(self.d.file("DEN"), self.dentiste)
            services.prendre_en_charge(entree, self.dentiste)
            with self.assertRaises(ErreurCircuit):
                services.liberer_quota(ticket, self.accueil, "Trop tard")
        self.assertEqual(ReservationQuota.objects.get().statut, ReservationQuota.Statut.CONSOMMEE)


class ModeAutreTests(BaseCircuit):
    def test_demande_transmise_a_l_agent_designe_et_reponse(self):
        directeur = self.d.agent("directeur", "Direction")
        service_den = self.d.svc["DEN"]
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        self.assertEqual(ticket.numero, "A001")
        demande = ticket.demande
        self.assertEqual(demande.statut, DemandeAutre.Statut.NOUVELLE)

        with self.assertRaises(ErreurCircuit):
            services.transmettre_demande(demande, self.accueil, service=service_den)
        service_den.destinataire_demandes = self.dentiste
        service_den.save()
        notif = services.transmettre_demande(demande, self.accueil, service=service_den, message="RDV")
        self.assertEqual(notif.destinataire, self.dentiste)
        self.assertEqual(Notification.objects.filter(destinataire=self.d.agent("den2", "Dentiste", ["DEN"])).count(), 0)

        with self.assertRaises(ErreurCircuit):
            services.repondre_notification(notif, self.medecin, ReponseNotification.Statut.RECUE)
        services.repondre_notification(notif, self.dentiste, ReponseNotification.Statut.PATIENT_ATTENDU, "Salle 2")
        services.repondre_notification(notif, self.dentiste, ReponseNotification.Statut.TRAITEE)
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeAutre.Statut.CLOTUREE)
        self.assertEqual(notif.derniere_reponse().statut, ReponseNotification.Statut.TRAITEE)

        notif2 = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif).demande
        n = services.transmettre_demande(notif2, self.accueil, destinataire=directeur)
        self.assertEqual(n.destinataire, directeur)

    def test_ticket_autre_cloture_apres_orientation_demande_independante(self):
        directeur = self.d.agent("directeur", "Direction")
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil)
        services.prendre_en_charge(entree, self.accueil)
        # Pas d'identification requise, mais orientation obligatoire avant clôture.
        with self.assertRaises(ErreurCircuit):
            services.terminer(entree, self.accueil)
        notif = services.transmettre_demande(ticket.demande, self.accueil, destinataire=directeur)
        services.terminer(entree, self.accueil)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        self.assertIsNone(ticket.patient)
        # La demande reste suivie après la clôture du ticket.
        demande = ticket.demande
        self.assertEqual(demande.statut, DemandeAutre.Statut.TRANSMISE)
        services.repondre_notification(notif, directeur, ReponseNotification.Statut.RECUE)
        services.repondre_notification(notif, directeur, ReponseNotification.Statut.NON_REALISABLE,
                                       "Directeur absent aujourd'hui")
        demande.refresh_from_db()
        self.assertEqual(demande.statut, DemandeAutre.Statut.CLOTUREE)
        self.assertEqual(notif.reponses.count(), 2)
        self.assertEqual(
            list(ticket.evenements.filter(type__in=["NOTIFICATION", "FIN_TICKET", "REPONSE"])
                 .values_list("type", flat=True)),
            ["NOTIFICATION", "FIN_TICKET", "REPONSE", "REPONSE"],
        )

    def test_ticket_autre_jamais_transfere(self):
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil)
        services.prendre_en_charge(entree, self.accueil)
        self.assertFalse(services.peut_transferer_pharmacie(entree.passage))

    def test_un_service_choisi_sans_agent_designe_et_statut_inconnu(self):
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE)
        with self.assertRaises(ErreurCircuit):
            services.transmettre_demande(ticket.demande, self.accueil)  # ni personne ni service
        with self.assertRaises(ErreurCircuit):
            services.transmettre_demande(ticket.demande, self.accueil, destinataire=self.medecin,
                                         service=self.d.svc["MED"])
        notif = services.transmettre_demande(ticket.demande, self.accueil, destinataire=self.medecin)
        with self.assertRaises(ErreurCircuit):
            services.repondre_notification(notif, self.medecin, "INVENTE")

    def test_destinataire_d_un_autre_centre_refuse(self):
        autre = Donnees().construire("ANT")
        etranger = autre.agent("etranger", "Accueil", ["ACC"])
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE)
        with self.assertRaises(ErreurCircuit):
            services.transmettre_demande(ticket.demande, self.accueil, destinataire=etranger)


class ClotureTests(BaseCircuit):
    def test_cloture_a_20h_des_tickets_du_jour(self):
        avant_20h = LUNDI_MATIN.replace(hour=16, minute=30)   # 19:30 heure du centre
        apres_20h = LUNDI_MATIN.replace(hour=17, minute=5)    # 20:05 heure du centre
        self.assertEqual(str(self.d.centre.heure_cloture_tickets), "20:00:00")
        with a_heure(LUNDI_MATIN):
            ticket = self.d.ticket("M")
            self.d.accueillir(ticket, self.accueil)
        with a_heure(avant_20h):
            self.assertEqual(services.cloturer_tickets(self.d.centre), 0)
        ticket.refresh_from_db()
        self.assertTrue(ticket.ouvert)
        with a_heure(apres_20h):
            self.assertEqual(services.cloturer_tickets(self.d.centre), 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.CLOTURE)
        self.assertEqual(ticket.passages.get(service__code="INF").statut, Passage.Statut.ANNULE)

    def test_heure_de_cloture_reglable_par_centre(self):
        import datetime as dt

        self.d.centre.heure_cloture_tickets = dt.time(18, 0)
        self.d.centre.save()
        with a_heure(LUNDI_MATIN):
            ticket = self.d.ticket("I")
        with a_heure(LUNDI_MATIN.replace(hour=15, minute=10)):   # 18:10 heure du centre
            self.assertEqual(services.cloturer_tickets(self.d.centre), 1)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.CLOTURE)

    def test_cloture_des_tickets_des_jours_precedents(self):
        with a_heure(LUNDI_MATIN):
            vieux = self.d.ticket("M")
            services.appeler(self.d.file("ACC"), self.accueil)
        with a_heure(LUNDI_MATIN + datetime.timedelta(days=1)):
            recent = self.d.ticket("M")
            self.assertEqual(services.cloturer_tickets(self.d.centre), 1)
        vieux.refresh_from_db()
        recent.refresh_from_db()
        self.assertEqual(vieux.statut, Ticket.Statut.CLOTURE)
        self.assertTrue(recent.ouvert)
        self.assertEqual(vieux.passages.get().statut, Passage.Statut.ANNULE)
        # L'agent est libéré.
        self.assertIsNone(services.entree_tenue_par(self.accueil))


    def test_cloture_automatique_sans_commande(self):
        """Les tickets de la veille sont fermés au fil des pages, au plus une fois par minute."""
        from django.core.cache import cache

        cache.clear()
        with a_heure(LUNDI_MATIN):
            vieux = self.d.ticket("M")
            services.appeler(self.d.file("ACC"), self.accueil)
        mardi = LUNDI_MATIN + datetime.timedelta(days=1)
        with a_heure(mardi):
            self.assertEqual(services.cloture_automatique(self.d.centre), 1)
        vieux.refresh_from_db()
        self.assertEqual(vieux.statut, Ticket.Statut.CLOTURE)
        self.assertIsNone(services.entree_tenue_par(self.accueil))
        # Moins d'une minute après : aucune nouvelle recherche.
        with a_heure(LUNDI_MATIN):
            autre = self.d.ticket("M")
        with a_heure(mardi):
            self.assertEqual(services.cloture_automatique(self.d.centre), 0)
        autre.refresh_from_db()
        self.assertTrue(autre.ouvert)
        cache.clear()
        with a_heure(mardi):
            self.assertEqual(services.cloture_automatique(self.d.centre), 1)
        self.assertEqual(services.cloture_automatique(None), 0)

class IntegriteBaseDeDonneesTests(BaseCircuit):
    def test_evenement_en_ajout_seulement(self):
        ticket = self.d.ticket("M")
        evenement = ticket.evenements.first()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Evenement.objects.filter(pk=evenement.pk).update(type="APPEL")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as c:
                    c.execute("DELETE FROM circuit_evenement WHERE id = %s", [evenement.pk])

    def test_trigger_de_coherence_de_centre(self):
        autre = Donnees().construire("ANT")
        ticket = self.d.ticket("M")
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                Passage.objects.filter(ticket=ticket).update(service=autre.svc["INF"])

    def test_contrainte_un_patient_par_agent(self):
        self.d.ticket("M")
        self.d.ticket("M")
        services.appeler(self.d.file("ACC"), self.accueil)
        seconde = EntreeFile.objects.filter(statut="EN_ATTENTE").get()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                EntreeFile.objects.filter(pk=seconde.pk).update(statut="APPELE", agent=self.accueil)

    def test_compte_jamais_supprime_historique_intact(self):
        ticket = self.d.ticket("M")
        services.appeler(self.d.file("ACC"), self.accueil)
        with self.assertRaises(Exception):
            self.accueil.delete()
        self.accueil.is_active = False
        self.accueil.save()
        self.assertEqual(Appel.objects.get(ticket=ticket).agent, self.accueil)
        self.assertTrue(Evenement.objects.filter(agent=self.accueil).exists())


class DelaiEntreAppelsTests(BaseCircuit):
    def test_dix_secondes_entre_deux_appels(self):
        self.assertEqual(self.d.file("ACC").delai_entre_appels_secondes, 10)
        ticket = self.d.ticket("M")
        services.appeler(self.d.file("ACC"), self.accueil)
        entree = self.d.entree(ticket)
        with maintenant_plus(5):
            with self.assertRaises(ErreurCircuit):
                services.rappeler(entree, self.accueil)
        with maintenant_plus(11):
            appel = services.rappeler(entree, self.accueil)
        self.assertEqual(appel.numero_appel, 2)



class DeuxAgentsParServiceTests(BaseCircuit):
    """Décision du 18/11/2026 : toujours au moins 2 agents par service."""

    def test_deux_agents_du_meme_service_appellent_en_meme_temps(self):
        from django.core.exceptions import ValidationError

        accueil2 = self.d.agent("accueil2", "Accueil", ["ACC"])
        accueil3 = self.d.agent("accueil3", "Accueil", ["ACC"])
        t1, t2 = self.d.ticket("M"), self.d.ticket("E")
        self.d.ticket("I")
        for code in ("ACC", "INF", "MED", "DEN", "ECH", "PHA"):
            self.assertGreaterEqual(self.d.file(code).nb_appels_simultanes, 2, code)
        a1 = services.appeler(self.d.file("ACC"), self.accueil, poste="Guichet 1")
        a2 = services.appeler(self.d.file("ACC"), accueil2, poste="Guichet 2")
        self.assertEqual({a1.ticket, a2.ticket}, {t1, t2})
        with self.assertRaises(ErreurCircuit):
            services.appeler(self.d.file("ACC"), accueil3)   # 2 guichets occupés
        file = self.d.file("ACC")
        file.nb_appels_simultanes = 1
        with self.assertRaises(ValidationError):
            file.full_clean()


class ToutPasseParLAccueilTests(BaseCircuit):
    """
    Décision du 19/11/2026 : tout passe d'abord par l'accueil. Seul l'accueil
    appelle et oriente les tickets, y compris les « Autre » ; administrateurs,
    responsables et direction n'appellent aucun ticket.
    """

    def setUp(self):
        super().setUp()
        self.file_acc = self.d.file("ACC")
        self.file_acc.nb_appels_simultanes = 5
        self.file_acc.save()

    def _autre(self):
        return services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)

    def test_responsable_direction_et_administrateur_n_appellent_rien(self):
        patient = self.d.ticket("M")
        autre = self._autre()
        admin = Utilisateur.objects.create_superuser("admin_central", "", "mot-de-passe-1")
        for agent in (self.d.agent("chef_resp", "Responsable"), self.d.agent("dir2", "Direction"), admin):
            for entree in (None, self.d.entree(autre), self.d.entree(patient)):
                with self.assertRaisesMessage(ErreurCircuit, "Tout passe d'abord par l'accueil"):
                    services.appeler(self.file_acc, agent, entree=entree)
        # L'accueil appelle les patients comme les demandes « Autre ».
        self.assertEqual(services.appeler(self.file_acc, self.accueil).ticket, patient)
        accueil2 = self.d.agent("accueil_2", "Accueil", ["ACC"])
        self.assertEqual(services.appeler(self.file_acc, accueil2).ticket, autre)

    def test_un_service_n_appelle_pas_un_patient_que_l_accueil_n_a_pas_oriente(self):
        self.d.ticket("I")                       # pas encore validé par l'accueil
        infirmier = self.d.agent("inf_y", "Infirmerie", ["INF"])
        with self.assertRaisesMessage(ErreurCircuit, "Aucun patient en attente"):
            services.appeler(self.d.file("INF"), infirmier)

    def test_pas_d_appel_oral_d_un_patient_a_la_pharmacie(self):
        ticket = self.d.ticket("I")
        self.d.accueillir(ticket, self.accueil)
        infirmier = self.d.agent("inf_x", "Infirmerie", ["INF"])
        self.d.traiter(ticket, "INF", infirmier, transferer=True)
        responsable = self.d.agent("resp_pha", "Responsable")
        with self.assertRaisesMessage(ErreurCircuit, "Tout passe d'abord par l'accueil"):
            services.prendre_en_charge(self.d.entree(ticket), responsable, oral=True)


class AppelDuVisiteurAutreTests(BaseCircuit):
    """Décision du 19/11/2026 : le destinataire d'une demande « Autre » appelle le visiteur à l'écran."""

    def _orienter(self, **cible):
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE, motif=self.d.motif)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil, entree=entree)
        services.prendre_en_charge(entree, self.accueil)
        notification = services.transmettre_demande(ticket.demande, self.accueil, **cible)
        services.terminer(entree, self.accueil)
        return ticket, notification

    def test_le_destinataire_appelle_le_visiteur_a_l_ecran(self):
        from circuit.views_postes import appels_a_afficher

        directeur = self.d.agent("directeur_v", "Direction")
        with a_heure(LUNDI_MATIN):
            ticket, notification = self._orienter(destinataire=directeur)
            ticket.refresh_from_db()
            self.assertFalse(ticket.ouvert)               # ticket clôturé après l'orientation
            # Seul le destinataire peut appeler le visiteur.
            with self.assertRaisesMessage(ErreurCircuit, "ne vous a pas été transmise"):
                services.appeler_visiteur(notification, self.medecin)
            appel = services.appeler_visiteur(notification, directeur, poste="Bureau 3")
            self.assertEqual((appel.numero_ticket, appel.nom_service, appel.poste), (ticket.numero, "Direction", "Bureau 3"))
            self.assertIn(appel, appels_a_afficher(self.d.centre))
            # 10 s minimum entre deux appels.
            with self.assertRaisesMessage(ErreurCircuit, "Veuillez patienter"):
                services.appeler_visiteur(notification, directeur)
        with a_heure(LUNDI_MATIN + datetime.timedelta(seconds=11)):
            self.assertEqual(services.appeler_visiteur(notification, directeur).numero_appel, appel.numero_appel + 1)
        self.assertTrue(ticket.evenements.filter(type="APPEL", agent=directeur).exists())

    def test_appel_vers_un_service_et_demande_cloturee(self):
        service_den = self.d.svc["DEN"]
        service_den.destinataire_demandes = self.dentiste
        service_den.save()
        with a_heure(LUNDI_MATIN):
            ticket, notification = self._orienter(service=service_den)
            self.assertEqual(services.appeler_visiteur(notification, self.dentiste).nom_service, service_den.nom)
            services.repondre_notification(notification, self.dentiste, ReponseNotification.Statut.TRAITEE)
        with a_heure(LUNDI_MATIN + datetime.timedelta(seconds=30)):
            with self.assertRaisesMessage(ErreurCircuit, "clôturée"):
                services.appeler_visiteur(notification, self.dentiste)
