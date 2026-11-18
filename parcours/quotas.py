"""
Règles de quota des parcours (dentiste).

Les places réservées et restantes seront calculées par le module circuit,
à partir des réservations faites lors de l'identification à l'accueil.
"""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.timeutils import convertir_heure_centre

from .models import ModificationQuota, QuotaParcours


def periode_pour(centre, date_heure=None):
    """
    Retourne (date locale, jour de la semaine, période) dans le fuseau
    du centre. Le matin se termine à l'heure de coupure du centre
    (12 h 00 par défaut), qui ouvre l'après-midi.
    """
    if date_heure is None:
        date_heure = timezone.now()

    locale = convertir_heure_centre(date_heure, centre)

    if locale.time() < centre.heure_coupure_matin:
        periode = QuotaParcours.Periode.MATIN
    else:
        periode = QuotaParcours.Periode.APRES_MIDI

    return locale.date(), locale.weekday(), periode


def quota_applicable(parcours, date_heure=None):
    """
    Quota en vigueur pour ce parcours au moment donné.

    Retourne None si aucun quota n'est configuré pour ce jour et cette
    période : pour un parcours soumis à quota, cela signifie que le
    service est fermé (samedi après-midi, dimanche) et qu'aucune
    identification n'est possible.
    """
    date_locale, jour, periode = periode_pour(parcours.centre, date_heure)

    return (
        QuotaParcours.objects
        .filter(
            parcours=parcours,
            jour_semaine=jour,
            periode=periode,
            date_debut__lte=date_locale,
        )
        .filter(Q(date_fin__isnull=True) | Q(date_fin__gte=date_locale))
        .first()
    )


def enregistrer_quota(quota, auteur):
    """Crée ou modifie un quota et historise l'opération."""
    with transaction.atomic():
        if quota.pk:
            avant = QuotaParcours.objects.get(pk=quota.pk).valeurs()
            action = ModificationQuota.Action.MODIFICATION
        else:
            avant = None
            action = ModificationQuota.Action.CREATION

        quota.full_clean()
        quota.save()

        ModificationQuota.objects.create(
            quota=quota,
            parcours_id=quota.parcours_id,
            action=action,
            auteur=auteur,
            valeurs_avant=avant,
            valeurs_apres=quota.valeurs(),
        )

    return quota


def supprimer_quota(quota, auteur):
    """Supprime un quota et historise l'opération."""
    with transaction.atomic():
        ModificationQuota.objects.create(
            quota=quota,
            parcours_id=quota.parcours_id,
            action=ModificationQuota.Action.SUPPRESSION,
            auteur=auteur,
            valeurs_avant=quota.valeurs(),
            valeurs_apres=None,
        )
        quota.delete()
