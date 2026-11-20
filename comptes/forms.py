from datetime import timedelta

from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils import timezone


def echecs_recents(identifiant, adresse_ip=None):
    """
    Nombre d'échecs de connexion récents pour cet identifiant, comptés dans
    le journal d'audit depuis la dernière connexion réussie (au plus sur la
    fenêtre de blocage).

    Avec `adresse_ip`, seuls les échecs venant de cette adresse comptent :
    quelqu'un qui se trompe ailleurs ne bloque pas le titulaire du compte
    sur son propre poste.
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
    echecs = JournalAudit.objects.filter(
        action=JournalAudit.Action.ECHEC_CONNEXION,
        identifiant_saisi__iexact=identifiant,
        date__gte=debut,
    )
    if adresse_ip:
        echecs = echecs.filter(adresse_ip=adresse_ip)
    return echecs.count()


def echecs_depuis_adresse(adresse_ip):
    """Échecs récents venant d'une adresse IP, tous identifiants confondus."""
    from audit.models import JournalAudit

    if not adresse_ip:
        return 0
    debut = timezone.now() - timedelta(minutes=settings.CONNEXION_BLOCAGE_MINUTES)
    return JournalAudit.objects.filter(
        action=JournalAudit.Action.ECHEC_CONNEXION, adresse_ip=adresse_ip, date__gte=debut
    ).count()


class ConnexionForm(AuthenticationForm):
    """Connexion avec blocage temporaire après plusieurs échecs."""

    error_messages = {
        **AuthenticationForm.error_messages,
        "bloque": (
            "Trop de tentatives échouées pour cet identifiant. "
            "Réessayez dans %(minutes)s minutes ou contactez l'administrateur."
        ),
        "bloque_ip": (
            "Trop de tentatives échouées depuis ce poste. "
            "Réessayez dans %(minutes)s minutes ou contactez l'administrateur."
        ),
    }

    def clean(self):
        from core.reseau import adresse_client

        identifiant = self.data.get("username", "")
        adresse = adresse_client(self.request)
        params = {"minutes": settings.CONNEXION_BLOCAGE_MINUTES}
        if adresse and echecs_depuis_adresse(adresse) >= settings.CONNEXION_MAX_ECHECS_IP:
            raise ValidationError(self.error_messages["bloque_ip"], code="bloque_ip", params=params)
        if identifiant and echecs_recents(identifiant, adresse) >= settings.CONNEXION_MAX_ECHECS:
            raise ValidationError(self.error_messages["bloque"], code="bloque", params=params)
        return super().clean()
