from django.db import models


class CentreQuerySet(models.QuerySet):
    """Filtrage des données métier par centre."""

    def pour_centre(self, centre):
        if centre is None or not getattr(centre, "pk", None):
            return self.none()

        return self.filter(centre_id=centre.pk)

    def pour_utilisateur(self, utilisateur, centre_actif):
        """Retourne uniquement les données du centre actif autorisé."""
        if not utilisateur or not utilisateur.is_authenticated:
            return self.none()

        if centre_actif is None:
            return self.none()

        from centres.access import utilisateur_peut_acceder_centre

        if not utilisateur_peut_acceder_centre(
            utilisateur,
            centre_actif,
        ):
            return self.none()

        return self.pour_centre(centre_actif)


class CentreManager(models.Manager.from_queryset(CentreQuerySet)):
    """Manager commun aux modèles liés à un centre."""
    pass