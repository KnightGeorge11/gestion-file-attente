"""
Moteur du parcours patient : tickets, passages, files, appels,
transferts, réservations de quota, demandes « Autre » et événements.

Le concept de « visite » n'existe pas : un ticket correspond à un parcours.
"""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from core.models import ModeleCentre


class CompteurTicket(ModeleCentre):
    """Compteur de numérotation par préfixe et par jour (heure du centre)."""

    prefixe = models.CharField(max_length=1)
    jour = models.DateField()
    dernier_numero = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Compteur de tickets"
        verbose_name_plural = "Compteurs de tickets"
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "prefixe", "jour"],
                name="unique_compteur_centre_prefixe_jour",
            ),
        ]

    def __str__(self):
        return f"{self.prefixe} {self.jour} : {self.dernier_numero}"


# Motifs proposés par défaut sur la borne pour le mode Autre (demandes hors
# parcours de soins). Liste modifiable par centre dans l'administration.
MOTIFS_AUTRE_PAR_DEFAUT = [
    "Rendez-vous",
    "Visite d'embauche",
    "Entretien",
    "Cotisations (paiement ou renseignement)",
    "Rencontrer une personne du centre",
]


class MotifDemande(ModeleCentre):
    """Motifs proposés pour le mode Autre (liste évolutive)."""

    libelle = models.CharField(max_length=150)
    ordre = models.PositiveSmallIntegerField(default=0)
    actif = models.BooleanField(default=True)

    class Meta:
        ordering = ["centre", "ordre", "libelle"]
        verbose_name = "Motif de demande (mode Autre)"
        verbose_name_plural = "Motifs de demande (mode Autre)"

    def __str__(self):
        return self.libelle


class Ticket(ModeleCentre):
    class Mode(models.TextChoices):
        INTERNE = "INTERNE", "Interne"
        EXTERNE = "EXTERNE", "Externe"
        AUTRE = "AUTRE", "Autre"

    class Statut(models.TextChoices):
        EMIS = "EMIS", "Émis"
        EN_COURS = "EN_COURS", "En cours"
        TERMINE = "TERMINE", "Terminé"
        ANNULE = "ANNULE", "Annulé"
        CLOTURE = "CLOTURE", "Clôturé automatiquement"

    STATUTS_OUVERTS = ("EMIS", "EN_COURS")

    numero = models.CharField(max_length=10)
    prefixe = models.CharField(max_length=1)
    sequence = models.PositiveIntegerField()
    jour = models.DateField(help_text="Jour d'émission (heure du centre).")

    mode = models.CharField(max_length=10, choices=Mode.choices)

    parcours = models.ForeignKey(
        "parcours.Parcours",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tickets",
    )
    parcours_version = models.ForeignKey(
        "parcours.ParcoursVersion",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tickets",
    )

    patient = models.ForeignKey(
        "patients.Patient",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tickets",
    )

    borne = models.ForeignKey(
        "centres.Borne",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tickets",
    )

    statut = models.CharField(
        max_length=10, choices=Statut.choices, default=Statut.EMIS
    )

    date_emission = models.DateTimeField()
    date_identification = models.DateTimeField(null=True, blank=True)
    identifie_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tickets_identifies",
    )
    date_fin = models.DateTimeField(null=True, blank=True)
    motif_annulation = models.TextField(blank=True)
    annule_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tickets_annules",
    )

    class Meta:
        ordering = ["-date_emission"]
        verbose_name = "Ticket"
        verbose_name_plural = "Tickets"
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "prefixe", "jour", "sequence"],
                name="unique_numero_ticket_par_jour",
            ),
            models.CheckConstraint(
                condition=Q(mode="AUTRE") | Q(parcours__isnull=False),
                name="ticket_soins_avec_parcours",
            ),
        ]
        indexes = [
            models.Index(fields=["centre", "jour"]),
            models.Index(fields=["centre", "statut"]),
        ]
        permissions = [
            ("appeler", "Peut appeler, rappeler et prendre en charge"),
            ("marquer_absent", "Peut marquer un patient absent"),
            ("reintegrer", "Peut réintégrer un patient absent"),
            ("deplacer", "Peut déplacer un patient dans une file"),
            ("transferer", "Peut transférer un patient vers la pharmacie"),
            ("identifier_patient", "Peut identifier le patient d'un ticket"),
            ("annuler_ticket", "Peut annuler un ticket"),
            ("orienter_demande", "Peut orienter une demande (mode Autre)"),
            ("voir_statistiques", "Peut consulter les statistiques"),
            ("consulter_reseau", "Peut consulter tous les centres"),
        ]

    def __str__(self):
        return f"{self.numero} ({self.jour:%d/%m/%Y})"

    @property
    def ouvert(self):
        return self.statut in self.STATUTS_OUVERTS

    @property
    def est_autre(self):
        return self.mode == self.Mode.AUTRE

    @property
    def duree_totale(self):
        if self.date_fin:
            return self.date_fin - self.date_emission
        return None

    def passage_actif(self):
        return self.passages.filter(
            statut__in=Passage.STATUTS_ACTIFS
        ).first()

    def reservation_active(self):
        return self.reservations_quota.filter(
            statut__in=["RESERVEE", "CONSOMMEE"]
        ).first()


class Passage(ModeleCentre):
    """Passage effectif du patient dans un service."""

    class Origine(models.TextChoices):
        PARCOURS = "PARCOURS", "Étape du parcours"
        TRANSFERT = "TRANSFERT", "Transfert"

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        APPELE = "APPELE", "Appelé"
        EN_COURS = "EN_COURS", "En cours de prise en charge"
        ABSENT = "ABSENT", "Absent"
        TERMINE = "TERMINE", "Terminé"
        ANNULE = "ANNULE", "Annulé"

    STATUTS_ACTIFS = ("EN_ATTENTE", "APPELE", "EN_COURS", "ABSENT")

    ticket = models.ForeignKey(
        Ticket, on_delete=models.PROTECT, related_name="passages"
    )
    service = models.ForeignKey(
        "centres.Service", on_delete=models.PROTECT, related_name="passages"
    )
    file = models.ForeignKey(
        "centres.FileAttente", on_delete=models.PROTECT, related_name="passages"
    )
    # Noms figés au moment du passage.
    nom_service = models.CharField(max_length=100)
    nom_file = models.CharField(max_length=100)

    origine = models.CharField(max_length=10, choices=Origine.choices)
    ordre_etape = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text="Ordre de l'étape du parcours (vide pour un transfert).",
    )

    statut = models.CharField(
        max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE
    )

    date_arrivee = models.DateTimeField()
    date_debut = models.DateTimeField(null=True, blank=True)
    date_fin = models.DateTimeField(null=True, blank=True)

    pris_en_charge_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="passages_pris_en_charge",
    )
    termine_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="passages_termines",
    )

    class Meta:
        ordering = ["date_arrivee", "id"]
        verbose_name = "Passage"
        verbose_name_plural = "Passages"
        constraints = [
            # Un ticket n'est jamais dans deux services en même temps.
            models.UniqueConstraint(
                fields=["ticket"],
                condition=Q(statut__in=["EN_ATTENTE", "APPELE", "EN_COURS", "ABSENT"]),
                name="un_passage_actif_par_ticket",
            ),
        ]

    def __str__(self):
        return f"{self.ticket.numero} - {self.nom_service}"

    @property
    def duree_attente(self):
        if self.date_debut:
            return self.date_debut - self.date_arrivee
        return None

    @property
    def duree_prise_en_charge(self):
        if self.date_debut and self.date_fin:
            return self.date_fin - self.date_debut
        return None


class EntreeFile(ModeleCentre):
    """Entrée et position (rang) d'un passage dans une file d'attente."""

    class Statut(models.TextChoices):
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        APPELE = "APPELE", "Appelé"
        EN_COURS = "EN_COURS", "En cours"
        TERMINE = "TERMINE", "Terminé"
        ABSENT = "ABSENT", "Absent"
        ANNULE = "ANNULE", "Annulé"

    STATUTS_TENUS = ("APPELE", "EN_COURS")

    passage = models.ForeignKey(
        Passage, on_delete=models.PROTECT, related_name="entrees"
    )
    file = models.ForeignKey(
        "centres.FileAttente", on_delete=models.PROTECT, related_name="entrees"
    )
    rang = models.PositiveBigIntegerField()
    statut = models.CharField(
        max_length=12, choices=Statut.choices, default=Statut.EN_ATTENTE
    )
    reintegration = models.BooleanField(default=False)

    date_entree = models.DateTimeField()
    nb_appels = models.PositiveSmallIntegerField(default=0)
    date_premier_appel = models.DateTimeField(null=True, blank=True)
    date_dernier_appel = models.DateTimeField(null=True, blank=True)
    date_debut = models.DateTimeField(null=True, blank=True)
    date_fin = models.DateTimeField(null=True, blank=True)
    date_absence = models.DateTimeField(null=True, blank=True)

    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="entrees_tenues",
        help_text="Agent qui a appelé / pris en charge le patient.",
    )
    poste = models.CharField(
        max_length=50,
        blank=True,
        help_text="Guichet, salle ou bureau annoncé (texte libre).",
    )

    class Meta:
        ordering = ["file", "rang"]
        verbose_name = "Entrée de file"
        verbose_name_plural = "Entrées de file"
        constraints = [
            models.UniqueConstraint(
                fields=["file", "rang"], name="unique_rang_par_file"
            ),
            # Un agent ne tient qu'un patient à la fois.
            models.UniqueConstraint(
                fields=["agent"],
                condition=Q(statut__in=["APPELE", "EN_COURS"]),
                name="un_patient_tenu_par_agent",
            ),
            # Une seule entrée active par passage.
            models.UniqueConstraint(
                fields=["passage"],
                condition=Q(statut__in=["EN_ATTENTE", "APPELE", "EN_COURS"]),
                name="une_entree_active_par_passage",
            ),
        ]
        indexes = [models.Index(fields=["file", "statut", "rang"])]

    def __str__(self):
        return f"{self.passage.ticket.numero} - {self.file.nom} (rang {self.rang})"

    def prochain_appel_possible(self):
        if self.date_dernier_appel is None:
            return None
        return self.date_dernier_appel + timedelta(
            seconds=self.file.delai_entre_appels_secondes
        )


class Appel(ModeleCentre):
    """Un appel d'un patient (écran principal ou oral)."""

    class Mode(models.TextChoices):
        ECRAN = "ECRAN", "Écran principal"
        ORAL = "ORAL", "Appel oral"

    entree = models.ForeignKey(
        EntreeFile, on_delete=models.PROTECT, related_name="appels"
    )
    ticket = models.ForeignKey(
        Ticket, on_delete=models.PROTECT, related_name="appels"
    )
    service = models.ForeignKey(
        "centres.Service", on_delete=models.PROTECT, related_name="appels"
    )
    numero_ticket = models.CharField(max_length=10)
    nom_service = models.CharField(max_length=100)
    poste = models.CharField(max_length=50, blank=True)
    numero_appel = models.PositiveSmallIntegerField()
    mode = models.CharField(max_length=5, choices=Mode.choices, default=Mode.ECRAN)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="appels"
    )
    date = models.DateTimeField()

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Appel"
        verbose_name_plural = "Appels"
        indexes = [models.Index(fields=["centre", "date"])]

    def __str__(self):
        return f"{self.numero_ticket} → {self.nom_service} ({self.numero_appel})"


class Transfert(ModeleCentre):
    """Transfert explicite décidé par un agent du service d'origine."""

    ticket = models.ForeignKey(
        Ticket, on_delete=models.PROTECT, related_name="transferts"
    )
    passage_origine = models.ForeignKey(
        Passage, on_delete=models.PROTECT, related_name="transferts_emis"
    )
    passage_destination = models.OneToOneField(
        Passage, on_delete=models.PROTECT, related_name="transfert_recu"
    )
    service_destination = models.ForeignKey(
        "centres.Service", on_delete=models.PROTECT, related_name="transferts_recus"
    )
    motif = models.TextField(blank=True)
    decide_par = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="transferts"
    )
    date = models.DateTimeField()

    class Meta:
        ordering = ["-date"]
        verbose_name = "Transfert"
        verbose_name_plural = "Transferts"

    def __str__(self):
        return f"{self.ticket.numero} → {self.service_destination.nom}"


class ReservationQuota(ModeleCentre):
    """Place de quota réservée à l'identification (dentiste)."""

    class Statut(models.TextChoices):
        RESERVEE = "RESERVEE", "Réservée"
        CONSOMMEE = "CONSOMMEE", "Consommée (patient pris en charge)"
        LIBEREE = "LIBEREE", "Libérée"

    STATUTS_OCCUPANT = ("RESERVEE", "CONSOMMEE")

    ticket = models.ForeignKey(
        Ticket, on_delete=models.PROTECT, related_name="reservations_quota"
    )
    parcours = models.ForeignKey(
        "parcours.Parcours", on_delete=models.PROTECT, related_name="reservations"
    )
    quota = models.ForeignKey(
        "parcours.QuotaParcours",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reservations",
    )
    jour = models.DateField()
    periode = models.CharField(max_length=10)
    statut = models.CharField(
        max_length=10, choices=Statut.choices, default=Statut.RESERVEE
    )

    derogation = models.BooleanField(default=False)
    motif_derogation = models.TextField(blank=True)

    reservee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reservations_quota",
    )
    date_reservation = models.DateTimeField()

    liberee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="liberations_quota",
    )
    motif_liberation = models.TextField(blank=True)
    date_liberation = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_reservation"]
        verbose_name = "Réservation de quota"
        verbose_name_plural = "Réservations de quota"
        constraints = [
            models.UniqueConstraint(
                fields=["ticket"],
                condition=Q(statut__in=["RESERVEE", "CONSOMMEE"]),
                name="une_reservation_active_par_ticket",
            ),
            models.CheckConstraint(
                condition=Q(derogation=False) | ~Q(motif_derogation=""),
                name="derogation_avec_motif",
            ),
            models.CheckConstraint(
                condition=~Q(statut="LIBEREE") | ~Q(motif_liberation=""),
                name="liberation_avec_motif",
            ),
        ]
        indexes = [models.Index(fields=["parcours", "jour", "periode", "statut"])]

    def __str__(self):
        return f"{self.ticket.numero} - {self.jour} {self.periode} ({self.get_statut_display()})"


class DemandeAutre(ModeleCentre):
    """Demande hors parcours de soins (mode Autre)."""

    class Statut(models.TextChoices):
        NOUVELLE = "NOUVELLE", "Nouvelle"
        TRANSMISE = "TRANSMISE", "Transmise"
        CLOTUREE = "CLOTUREE", "Clôturée"

    ticket = models.OneToOneField(
        Ticket, on_delete=models.PROTECT, related_name="demande"
    )
    motif = models.ForeignKey(
        MotifDemande,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="demandes",
    )
    precision = models.TextField(blank=True)
    statut = models.CharField(
        max_length=10, choices=Statut.choices, default=Statut.NOUVELLE
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    vue_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="demandes_vues",
    )
    date_vue = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "Demande (mode Autre)"
        verbose_name_plural = "Demandes (mode Autre)"

    def __str__(self):
        return f"{self.ticket.numero} - {self.motif or 'Autre'}"


class Notification(ModeleCentre):
    """Notification transmise par l'accueil à une personne désignée."""

    demande = models.ForeignKey(
        DemandeAutre, on_delete=models.PROTECT, related_name="notifications"
    )
    destinataire = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="notifications_recues",
    )
    service = models.ForeignKey(
        "centres.Service",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="notifications",
        help_text="Service choisi par l'accueil (vide si personne précise).",
    )
    message = models.TextField(blank=True)
    transmise_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="notifications_transmises",
    )
    date = models.DateTimeField()
    date_lecture = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "Notification"
        verbose_name_plural = "Notifications"

    def __str__(self):
        return f"{self.demande} → {self.destinataire}"

    def derniere_reponse(self):
        return self.reponses.order_by("-date", "-id").first()


class ReponseNotification(ModeleCentre):
    """Réponse du destinataire : statut prédéfini + message libre facultatif."""

    class Statut(models.TextChoices):
        RECUE = "RECUE", "Demande reçue"
        PRISE_EN_COMPTE = "PRISE_EN_COMPTE", "Demande prise en compte"
        EN_COURS = "EN_COURS", "En cours de traitement"
        PATIENT_ATTENDU = "PATIENT_ATTENDU", "Patient attendu"
        TRAITEE = "TRAITEE", "Demande traitée"
        NON_REALISABLE = "NON_REALISABLE", "Demande non réalisable"

    STATUTS_FINAUX = ("TRAITEE", "NON_REALISABLE")

    notification = models.ForeignKey(
        Notification, on_delete=models.PROTECT, related_name="reponses"
    )
    statut = models.CharField(max_length=20, choices=Statut.choices)
    message = models.TextField(blank=True)
    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reponses_notifications",
    )
    date = models.DateTimeField()
    lue_par_accueil = models.BooleanField(default=False)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Réponse à une notification"
        verbose_name_plural = "Réponses aux notifications"

    def __str__(self):
        return f"{self.get_statut_display()} ({self.auteur})"


class Evenement(ModeleCentre):
    """
    Historique chronologique des actions sur un ticket.
    Ajout seulement : un trigger PostgreSQL refuse UPDATE et DELETE.
    """

    class Type(models.TextChoices):
        EMISSION = "EMISSION", "Émission du ticket"
        ENTREE_FILE = "ENTREE_FILE", "Entrée en file"
        APPEL = "APPEL", "Appel"
        RAPPEL = "RAPPEL", "Rappel"
        PRISE_EN_CHARGE = "PRISE_EN_CHARGE", "Prise en charge"
        FIN_PRISE_EN_CHARGE = "FIN_PRISE_EN_CHARGE", "Fin de prise en charge"
        ABSENCE = "ABSENCE", "Absence"
        REINTEGRATION = "REINTEGRATION", "Réintégration"
        TRANSFERT = "TRANSFERT", "Transfert"
        IDENTIFICATION = "IDENTIFICATION", "Identification du patient"
        RESERVATION_QUOTA = "RESERVATION_QUOTA", "Réservation de quota"
        DEROGATION_QUOTA = "DEROGATION_QUOTA", "Dérogation au quota"
        LIBERATION_QUOTA = "LIBERATION_QUOTA", "Libération de quota"
        NOTIFICATION = "NOTIFICATION", "Notification transmise"
        REPONSE = "REPONSE", "Réponse à une notification"
        FIN_TICKET = "FIN_TICKET", "Fin du ticket"
        ANNULATION = "ANNULATION", "Annulation"
        CLOTURE = "CLOTURE", "Clôture automatique"

    ticket = models.ForeignKey(
        Ticket, on_delete=models.DO_NOTHING, related_name="evenements"
    )
    passage = models.ForeignKey(
        Passage,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="evenements",
    )
    entree = models.ForeignKey(
        EntreeFile,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="evenements",
    )
    type = models.CharField(max_length=25, choices=Type.choices)
    agent = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="evenements",
    )
    date = models.DateTimeField()
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["date", "id"]
        verbose_name = "Événement"
        verbose_name_plural = "Événements"
        indexes = [models.Index(fields=["centre", "date"])]

    def __str__(self):
        return f"{self.date:%H:%M:%S} {self.get_type_display()}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Un événement ne peut pas être modifié.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Un événement ne peut pas être supprimé.")
