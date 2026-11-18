from datetime import timedelta

from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils import timezone


def echecs_recents(identifiant):
    """
    Nombre d'échecs de connexion récents pour cet identifiant, comptés dans
    le journal d'audit depuis la dernière connexion réussie (au plus sur la
    fenêtre de blocage).
    """
    from audit.models import JournalAudit

    identifiant = (identifiant or "").strip().lower()
    debut = timezone.now() - timedelta(minutes=settings.CONNEXION_BLOCAGE_MINUTES)
    derniere_reussite = (
        JournalAudit.objects.filter(
            action=JournalAudit.Action.CONNEXION, utilisateur__username=identifiant, date__gte=debut
        )
        .order_by("-date")
        .values_list("date", flat=True)
        .first()
    )
    if derniere_reussite:
        debut = derniere_reussite
    return JournalAudit.objects.filter(
        action=JournalAudit.Action.ECHEC_CONNEXION,
        identifiant_saisi__iexact=identifiant,
        date__gte=debut,
    ).count()


class ConnexionForm(AuthenticationForm):
    """Connexion avec blocage temporaire après plusieurs échecs."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "bloque": (
            "Trop de tentatives échouées pour cet identifiant. "
            "Réessayez dans %(minutes)s minutes ou contactez l'administrateur."
        ),
    }

    def clean(self):
        identifiant = self.data.get("username", "")
        if identifiant and echecs_recents(identifiant) >= settings.CONNEXION_MAX_ECHECS:
            raise ValidationError(
                self.error_messages["bloque"],
                code="bloque",
                params={"minutes": settings.CONNEXION_BLOCAGE_MINUTES},
            )
        return super().clean()
