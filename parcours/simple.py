"""
Ajout d'un service à la borne en une seule étape (décision du 27/11/2026).

Avant, il fallait dans l'administration : créer le service (et sa file),
créer un parcours, créer un brouillon, y ajouter les étapes (accueil, puis
le service), puis publier. Ici, on donne un nom et on indique si le patient
passe d'abord par l'infirmerie : tout le reste est fait d'un coup.

Un « parcours », c'est le chemin du patient pour un choix de la borne :
« Médecin » = Accueil → Infirmerie → Médecin. La lettre des tickets
(M 001) est celle du parcours.
"""

import re
import string
import unicodedata

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max

from centres.models import Service

from .models import EtapeParcours, Parcours


def _ascii_majuscules(texte):
    texte = unicodedata.normalize("NFKD", texte or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]+", "", texte.upper())


def lettres_libres(centre):
    from circuit.services import PREFIXE_AUTRE

    prises = set(Parcours.objects.filter(centre=centre).values_list("prefixe", flat=True))
    return [l for l in string.ascii_uppercase if l not in prises and l != PREFIXE_AUTRE]


def lettre_proposee(centre, nom):
    """Première lettre libre du nom (« Kiné » → K), sinon la première lettre libre."""
    libres = lettres_libres(centre)
    for lettre in _ascii_majuscules(nom):
        if lettre in libres:
            return lettre
    return libres[0] if libres else None


def _code_libre(modele, centre, base, longueur):
    base = (_ascii_majuscules(base) or "SRV")[:longueur]
    code, rang = base, 1
    while modele.objects.filter(centre=centre, code=code).exists():
        rang += 1
        code = f"{base[:longueur - len(str(rang))]}{rang}"
    return code


def service_infirmerie(centre):
    return (
        Service.objects.filter(centre=centre, actif=True, code__iexact="INF").first()
        or Service.objects.filter(centre=centre, actif=True, nom__iexact="Infirmerie").first()
    )


def circuit(parcours):
    """« Accueil → Infirmerie → Médecin » (version en vigueur), ou None."""
    version = parcours.version_publiee()
    if version is None:
        return None
    return " → ".join(e.service.nom for e in version.etapes.select_related("service").order_by("ordre"))


@transaction.atomic
def ajouter_service_borne(centre, nom, lettre="", par_infirmerie=False, utilisateur=None):
    """Crée (ou reprend) le service, son parcours et la version publiée. Retourne le parcours."""
    nom = " ".join((nom or "").split())[:100]
    if not nom:
        raise ValidationError("Donnez le nom du service (ex. Kinésithérapie).")
    accueil = Service.objects.filter(centre=centre, est_accueil=True, actif=True).first()
    if accueil is None:
        raise ValidationError("Ce centre n'a pas de service d'accueil actif.")

    existant = Parcours.objects.filter(centre=centre, libelle_borne__iexact=nom).first()
    if existant is not None:
        if existant.actif and existant.visible_borne:
            raise ValidationError(f"« {nom} » est déjà proposé à la borne.")
        # Service retiré autrefois : on le remet tel qu'il était.
        existant.actif = existant.visible_borne = True
        existant.save(update_fields=["actif", "visible_borne", "date_modification"])
        return existant

    service = Service.objects.filter(centre=centre, nom__iexact=nom).first()
    if service is not None and (service.est_accueil or service.est_pharmacie):
        raise ValidationError("L'accueil et la pharmacie ne se choisissent pas à la borne.")
    if service is None:
        ordre = (Service.objects.filter(centre=centre).aggregate(m=Max("ordre"))["m"] or 0) + 1
        service = Service.objects.create(
            centre=centre, code=_code_libre(Service, centre, nom, 3), nom=nom, ordre=ordre,
        )
    elif not service.actif:
        service.actif = True
        service.save()

    lettre = (lettre or "").strip().upper() or lettre_proposee(centre, nom)
    if not lettre or lettre not in lettres_libres(centre):
        raise ValidationError(f"La lettre « {lettre} » est déjà utilisée par un autre service de la borne.")

    ordre_borne = (Parcours.objects.filter(centre=centre).aggregate(m=Max("ordre_affichage"))["m"] or 0) + 1
    parcours = Parcours(
        centre=centre, code=_code_libre(Parcours, centre, nom, 20), prefixe=lettre,
        libelle_borne=nom, ordre_affichage=ordre_borne,
    )
    parcours.full_clean()
    parcours.save()

    etapes = [accueil]
    infirmerie = service_infirmerie(centre) if par_infirmerie else None
    if infirmerie is not None and infirmerie.pk != service.pk:
        etapes.append(infirmerie)
    etapes.append(service)
    version = parcours.creer_brouillon()
    for ordre, etape in enumerate(etapes, start=1):
        EtapeParcours.objects.create(version=version, ordre=ordre, service=etape, file=etape.file_par_defaut())
    version.publier(utilisateur)
    return parcours
