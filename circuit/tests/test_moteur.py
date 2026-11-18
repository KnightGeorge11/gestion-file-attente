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
                self.d.accueillir(sans, self.accueil, patient=self.d.patient(f"SANS{prefixe}"))
                self.d.traiter(sans, code, agent)
                avec = self.d.ticket(prefixe)
                self.d.accueillir(avec, self.accueil, patient=self.d.patient(f"AVEC{prefixe}"))
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
                self.d.accueillir(ticket, self.accueil, patient=self.d.patient(f"P{prefixe}"))
                for passage_code in {"M": ["INF", "MED"], "D": ["DEN"], "E": ["ECH"], "I": ["INF"]}[prefixe]:
                    agent = {"INF": self.infirmier, "MED": self.medecin, "DEN": self.dentiste,
                             "ECH": self.echo}[passage_code]
                    self.d.traiter(ticket, passage_code, agent)
        self.assertFalse(Passage.objects.filter(service__est_pharmacie=True).exists())
        self.assertFalse(Ticket.objects.filter(statut__in=Ticket.STATUTS_OUVERTS).exists())

    def test_transfert_depuis_etape_intermediaire_exige_un_motif(self):
        ticket = self.d.ticket("M")
        self.d.accueillir(ticket, self.accueil)
        with self.assertRaises(ErreurCircuit):
            self.d.traiter(ticket, "INF", self.infirmier, transferer=True)
        # Rien n'a été modifié (tout ou rien).
        entree = self.d.entree(ticket)
        self.assertEqual(entree.statut, EntreeFile.Statut.EN_COURS)
        services.terminer(entree, self.infirmier, transferer_pharmacie=True, motif="Renouvellement")
        self.d.traiter(ticket, "PHA", self.pharma)
        ticket.refresh_from_db()
        self.assertEqual(ticket.statut, Ticket.Statut.TERMINE)
        self.assertFalse(ticket.passages.filter(service__code="MED").exists())

    def test_accueil_et_pharmacie_ne_transferent_pas(self):
        ticket = self.d.ticket("I")
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil)
        services.prendre_en_charge(entree, self.accueil)
        services.identifier_patient(ticket, self.d.patient(), self.accueil)
        with self.assertRaises(ErreurCircuit):
            services.terminer(entree, self.accueil, transferer_pharmacie=True, motif="x")

    def test_identification_obligatoire_avant_de_quitter_l_accueil(self):
        ticket = self.d.ticket("M")
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil)
        services.prendre_en_charge(entree, self.accueil)
        with self.assertRaises(ErreurCircuit):
            services.terminer(entree, self.accueil)

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
            self.d.accueillir(ticket, self.accueil, patient=self.d.patient(nom))
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
    def _identifier_dentiste(self, n, **kwargs):
        ticket = self.d.ticket("D")
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil, entree=entree)
        services.prendre_en_charge(entree, self.accueil)
        try:
            services.identifier_patient(ticket, self.d.patient(f"PATIENT{n}"), self.accueil, **kwargs)
        except ErreurCircuit:
            # Libère le guichet avant de propager l'erreur.
            services.annuler_ticket(ticket, self.accueil, "Identification impossible")
            raise
        services.terminer(entree, self.accueil)
        return ticket

    def test_quota_matin_puis_blocage_et_derogation(self):
        with a_heure(LUNDI_MATIN):
            for n in range(7):
                self._identifier_dentiste(n)
            quota, reservees, restantes = services.etat_quota(self.d.parcours["D"])
            self.assertEqual((quota.nombre_places, reservees, restantes), (7, 7, 0))
            with self.assertRaises(ErreurCircuit):
                self._identifier_dentiste(8)
            with self.assertRaises(ErreurCircuit):
                self._identifier_dentiste(9, derogation=True, motif_derogation="")
            self._identifier_dentiste(10, derogation=True, motif_derogation="Urgence dentaire")
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
                    self._identifier_dentiste(1)
        with a_heure(samedi_matin):
            self.assertEqual(services.etat_quota(self.d.parcours["D"])[2], 4)

    def test_liberation_avec_motif_obligatoire(self):
        with a_heure(LUNDI_MATIN):
            ticket = self._identifier_dentiste(1)
            with self.assertRaises(ErreurCircuit):
                services.liberer_quota(ticket, self.accueil, " ")
            services.liberer_quota(ticket, self.accueil, "Patient parti")
            self.assertEqual(services.etat_quota(self.d.parcours["D"])[2], 7)
        reservation = ReservationQuota.objects.get()
        self.assertEqual(reservation.statut, ReservationQuota.Statut.LIBEREE)
        self.assertEqual(reservation.liberee_par, self.accueil)

    def test_quota_consomme_a_la_prise_en_charge_dentiste(self):
        with a_heure(LUNDI_MATIN):
            ticket = self._identifier_dentiste(1)
            entree = self.d.entree(ticket)
            services.appeler(self.d.file("DEN"), self.dentiste)
            services.prendre_en_charge(entree, self.dentiste)
            with self.assertRaises(ErreurCircuit):
                services.liberer_quota(ticket, self.accueil, "Trop tard")
        self.assertEqual(ReservationQuota.objects.get().statut, ReservationQuota.Statut.CONSOMMEE)


class PatientsTicketsTests(BaseCircuit):
    def test_un_seul_ticket_ouvert_par_patient(self):
        patient = self.d.patient()
        t1 = self.d.ticket("M")
        self.d.accueillir(t1, self.accueil, patient=patient)
        t2 = self.d.ticket("I")
        entree = self.d.entree(t2)
        services.appeler(self.d.file("ACC"), self.accueil)
        services.prendre_en_charge(entree, self.accueil)
        with self.assertRaises(ErreurCircuit):
            services.identifier_patient(t2, patient, self.accueil)
        services.annuler_ticket(t2, self.accueil, "Ticket supplémentaire")
        t2.refresh_from_db()
        self.assertEqual(t2.statut, Ticket.Statut.ANNULE)

    def test_un_seul_ticket_ouvert_tous_centres_confondus(self):
        autre = Donnees().construire("ANT")
        accueil_b = autre.agent("accueil_b", "Accueil", ["ACC"])
        patient = self.d.patient()
        t1 = self.d.ticket("M")
        self.d.accueillir(t1, self.accueil, patient=patient)
        t2 = autre.ticket("I")
        entree = autre.entree(t2)
        services.appeler(autre.file("ACC"), accueil_b)
        services.prendre_en_charge(entree, accueil_b)
        with self.assertRaises(ErreurCircuit) as erreur:
            services.identifier_patient(t2, patient, accueil_b)
        message = " ".join(erreur.exception.messages)
        self.assertIn("autre centre", message)
        self.assertNotIn(t1.numero, message)  # aucun numéro de l'autre centre révélé
        # Une fois le premier ticket terminé, l'identification est possible.
        services.annuler_ticket(t1, self.accueil, "Fin de journée")
        services.identifier_patient(t2, patient, accueil_b)

    def test_identification_cree_le_rattachement_au_centre(self):
        patient = self.d.patient()
        t1 = self.d.ticket("I")
        self.d.accueillir(t1, self.accueil, patient=patient)
        self.assertEqual(patient.rattachements.get().matricule, "TAM-000001")


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

    def test_ticket_autre_jamais_transfere_ni_identifie(self):
        ticket = services.emettre_ticket(self.d.centre, Ticket.Mode.AUTRE)
        entree = self.d.entree(ticket)
        services.appeler(self.d.file("ACC"), self.accueil)
        services.prendre_en_charge(entree, self.accueil)
        with self.assertRaises(ErreurCircuit):
            services.identifier_patient(ticket, self.d.patient(), self.accueil)
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
