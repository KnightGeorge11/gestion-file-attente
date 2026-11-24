from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class JournalAudit(models.Model):
    """
    Journal des actions d'administration et de sécurité (connexions,
    rôles, affectations, quotas, parcours, désactivations, fusions).

    Distinct du journal métier `circuit.Evenement`. Ajout seulement :
    un trigger PostgreSQL refuse UPDATE et DELETE.
    """

    class Action(models.TextChoices):
        CONNEXION = "CONNEXION", "Connexion"
        ECHEC_CONNEXION = "ECHEC_CONNEXION", "Échec de connexion"
        DECONNEXION = "DECONNEXION", "Déconnexion"
        CREATION = "CREATION", "Création"
        MODIFICATION = "MODIFICATION", "Modification"
        SUPPRESSION = "SUPPRESSION", "Suppression"
        DESACTIVATION = "DESACTIVATION", "Désactivation"
        REACTIVATION = "REACTIVATION", "Réactivation"
        ROLE = "ROLE", "Changement de rôle ou de permissions"
        PUBLICATION = "PUBLICATION", "Publication de parcours"
        QUOTA = "QUOTA", "Modification de quota"
        FUSION = "FUSION", "Fusion de patients"
        ANNULATION_FUSION = "ANNULATION_FUSION", "Annulation de fusion"
        EXPORT = "EXPORT", "Export de données"

    date = models.DateTimeField(auto_now_add=True, db_index=True)
    utilisateur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="actions_auditees",
    )
    identifiant_saisi = models.CharField(
        max_length=150,
        blank=True,
        help_text="Identifiant saisi (utile pour les échecs de connexion).",
    )
    action = models.CharField(max_length=20, choices=Action.choices)
    centre = models.ForeignKey(
        "centres.Centre",
        on_delete=models.DO_NOTHING,
        null=True,
        blank=True,
        related_name="journal_audit",
    )
    objet_type = models.CharField(max_length=100, blank=True)
    objet_id = models.CharField(max_length=64, blank=True)
    objet_libelle = models.CharField(max_length=255, blank=True)
    details = models.JSONField(default=dict, blank=True)
    adresse_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name = "Entrée du journal d'audit"
        verbose_name_plural = "Journal d'audit"

    def __str__(self):
        return f"{timezone.localtime(self.date):%d/%m/%Y %H:%M} {self.get_action_display()} {self.objet_libelle}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Le journal d'audit ne peut pas être modifié.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Le journal d'audit ne peut pas être supprimé.")
