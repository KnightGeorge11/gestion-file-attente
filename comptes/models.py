
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.core.exceptions import ValidationError


class Utilisateur(AbstractUser):
    """
    Compte utilisateur du personnel de SMIE FUNTOA.
    """

    class TypeCompte(models.TextChoices):
        ADMINISTRATEUR = "ADMIN", "Administrateur central"
        RESPONSABLE = "RESP", "Responsable"
        AGENT = "AGENT", "Agent"

    type_compte = models.CharField(
        max_length=10,
        choices=TypeCompte.choices,
        default=TypeCompte.AGENT,
    )

    telephone = models.CharField(
        max_length=20,
        blank=True,
    )

    def save(self, *args, **kwargs):
        # Identifiant unique stocké en minuscules.
        if self.username:
            self.username = self.username.strip().lower()

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        # Les comptes ne sont jamais supprimés : l'historique doit rester
        # rattaché à l'agent. Utiliser la désactivation (is_active=False).
        raise ValidationError(
            "Un compte utilisateur ne peut pas être supprimé : désactivez-le."
        )

    def __str__(self):
        return self.get_full_name() or self.username


class AffectationUtilisateur(models.Model):
    """
    Affectation d'un utilisateur à un centre.
    Un utilisateur peut avoir plusieurs affectations.
    """

    class RoleCentre(models.TextChoices):
        RESPONSABLE = "RESP", "Responsable"
        AGENT = "AGENT", "Agent"

    utilisateur = models.ForeignKey(
        "comptes.Utilisateur",
        on_delete=models.CASCADE,
        related_name="affectations",
    )

    centre = models.ForeignKey(
        "centres.Centre",
        on_delete=models.PROTECT,
        related_name="affectations_utilisateurs",
    )

    role = models.CharField(
        max_length=10,
        choices=RoleCentre.choices,
        default=RoleCentre.AGENT,
    )

    groupe = models.ForeignKey(
        "auth.Group",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="affectations",
        verbose_name="Rôle (groupe)",
        help_text="Rôle de l'agent dans ce centre (Accueil, Médecin...).",
    )

    permissions = models.ManyToManyField(
        "auth.Permission",
        blank=True,
        related_name="affectations",
        verbose_name="Permissions individuelles",
        help_text="Permissions ajoutées au rôle, pour ce centre uniquement.",
    )

    actif = models.BooleanField(
        default=True,
    )

    date_creation = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["centre", "utilisateur"]
        verbose_name = "Affectation utilisateur"
        verbose_name_plural = "Affectations utilisateurs"
        constraints = [
            models.UniqueConstraint(
                fields=["utilisateur", "centre"],
                name="unique_affectation_utilisateur_centre",
            ),
        ]

    def __str__(self):
        return f"{self.utilisateur} - {self.centre} ({self.get_role_display()})"


class AffectationService(models.Model):
    """
    Services auxquels un utilisateur est affecté dans un centre.
    Sans restriction enregistrée, l'utilisateur peut accéder
    à tous les services de son centre.
    """

    affectation = models.ForeignKey(
        AffectationUtilisateur,
        on_delete=models.CASCADE,
        related_name="services_autorises",
    )

    service = models.ForeignKey(
        "centres.Service",
        on_delete=models.PROTECT,
        related_name="affectations_utilisateurs",
    )

    date_creation = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["service__ordre", "service__nom"]
        verbose_name = "Service autorisé"
        verbose_name_plural = "Services autorisés"
        constraints = [
            models.UniqueConstraint(
                fields=["affectation", "service"],
                name="unique_service_par_affectation",
            ),
        ]

    def clean(self):
        super().clean()

        if (
            self.affectation_id
            and self.service_id
            and self.affectation.centre_id != self.service.centre_id
        ):
            raise ValidationError(
                "Le service doit appartenir au centre de l'affectation."
            )

    def __str__(self):
        return f"{self.affectation} - {self.service}"
