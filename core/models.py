from django.db import models

from .managers import CentreManager


class ModeleCentre(models.Model):
    """
    Modèle abstrait pour les données appartenant à un centre.

    Les futurs modèles métier pourront hériter de cette classe.
    """

    centre = models.ForeignKey(
        "centres.Centre",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s",
    )

    objects = CentreManager()

    class Meta:
        abstract = True