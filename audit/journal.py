from core.reseau import adresse_client

from .models import JournalAudit
from .threadlocal import requete_courante


def _ip(request):
    # Sans requête explicite (signaux de l'administration) : la requête en
    # cours, pour que chaque action d'administration garde son adresse IP.
    return adresse_client(request if request is not None else requete_courante())


def journaliser(action, utilisateur=None, objet=None, centre=None, details=None,
                request=None, identifiant_saisi=""):
    """Ajoute une entrée au journal d'audit."""
    if utilisateur is not None and not getattr(utilisateur, "is_authenticated", False):
        utilisateur = None

    if centre is None and objet is not None:
        centre = getattr(objet, "centre", None)

    return JournalAudit.objects.create(
        utilisateur=utilisateur,
        identifiant_saisi=identifiant_saisi[:150],
        action=action,
        centre=centre,
        objet_type=(
            f"{objet._meta.app_label}.{objet._meta.model_name}" if objet is not None else ""
        ),
        objet_id=str(objet.pk) if objet is not None and objet.pk is not None else "",
        objet_libelle=str(objet)[:255] if objet is not None else "",
        details=details or {},
        adresse_ip=_ip(request),
    )
