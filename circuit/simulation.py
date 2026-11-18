"""
Simulation de patients (« clients ») pour tester et démontrer le système.

La simulation passe uniquement par le moteur réel (circuit.services) :
toutes les règles s'appliquent (quotas, ordre des files, un patient par
agent, transferts explicites…). Les actions sont faites par les comptes de
test (commande creer_comptes_test).

Les données créées sont réelles (tickets, passages, événements) et ne
peuvent pas être supprimées (historique en ajout seulement) : à utiliser
sur une base de développement ou de test, jamais en production.
"""

import random

from django.db.models import Q

from centres.models import Borne, Service
from comptes.management.commands.creer_comptes_test import identifiants_test
from comptes.models import Utilisateur
from core.timeutils import maintenant_centre
from parcours.models import Parcours
from patients.models import Entreprise, Patient, PatientCentre

from . import services
from .models import (
    DemandeAutre,
    EntreeFile,
    MotifDemande,
    Notification,
    ReponseNotification,
    Ticket,
)
from .services import ErreurCircuit

ENTREPRISE_SIMULATION = "ENTREPRISE SIMULATION"
NOMS = ["RAKOTO", "RABE", "RANDRIA", "RAZAFY", "RASOA", "ANDRIAMANANTENA", "RAHARISON", "RAVELO",
        "RAMAROSON", "RAJAONARY", "RAKOTONIRINA", "RANAIVO", "RAFALIMANANA", "ANDRIANJAKA", "RATSIMBA"]
PRENOMS = ["Hery", "Lova", "Tiana", "Fara", "Mialy", "Nirina", "Tojo", "Haja", "Voahirana", "Fanja",
           "Toky", "Soa", "Rivo", "Onja", "Mamy", "Zo", "Faly", "Ny Aina"]
MOTIFS_AUTRE = ["Rendez-vous", "Cotisations (paiement ou renseignement)", "Rencontrer une personne du centre"]


class Simulateur:
    def __init__(self, centre, graine=None, journal=None):
        self.centre = centre
        self.hasard = random.Random(graine)
        self.journal = journal or (lambda texte: None)
        self.agents = self._agents_par_service()
        if not any(self.agents.values()):
            raise ErreurCircuit(
                "Aucun compte de test dans ce centre : lancez d'abord « python manage.py creer_comptes_test »."
            )
        self.files = {code: service.file_par_defaut() for code, service in self.services.items()}
        # Les tickets simulés sont émis par une borne dédiée : la simulation
        # n'agit QUE sur ses propres tickets et QUE avec les comptes de test.
        self.borne, _ = Borne.objects.get_or_create(
            centre=centre, code="SIMULATION",
            defaults={"libelle": "Borne de simulation (tests)", "cle_hash": "!"},
        )
        self.ids_agents_test = {a.pk for liste in self.agents.values() for a in liste}

    # -- préparation ---------------------------------------------------------

    def _agents_par_service(self):
        self.services = {s.code: s for s in Service.objects.filter(centre=self.centre, actif=True)}
        agents = {}
        for code, service in self.services.items():
            agents[code] = list(
                Utilisateur.objects.filter(
                    username__in=identifiants_test(),
                    is_active=True,
                    affectations__centre=self.centre,
                    affectations__actif=True,
                    affectations__services_autorises__service=service,
                ).order_by("username")
            )
        return agents

    def creer_patients(self, nombre):
        entreprise, _ = Entreprise.objects.get_or_create(nom=ENTREPRISE_SIMULATION)
        for motif in MOTIFS_AUTRE:
            MotifDemande.objects.get_or_create(centre=self.centre, libelle=motif)
        crees = 0
        existants = Patient.objects.filter(entreprise=entreprise).count()
        for i in range(existants, existants + nombre):
            patient = Patient.objects.create(
                nom=f"{self.hasard.choice(NOMS)}",
                prenom=f"{self.hasard.choice(PRENOMS)} {i + 1}",
                sexe=self.hasard.choice(["Masculin", "Féminin"]),
                entreprise=entreprise,
                matricule_entreprise=f"SIM-{i + 1:04d}",
            )
            PatientCentre.objects.create(patient=patient, centre=self.centre)
            crees += 1
        return crees

    def _patient_libre(self):
        libres = (
            Patient.objects.filter(entreprise__nom=ENTREPRISE_SIMULATION, fusionne_dans__isnull=True)
            .exclude(tickets__statut__in=Ticket.STATUTS_OUVERTS)
            .order_by("?")
        )
        return libres.first()

    def _agent_libre(self, code):
        for agent in self.hasard.sample(self.agents.get(code, []), len(self.agents.get(code, []))):
            if services.entree_tenue_par(agent) is None:
                return agent
        return None

    # -- actions -------------------------------------------------------------

    def emettre(self, nombre):
        parcours = list(Parcours.objects.filter(centre=self.centre, actif=True, visible_borne=True))
        motifs = list(MotifDemande.objects.filter(centre=self.centre, actif=True))
        emis = []
        for _ in range(nombre):
            tirage = self.hasard.random()
            try:
                if tirage < 0.1:
                    ticket = services.emettre_ticket(
                        self.centre, Ticket.Mode.AUTRE, borne=self.borne,
                        motif=self.hasard.choice(motifs) if motifs else None,
                    )
                else:
                    mode = Ticket.Mode.INTERNE if tirage < 0.55 else Ticket.Mode.EXTERNE
                    ticket = services.emettre_ticket(
                        self.centre, mode, parcours=self.hasard.choice(parcours), borne=self.borne
                    )
            except ErreurCircuit as erreur:
                self.journal(f"Émission impossible : {' '.join(erreur.messages)}")
                continue
            emis.append(ticket)
            self.journal(f"Borne : ticket {ticket.numero} ({ticket.get_mode_display()})")
        return emis

    def _terminer(self, entree, agent):
        passage = entree.passage
        ticket = passage.ticket
        service = passage.service
        if service.est_accueil:
            if ticket.est_autre:
                demande = DemandeAutre.objects.get(ticket=ticket)
                if demande.statut == DemandeAutre.Statut.NOUVELLE:
                    cible = self.hasard.choice([s for s in self.services.values()
                                                if s.destinataire_demandes_id] or [None])
                    if cible is None:
                        services.annuler_ticket(ticket, agent, "Simulation : aucun destinataire désigné")
                        return
                    services.transmettre_demande(demande, agent, service=cible, message="Demande simulée")
                    self.journal(f"Accueil : demande {ticket.numero} transmise ({cible.nom})")
            elif ticket.patient_id is None:
                patient = self._patient_libre()
                try:
                    if patient is None:
                        raise ErreurCircuit("Aucun patient simulé disponible.")
                    services.identifier_patient(ticket, patient, agent)
                    self.journal(f"Accueil : {ticket.numero} identifié ({patient})")
                except ErreurCircuit as erreur:
                    services.annuler_ticket(ticket, agent, f"Simulation : {' '.join(erreur.messages)}")
                    self.journal(f"Accueil : {ticket.numero} annulé ({' '.join(erreur.messages)})")
                    return
        transferer, motif = False, ""
        if services.peut_transferer_pharmacie(passage):
            derniere_etape = services.etape_suivante(ticket, passage) is None
            if derniere_etape and self.hasard.random() < 0.35:
                transferer = True
            elif not derniere_etape and self.hasard.random() < 0.05:
                transferer, motif = True, "Simulation : renouvellement d'ordonnance"
        suite = services.terminer(entree, agent, transferer_pharmacie=transferer, motif=motif)
        destination = "fin du parcours" if suite is None else suite.nom_service
        self.journal(f"{service.nom} : {ticket.numero} terminé -> {destination}")

    def etape(self):
        """Fait avancer la simulation d'un pas. Retourne le nombre d'actions."""
        actions = 0
        jour = maintenant_centre(self.centre).date()
        actives = (
            EntreeFile.objects.filter(
                centre=self.centre,
                passage__ticket__jour=jour,
                passage__ticket__borne=self.borne,
                agent_id__in=self.ids_agents_test,
                statut__in=["APPELE", "EN_COURS"],
            )
            .select_related("passage__ticket", "passage__service", "file__service", "agent")
            .order_by("date_entree")
        )
        for entree in list(actives):
            if self.hasard.random() > 0.75:
                continue  # rythme irrégulier, comme dans la réalité
            try:
                if entree.statut == EntreeFile.Statut.EN_COURS:
                    self._terminer(entree, entree.agent)
                else:
                    services.prendre_en_charge(entree, entree.agent)
                    self.journal(f"{entree.file.service.nom} : {entree.passage.ticket.numero} pris en charge")
                actions += 1
            except ErreurCircuit as erreur:
                self.journal(f"Ignoré ({entree.passage.ticket.numero}) : {' '.join(erreur.messages)}")

        for code, file in self.files.items():
            if file is None:
                continue
            while services.file_en_attente(file).exists():
                # Ordre strict : si le premier de la file est un vrai patient,
                # la simulation n'appelle personne dans cette file.
                if services.file_en_attente(file).first().passage.ticket.borne_id != self.borne.pk:
                    break
                agent = self._agent_libre(code)
                if agent is None:
                    break
                try:
                    if self.services[code].appel_oral_autorise and self.hasard.random() < 0.5:
                        premier = services.file_en_attente(file).first()
                        services.prendre_en_charge(premier, agent, oral=True)
                        self.journal(f"{file.service.nom} : {premier.passage.ticket.numero} appel oral")
                    else:
                        appel = services.appeler(file, agent, poste=self._poste(code, agent))
                        self.journal(f"Écran : {appel.numero_ticket} -> {appel.nom_service} {appel.poste}")
                    actions += 1
                except ErreurCircuit:
                    break

        actions += self._repondre_notifications()
        return actions

    def _poste(self, code, agent):
        numero = agent.username[-1]
        return {"ACC": f"Guichet {numero}", "PHA": f"Comptoir {numero}"}.get(code, f"Salle {numero}")

    def _repondre_notifications(self):
        reponses = 0
        ordre = list(ReponseNotification.Statut.values)
        en_cours = Notification.objects.filter(
            centre=self.centre, destinataire__username__in=identifiants_test(),
            demande__ticket__borne=self.borne,
        ).exclude(demande__statut=DemandeAutre.Statut.CLOTUREE)
        for notification in en_cours:
            if self.hasard.random() > 0.5:
                continue
            derniere = notification.derniere_reponse()
            rang = ordre.index(derniere.statut) + 1 if derniere else 0
            statut = ordre[min(rang, ordre.index("TRAITEE"))]
            services.repondre_notification(notification, notification.destinataire, statut,
                                           "Réponse simulée" if statut == "TRAITEE" else "")
            reponses += 1
        return reponses

    def etat(self):
        jour = maintenant_centre(self.centre).date()
        tickets = Ticket.objects.filter(centre=self.centre, jour=jour, borne=self.borne)
        en_attente = EntreeFile.objects.filter(
            centre=self.centre, passage__ticket__jour=jour, passage__ticket__borne=self.borne,
            statut="EN_ATTENTE",
        )
        return {
            "tickets": tickets.count(),
            "ouverts": tickets.filter(Q(statut="EMIS") | Q(statut="EN_COURS")).count(),
            "termines": tickets.filter(statut="TERMINE").count(),
            "annules": tickets.filter(statut="ANNULE").count(),
            "en_attente": en_attente.count(),
        }
