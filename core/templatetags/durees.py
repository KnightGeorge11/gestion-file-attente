from django import template

register = template.Library()


@register.filter
def duree(valeur):
    """Affiche une durée lisible : « 1 h 05 min », « 12 min 33 s », « 45 s »."""
    if valeur is None:
        return "—"
    secondes = int(valeur.total_seconds())
    if secondes < 0:
        return "—"
    heures, reste = divmod(secondes, 3600)
    minutes, secondes = divmod(reste, 60)
    if heures:
        return f"{heures} h {minutes:02d} min"
    if minutes:
        return f"{minutes} min {secondes:02d} s"
    return f"{secondes} s"
