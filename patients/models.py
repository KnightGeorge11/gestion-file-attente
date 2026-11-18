
import unicodedata

from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.postgres.indexes import GinIndex
from django.db import models, transaction
from django.db.models import F

from centres.models import Centre


def normaliser_texte(texte):
    """Supprime les accents et uniformise le texte pour la recherche."""
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(
        caractere
        for caractere in texte
        if not unicodedata.combining(caractere)
    )
    return " ".join(texte.casefold().split())


class Entreprise(models.Model):
    """Référentiel central des entreprises."""

    nom = models.CharField(max_length=200, unique=True)
    adresse = models.TextField(blank=True)
    telephone = models.CharField(max_length=30, blank=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["nom"]
        verbose_name = "Entreprise"
        verbose_name_plural = "Entreprises"

    def __str__(self):
        return self.nom


class Patient(models.Model):
    """Identité centrale et commune du patient."""

    nom = models.CharField(max_length=150)
    prenom = models.CharField(max_length=150, blank=True)
    nom_recherche = models.CharField(
        max_length=310,
        blank=True,
        default="",
        editable=False,
    )
    date_naissance = models.DateField(null=True, blank=True)
    sexe = models.CharField(max_length=20, blank=True)
    telephone = models.CharField(max_length=30, blank=True)
    adresse = models.TextField(blank=True)
    profession = models.CharField(max_length=150, blank=True)

    entreprise = models.ForeignKey(
        Entreprise,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="patients",
    )

    matricule_entreprise = models.CharField(max_length=100, blank=True)

    # Fusion réversible : la fiche fusionnée pointe vers la fiche conservée.
    # Aucun ticket ni historique n'est réattribué.
    fusionne_dans = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="fiches_fusionnees",
        editable=False,
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["nom", "prenom"]
        verbose_name = "Patient"
        verbose_name_plural = "Patients"
        indexes = [
            GinIndex(
                fields=["nom_recherche"],
                opclasses=["gin_trgm_ops"],
                name="patient_nom_recherche_trgm",
            ),
        ]

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")

        if (
            self._state.adding
            or update_fields is None
            or {"nom", "prenom"}.intersection(update_fields)
        ):
            self.nom_recherche = normaliser_texte(
                f"{self.nom} {self.prenom}"
            )

            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {
                    "nom_recherche"
                }

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.nom} {self.prenom}".strip()


class PatientCentre(models.Model):
    """Rattachement local d'un patient à un centre."""

    patient = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="rattachements",
    )

    centre = models.ForeignKey(
        Centre,
        on_delete=models.PROTECT,
        related_name="patients",
    )

    matricule = models.CharField(
        max_length=40,
        editable=False,
    )

    archive = models.BooleanField(default=False)
    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["centre", "matricule"]
        verbose_name = "Rattachement patient"
        verbose_name_plural = "Rattachements patients"

        constraints = [
            models.UniqueConstraint(
                fields=["centre", "patient"],
                name="unique_patient_par_centre",
            ),
            models.UniqueConstraint(
                fields=["centre", "matricule"],
                name="unique_matricule_patient_par_centre",
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding:
            if not self.centre_id or not self.patient_id:
                raise ValidationError(
                    "Un patient et un centre sont obligatoires."
                )

            with transaction.atomic():
                centre = Centre.objects.select_for_update().get(
                    pk=self.centre_id
                )

                numero = centre.prochain_numero_patient

                self.matricule = (
                    f"{centre.code.upper()}-{numero:06d}"
                )

                centre.prochain_numero_patient = numero + 1

                centre.save(
                    update_fields=[
                        "prochain_numero_patient",
                        "date_modification",
                    ]
                )

                return super().save(*args, **kwargs)

        original = type(self).objects.only(
            "patient_id",
            "centre_id",
            "matricule",
        ).get(pk=self.pk)

        if original.patient_id != self.patient_id:
            raise ValidationError(
                "Le patient d'un rattachement existant ne peut pas être remplacé."
            )

        if original.centre_id != self.centre_id:
            raise ValidationError(
                "Le centre d'un rattachement existant ne peut pas être remplacé."
            )

        if original.matricule != self.matricule:
            raise ValidationError(
                "Le matricule d'un rattachement ne peut pas être modifié."
            )

        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.matricule} - {self.patient}"


class SignalementDoublon(models.Model):
    """Signalement de deux fiches patient potentiellement identiques."""

    class Statut(models.TextChoices):
        A_EXAMINER = "A_EXAMINER", "À examiner"
        CONFIRME = "CONFIRME", "Doublon confirmé"
        NON_DOUBLON = "NON_DOUBLON", "Pas un doublon"

    patient_1 = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="doublons_signales_1",
    )

    patient_2 = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="doublons_signales_2",
    )

    motif = models.TextField(blank=True)

    score_similarite = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )

    statut = models.CharField(
        max_length=20,
        choices=Statut.choices,
        default=Statut.A_EXAMINER,
    )

    examine_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doublons_examines",
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_examen = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_creation"]
        verbose_name = "Signalement de doublon"
        verbose_name_plural = "Signalements de doublons"

        constraints = [
            models.CheckConstraint(
                condition=~models.Q(patient_1=F("patient_2")),
                name="doublon_patients_distincts",
            ),
        ]

    def clean(self):
        if (
            self.patient_1_id
            and self.patient_2_id
            and self.patient_1_id == self.patient_2_id
        ):
            raise ValidationError(
                "Un patient ne peut pas être comparé à lui-même."
            )

    def __str__(self):
        return (
            f"Doublon potentiel : "
            f"{self.patient_1} / {self.patient_2}"
        )


class FusionPatient(models.Model):
    """Historique des fusions manuelles et de leurs annulations."""

    class Statut(models.TextChoices):
        ACTIVE = "ACTIVE", "Fusion active"
        ANNULEE = "ANNULEE", "Fusion annulée"

    patient_source = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="fusions_comme_source",
    )

    patient_cible = models.ForeignKey(
        Patient,
        on_delete=models.PROTECT,
        related_name="fusions_comme_cible",
    )

    effectuee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="fusions_patients_effectuees",
    )

    annulee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fusions_patients_annulees",
    )

    motif = models.TextField(blank=True)

    donnees_avant_fusion = models.JSONField(
        default=dict,
        blank=True,
    )

    statut = models.CharField(
        max_length=10,
        choices=Statut.choices,
        default=Statut.ACTIVE,
    )

    date_fusion = models.DateTimeField(auto_now_add=True)
    date_annulation = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date_fusion"]
        verbose_name = "Fusion de patients"
        verbose_name_plural = "Fusions de patients"

        constraints = [
            models.CheckConstraint(
                condition=~models.Q(
                    patient_source=F("patient_cible")
                ),
                name="fusion_patients_distincts",
            ),
        ]

    def __str__(self):
        return (
            f"Fusion : {self.patient_source} "
            f"vers {self.patient_cible}"
        )
