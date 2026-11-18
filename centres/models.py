
import datetime
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction

from core.models import ModeleCentre


class Centre(models.Model):
    """
    Centre SMIE FUNTOA.
    """

    code = models.CharField(
        max_length=20,
        unique=True,
    )

    nom = models.CharField(
        max_length=150,
    )

    adresse = models.TextField(
        blank=True,
    )

    fuseau_horaire = models.CharField(
        max_length=50,
        default="Indian/Antananarivo",
    )

    # Prochain numéro de matricule patient à attribuer dans ce centre.
    prochain_numero_patient = models.PositiveBigIntegerField(
        default=1,
    )

    # Paramètres de fonctionnement du centre.
    heure_coupure_matin = models.TimeField(
        default=datetime.time(12, 0),
        help_text="Fin du matin et début de l'après-midi (quotas).",
    )

    heure_cloture_tickets = models.TimeField(
        null=True,
        blank=True,
        help_text="Heure de clôture des tickets restés ouverts (à confirmer).",
    )

    max_reintegrations = models.PositiveSmallIntegerField(
        default=1,
        help_text="Nombre maximal de réintégrations après une absence.",
    )

    max_tickets_ouverts_par_patient = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )

    duree_affichage_appel_secondes = models.PositiveSmallIntegerField(
        default=30,
        validators=[MinValueValidator(1)],
        help_text="Durée d'affichage d'un appel sur l'écran principal.",
    )

    actif = models.BooleanField(
        default=True,
    )

    date_creation = models.DateTimeField(
        auto_now_add=True,
    )

    date_modification = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["nom"]
        verbose_name = "Centre"
        verbose_name_plural = "Centres"

    def __str__(self):
        return f"{self.code} - {self.nom}"


class Service(models.Model):
    """
    Service appartenant à un centre.
    """

    centre = models.ForeignKey(
        Centre,
        on_delete=models.PROTECT,
        related_name="services",
    )

    code = models.CharField(
        max_length=20,
    )

    nom = models.CharField(
        max_length=100,
    )

    ordre = models.PositiveSmallIntegerField(
        default=0,
    )

    est_accueil = models.BooleanField(
        default=False,
    )

    est_pharmacie = models.BooleanField(
        default=False,
    )

    accepte_transferts = models.BooleanField(
        default=True,
    )

    appel_oral_autorise = models.BooleanField(
        default=False,
    )

    afficher_ecran_principal = models.BooleanField(
        default=True,
    )

    actif = models.BooleanField(
        default=True,
    )

    # Suspension temporaire : le service reste configuré
    # mais ne reçoit plus de patients.
    suspendu = models.BooleanField(
        default=False,
    )

    motif_suspension = models.CharField(
        max_length=255,
        blank=True,
    )

    # Mode Autre : lorsqu'un service est choisi, la notification est
    # adressée à cet agent désigné, et non à tous les agents du service.
    destinataire_demandes = models.ForeignKey(
        "comptes.Utilisateur",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="services_destinataire_demandes",
        verbose_name="Agent désigné (demandes « Autre »)",
    )

    date_creation = models.DateTimeField(
        auto_now_add=True,
    )

    date_modification = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["centre", "ordre", "nom"]
        verbose_name = "Service"
        verbose_name_plural = "Services"
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "code"],
                name="unique_code_service_par_centre",
            ),
        ]

    def __str__(self):
        return f"{self.nom} ({self.centre.code})"

    @property
    def disponible(self):
        return self.actif and not self.suspendu

    def clean(self):
        super().clean()

        if self.suspendu and not self.motif_suspension.strip():
            raise ValidationError(
                {"motif_suspension": "Un motif est obligatoire pour suspendre un service."}
            )

    def save(self, *args, **kwargs):
        creation = self._state.adding

        with transaction.atomic():
            super().save(*args, **kwargs)

            # Chaque service possède une file par défaut.
            if creation:
                FileAttente.objects.create(
                    centre_id=self.centre_id,
                    service=self,
                    code=self.code,
                    nom=self.nom,
                    par_defaut=True,
                )

    def file_par_defaut(self):
        return self.files.filter(par_defaut=True).first()


class HoraireService(models.Model):
    """Plage d'ouverture d'un service. Plusieurs plages possibles par jour."""

    class JourSemaine(models.IntegerChoices):
        LUNDI = 0, "Lundi"
        MARDI = 1, "Mardi"
        MERCREDI = 2, "Mercredi"
        JEUDI = 3, "Jeudi"
        VENDREDI = 4, "Vendredi"
        SAMEDI = 5, "Samedi"
        DIMANCHE = 6, "Dimanche"

    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name="horaires",
    )

    jour_semaine = models.PositiveSmallIntegerField(
        choices=JourSemaine.choices,
    )

    heure_debut = models.TimeField()
    heure_fin = models.TimeField()

    class Meta:
        ordering = ["service", "jour_semaine", "heure_debut"]
        verbose_name = "Horaire de service"
        verbose_name_plural = "Horaires de service"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(heure_fin__gt=models.F("heure_debut")),
                name="horaire_fin_apres_debut",
            ),
        ]

    def clean(self):
        super().clean()

        if (
            self.heure_debut
            and self.heure_fin
            and self.heure_fin <= self.heure_debut
        ):
            raise ValidationError(
                "L'heure de fin doit être postérieure à l'heure de début."
            )

        if self.service_id and self.heure_debut and self.heure_fin:
            chevauchement = (
                HoraireService.objects
                .filter(
                    service_id=self.service_id,
                    jour_semaine=self.jour_semaine,
                    heure_debut__lt=self.heure_fin,
                    heure_fin__gt=self.heure_debut,
                )
                .exclude(pk=self.pk)
                .exists()
            )

            if chevauchement:
                raise ValidationError(
                    "Cette plage chevauche une autre plage du même jour."
                )

    def __str__(self):
        return (
            f"{self.service} - {self.get_jour_semaine_display()} "
            f"{self.heure_debut:%H:%M}-{self.heure_fin:%H:%M}"
        )


class FileAttente(ModeleCentre):
    """
    File d'attente d'un service. Une file par défaut est créée
    automatiquement avec chaque service.
    """

    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="files",
    )

    code = models.CharField(max_length=20)
    nom = models.CharField(max_length=100)

    par_defaut = models.BooleanField(default=False)

    nb_appels_avant_absence = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1)],
    )

    delai_entre_appels_secondes = models.PositiveSmallIntegerField(
        default=60,
        help_text="Valeur provisoire, à confirmer.",
    )

    nb_appels_simultanes = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
        help_text="Plusieurs uniquement pour une file partagée (médecins).",
    )

    ordre_strict = models.BooleanField(
        default=True,
        help_text="Si coché, seul le premier patient de la file peut être appelé.",
    )

    actif = models.BooleanField(default=True)

    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["centre", "service__ordre", "nom"]
        verbose_name = "File d'attente"
        verbose_name_plural = "Files d'attente"
        constraints = [
            models.UniqueConstraint(
                fields=["service", "code"],
                name="unique_code_file_par_service",
            ),
            models.UniqueConstraint(
                fields=["service"],
                condition=models.Q(par_defaut=True),
                name="unique_file_par_defaut_par_service",
            ),
        ]

    def clean(self):
        super().clean()

        if (
            self.service_id
            and self.centre_id
            and self.service.centre_id != self.centre_id
        ):
            raise ValidationError(
                "La file doit appartenir au même centre que son service."
            )

    def __str__(self):
        return f"{self.nom} ({self.service})"


class Borne(ModeleCentre):
    """
    Poste de libre-service émettant les tickets.
    Identifié par un code et une clé secrète ; aucun compte utilisateur.
    """

    code = models.CharField(max_length=30)
    libelle = models.CharField(max_length=100)

    cle_hash = models.CharField(
        max_length=128,
        editable=False,
    )

    actif = models.BooleanField(default=True)
    derniere_activite = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["centre", "code"]
        verbose_name = "Borne"
        verbose_name_plural = "Bornes"
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "code"],
                name="unique_code_borne_par_centre",
            ),
        ]

    def generer_cle(self):
        """
        Génère une nouvelle clé, en conserve uniquement l'empreinte
        et retourne la clé en clair (à afficher une seule fois).
        """
        cle = secrets.token_urlsafe(32)
        self.cle_hash = make_password(cle)
        return cle

    def verifier_cle(self, cle):
        if not cle or not self.cle_hash:
            return False

        return check_password(cle, self.cle_hash)

    def __str__(self):
        return f"{self.libelle} ({self.centre.code})"


class EcranAffichage(ModeleCentre):
    """
    Écran principal (Smart TV) affichant les appels du centre.
    Identifié par un code et une clé secrète ; aucun compte utilisateur.
    """

    code = models.CharField(max_length=30)
    libelle = models.CharField(max_length=100)
    cle_hash = models.CharField(max_length=128, editable=False)
    nb_appels_affiches = models.PositiveSmallIntegerField(
        default=8,
        validators=[MinValueValidator(1)],
        help_text="Nombre maximal d'appels affichés simultanément.",
    )
    annonce_vocale = models.BooleanField(
        default=True,
        help_text="Annoncer les appels par synthèse vocale (si disponible sur l'appareil).",
    )
    actif = models.BooleanField(default=True)
    derniere_activite = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["centre", "code"]
        verbose_name = "Écran d'affichage"
        verbose_name_plural = "Écrans d'affichage"
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "code"],
                name="unique_code_ecran_par_centre",
            ),
        ]

    generer_cle = Borne.generer_cle
    verifier_cle = Borne.verifier_cle

    def __str__(self):
        return f"{self.libelle} ({self.centre.code})"
