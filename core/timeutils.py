from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.utils import timezone


def fuseau_horaire_centre(centre):
    """Retourne le fuseau horaire configuré pour un centre."""

    if centre is None:
        raise ValueError("Aucun centre fourni.")

    try:
        return ZoneInfo(centre.fuseau_horaire)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        raise ValueError(
            f"Fuseau horaire invalide pour le centre {centre}."
        )


def maintenant_centre(centre):
    """Retourne la date et l'heure actuelles dans le fuseau du centre."""

    return timezone.localtime(
        timezone.now(),
        fuseau_horaire_centre(centre),
    )


def convertir_heure_centre(date_heure, centre):
    """Convertit une date/heure existante vers le fuseau du centre."""

    if timezone.is_naive(date_heure):
        raise ValueError("La date/heure doit être avec fuseau horaire.")

    return timezone.localtime(
        date_heure,
        fuseau_horaire_centre(centre),
    )