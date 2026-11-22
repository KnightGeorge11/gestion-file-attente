from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, transaction
from django.db.models import F, Func, Max, Q, Value
from django.utils import timezone

from core.models import ModeleCentre


class Parcours(ModeleCentre):
    """
    Parcours proposé sur la borne (Médecin, Dentiste, Infirmerie, Échographie).
    Le contenu (les étapes) est porté par des versions.
    """

    code = models.CharField(max_length=20)

    prefixe = models.CharField(
        max_length=1,
        validators=[
            RegexValidator(
                r"^[A-Z]$",
                "Le préfixe doit être une seule lettre majuscule.",
            ),
        ],
        help_text="Lettre des tickets de ce parcours (M, D, I, E...).",
    )

    libelle_borne = models.CharField(
        max_length=100,
        help_text="Libellé affiché sur la borne.",
    )

    ordre_affichage = models.PositiveSmallIntegerField(default=0)

    visible_borne = models.BooleanField(default=True)

    # Médecin en chef (décision du 22/11/2026) : proposé seulement sur la borne
    # tenue par l'accueil (centre où l'accueil délivre les tickets).
    reserve_accueil = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name="Réservé à la borne de l'accueil",
        help_text="Proposé uniquement quand l'accueil tient la borne et délivre les tickets ; jamais en libre-service.",
    )

    soumis_quota = models.BooleanField(
        default=False,
        help_text=(
            "Si coché, une place de quota doit être disponible "
            "pour identifier un patient (dentiste)."
        ),
    )

    actif = models.BooleanField(default=True)

    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["centre", "ordre_affichage", "libelle_borne"]
        verbose_name = "Parcours"
        verbose_name_plural = "Parcours"
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "code"],
                name="unique_code_parcours_par_centre",
            ),
            models.UniqueConstraint(
                fields=["centre", "prefixe"],
                name="unique_prefixe_parcours_par_centre",
            ),
        ]

    def clean(self):
        super().clean()
        from circuit.services import PREFIXE_AUTRE

        if (self.prefixe or "").upper() == PREFIXE_AUTRE:
            raise ValidationError(
                {"prefixe": f"Le préfixe « {PREFIXE_AUTRE} » est réservé aux tickets « Autre » "
                            "(rendez-vous, cotisations, rencontres)."}
            )

    def __str__(self):
        return f"{self.prefixe} - {self.libelle_borne} ({self.centre.code})"

    def version_publiee(self):
        return self.versions.filter(
            statut=ParcoursVersion.Statut.PUBLIEE
        ).first()

    def brouillon(self):
        return self.versions.filter(
            statut=ParcoursVersion.Statut.BROUILLON
        ).first()

    def creer_brouillon(self):
        """
        Retourne le brouillon en cours ou en crée un nouveau,
        en recopiant les étapes de la version publiée.
        """
        with transaction.atomic():
            Parcours.objects.select_for_update().get(pk=self.pk)

            brouillon = self.brouillon()

            if brouillon is not None:
                return brouillon

            dernier_numero = (
                self.versions.aggregate(m=Max("numero"))["m"] or 0
            )

            brouillon = ParcoursVersion.objects.create(
                parcours=self,
                numero=dernier_numero + 1,
            )

            publiee = self.version_publiee()

            if publiee is not None:
                for etape in publiee.etapes.all():
                    EtapeParcours.objects.create(
                        version=brouillon,
                        ordre=etape.ordre,
                        service_id=etape.service_id,
                        file_id=etape.file_id,
                    )

            return brouillon


class ParcoursVersion(models.Model):
    """
    Version d'un parcours. Une version publiée est immuable :
    modifier le parcours crée une nouvelle version. Les tickets
    gardent la version en vigueur au moment de leur émission.
    """

    class Statut(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        PUBLIEE = "PUBLIEE", "Publiée"
        ARCHIVEE = "ARCHIVEE", "Archivée"

    parcours = models.ForeignKey(
        Parcours,
        on_delete=models.PROTECT,
        related_name="versions",
    )

    numero = models.PositiveIntegerField()

    statut = models.CharField(
        max_length=10,
        choices=Statut.choices,
        default=Statut.BROUILLON,
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_publication = models.DateTimeField(null=True, blank=True)

    publiee_par = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="versions_parcours_publiees",
    )

    class Meta:
        ordering = ["parcours", "-numero"]
        verbose_name = "Version de parcours"
        verbose_name_plural = "Versions de parcours"
        constraints = [
            models.UniqueConstraint(
                fields=["parcours", "numero"],
                name="unique_numero_version_par_parcours",
            ),
            models.UniqueConstraint(
                fields=["parcours"],
                condition=Q(statut="PUBLIEE"),
                name="une_version_publiee_par_parcours",
            ),
            models.UniqueConstraint(
                fields=["parcours"],
                condition=Q(statut="BROUILLON"),
                name="un_brouillon_par_parcours",
            ),
        ]

    def __str__(self):
        return f"{self.parcours} - v{self.numero} ({self.get_statut_display()})"

    @property
    def modifiable(self):
        return self.statut == self.Statut.BROUILLON

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = type(self).objects.get(pk=self.pk)

            if original.statut != self.Statut.BROUILLON:
                # Seul passage autorisé : publiée -> archivée.
                transition_archivage = (
                    original.statut == self.Statut.PUBLIEE
                    and self.statut == self.Statut.ARCHIVEE
                )
                inchange = (
                    original.parcours_id == self.parcours_id
                    and original.numero == self.numero
                    and original.date_publication == self.date_publication
                    and original.publiee_par_id == self.publiee_par_id
                )

                if not (transition_archivage and inchange) and not (
                    original.statut == self.statut and inchange
                ):
                    raise ValidationError(
                        "Une version publiée ne peut pas être modifiée. "
                        "Créez une nouvelle version."
                    )

        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.statut != self.Statut.BROUILLON:
            raise ValidationError(
                "Seul un brouillon peut être supprimé."
            )

        return super().delete(*args, **kwargs)

    def verifier_publiable(self):
        """Liste des erreurs empêchant la publication (vide si publiable)."""
        erreurs = []
        etapes = list(
            self.etapes.select_related("service", "file").order_by("ordre")
        )

        if not etapes:
            erreurs.append("La version ne contient aucune étape.")
            return erreurs

        if not etapes[0].service.est_accueil:
            erreurs.append("La première étape doit être l'accueil.")

        centre_id = self.parcours.centre_id

        for etape in etapes:
            if etape.service.est_pharmacie:
                erreurs.append(
                    "La pharmacie ne peut pas être une étape du parcours : "
                    "elle reçoit les patients uniquement par transfert."
                )

            if etape.service.centre_id != centre_id:
                erreurs.append(
                    f"Étape {etape.ordre} : le service n'appartient pas au centre."
                )

            if etape.file.service_id != etape.service_id:
                erreurs.append(
                    f"Étape {etape.ordre} : la file n'appartient pas au service."
                )

            if not etape.service.actif or not etape.file.actif:
                erreurs.append(
                    f"Étape {etape.ordre} : service ou file inactif."
                )

        return erreurs

    def publier(self, utilisateur=None):
        """Publie ce brouillon et archive la version publiée précédente."""
        with transaction.atomic():
            Parcours.objects.select_for_update().get(pk=self.parcours_id)
            version = type(self).objects.select_for_update().get(pk=self.pk)

            if version.statut != self.Statut.BROUILLON:
                raise ValidationError("Seul un brouillon peut être publié.")

            erreurs = version.verifier_publiable()

            if erreurs:
                raise ValidationError(erreurs)

            ancienne = version.parcours.version_publiee()

            if ancienne is not None:
                ancienne.statut = self.Statut.ARCHIVEE
                ancienne.save(update_fields=["statut"])

            version.statut = self.Statut.PUBLIEE
            version.date_publication = timezone.now()
            version.publiee_par = utilisateur
            # Le brouillon devient publié : on contourne le contrôle
            # d'immuabilité qui ne s'applique qu'après publication.
            super(ParcoursVersion, version).save(
                update_fields=["statut", "date_publication", "publiee_par"]
            )

        self.refresh_from_db()
        return self


class EtapeParcours(models.Model):
    """Étape d'une version : un service et sa file, dans un ordre donné."""

    version = models.ForeignKey(
        ParcoursVersion,
        on_delete=models.CASCADE,
        related_name="etapes",
    )

    ordre = models.PositiveSmallIntegerField()

    service = models.ForeignKey(
        "centres.Service",
        on_delete=models.PROTECT,
        related_name="etapes_parcours",
    )

    file = models.ForeignKey(
        "centres.FileAttente",
        on_delete=models.PROTECT,
        related_name="etapes_parcours",
    )

    class Meta:
        ordering = ["version", "ordre"]
        verbose_name = "Étape de parcours"
        verbose_name_plural = "Étapes de parcours"
        constraints = [
            models.UniqueConstraint(
                fields=["version", "ordre"],
                name="unique_ordre_etape_par_version",
            ),
        ]

    def __str__(self):
        return f"{self.ordre}. {self.service.nom}"

    def clean(self):
        super().clean()

        if self.file_id and self.service_id and self.file.service_id != self.service_id:
            raise ValidationError(
                {"file": "La file doit appartenir au service de l'étape."}
            )

        if self.service_id and self.version_id:
            if self.service.centre_id != self.version.parcours.centre_id:
                raise ValidationError(
                    {"service": "Le service doit appartenir au centre du parcours."}
                )

            if self.service.est_pharmacie:
                raise ValidationError(
                    {"service": "La pharmacie n'est jamais une étape du parcours."}
                )

    def _verifier_version_modifiable(self):
        version = ParcoursVersion.objects.get(pk=self.version_id)

        if not version.modifiable:
            raise ValidationError(
                "Les étapes d'une version publiée ne peuvent pas être modifiées."
            )

    def save(self, *args, **kwargs):
        self._verifier_version_modifiable()
        self.clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._verifier_version_modifiable()
        return super().delete(*args, **kwargs)


class QuotaParcours(models.Model):
    """
    Nombre de places par jour de semaine et par période (dentiste).
    Les plages de validité d'un même jour et d'une même période
    ne peuvent pas se chevaucher (contrainte PostgreSQL).
    """

    class Periode(models.TextChoices):
        MATIN = "MATIN", "Matin"
        APRES_MIDI = "APRES_MIDI", "Après-midi"

    class JourSemaine(models.IntegerChoices):
        LUNDI = 0, "Lundi"
        MARDI = 1, "Mardi"
        MERCREDI = 2, "Mercredi"
        JEUDI = 3, "Jeudi"
        VENDREDI = 4, "Vendredi"
        SAMEDI = 5, "Samedi"
        DIMANCHE = 6, "Dimanche"

    parcours = models.ForeignKey(
        Parcours,
        on_delete=models.PROTECT,
        related_name="quotas",
    )

    jour_semaine = models.PositiveSmallIntegerField(
        choices=JourSemaine.choices,
    )

    periode = models.CharField(
        max_length=10,
        choices=Periode.choices,
    )

    nombre_places = models.PositiveSmallIntegerField()

    date_debut = models.DateField()

    date_fin = models.DateField(
        null=True,
        blank=True,
        help_text="Laisser vide pour une validité sans fin.",
    )

    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["parcours", "jour_semaine", "periode", "-date_debut"]
        verbose_name = "Quota de parcours"
        verbose_name_plural = "Quotas de parcours"
        permissions = [
            ("deroger_quota", "Peut déroger à un quota atteint"),
            ("liberer_quota", "Peut libérer une place de quota"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(date_fin__isnull=True) | Q(date_fin__gte=F("date_debut")),
                name="quota_fin_apres_debut",
            ),
            ExclusionConstraint(
                name="quota_sans_chevauchement",
                expressions=[
                    ("parcours", RangeOperators.EQUAL),
                    ("jour_semaine", RangeOperators.EQUAL),
                    ("periode", RangeOperators.EQUAL),
                    (
                        Func(
                            F("date_debut"),
                            F("date_fin"),
                            Value("[]"),
                            function="DATERANGE",
                            output_field=DateRangeField(),
                        ),
                        RangeOperators.OVERLAPS,
                    ),
                ],
            ),
        ]

    def __str__(self):
        return (
            f"{self.parcours.prefixe} - {self.get_jour_semaine_display()} "
            f"{self.get_periode_display()} : {self.nombre_places} places"
        )

    def clean(self):
        super().clean()

        if self.date_fin and self.date_debut and self.date_fin < self.date_debut:
            raise ValidationError(
                {"date_fin": "La date de fin doit suivre la date de début."}
            )

        if self.parcours_id and self.date_debut:
            chevauchements = QuotaParcours.objects.filter(
                parcours_id=self.parcours_id,
                jour_semaine=self.jour_semaine,
                periode=self.periode,
            ).exclude(pk=self.pk)

            if self.date_fin:
                chevauchements = chevauchements.filter(date_debut__lte=self.date_fin)

            chevauchements = chevauchements.filter(
                Q(date_fin__isnull=True) | Q(date_fin__gte=self.date_debut)
            )

            if chevauchements.exists():
                raise ValidationError(
                    "Un quota existe déjà pour ce jour et cette période "
                    "sur une partie de ces dates. Terminez l'ancien quota "
                    "(date de fin) avant d'en créer un nouveau."
                )

    def valeurs(self):
        return {
            "parcours": self.parcours_id,
            "jour_semaine": self.jour_semaine,
            "periode": self.periode,
            "nombre_places": self.nombre_places,
            "date_debut": self.date_debut.isoformat() if self.date_debut else None,
            "date_fin": self.date_fin.isoformat() if self.date_fin else None,
        }


class ModificationQuota(models.Model):
    """Historique des modifications de quotas (auteur, date, avant/après)."""

    class Action(models.TextChoices):
        CREATION = "CREATION", "Création"
        MODIFICATION = "MODIFICATION", "Modification"
        SUPPRESSION = "SUPPRESSION", "Suppression"

    quota = models.ForeignKey(
        QuotaParcours,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="modifications",
    )

    parcours = models.ForeignKey(
        Parcours,
        on_delete=models.PROTECT,
        related_name="modifications_quotas",
    )

    action = models.CharField(max_length=15, choices=Action.choices)

    auteur = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        related_name="modifications_quotas",
    )

    valeurs_avant = models.JSONField(null=True, blank=True)
    valeurs_apres = models.JSONField(null=True, blank=True)

    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]
        verbose_name = "Modification de quota"
        verbose_name_plural = "Modifications de quotas"

    def __str__(self):
        return f"{self.get_action_display()} - {self.parcours} - {self.date:%d/%m/%Y %H:%M}"
