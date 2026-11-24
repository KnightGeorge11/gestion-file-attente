"""
Comptes connectés (décision du 24/11/2026).

Un compte est « connecté » s'il a une page de la plateforme ouverte : les
pages se rafraîchissent seules toutes les 10 à 15 s, et chaque requête note
l'heure dans la session (au plus une écriture par minute). Au-delà de
5 minutes sans requête, le compte est hors ligne. Aucune table en plus :
l'information vit dans les sessions, qui disparaissent à la déconnexion.

Sert à l'administrateur de la plateforme (liste des comptes connectés) et au
relais de l'accueil par l'infirmerie quand aucun agent d'accueil n'est
connecté.
"""

from datetime import datetime, timedelta
from datetime import timezone as fuseau_utc

from django.conf import settings
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.utils import timezone

DUREE_PRESENCE = timedelta(minutes=5)
CLE_VU = "presence_vu_a"
CLE_IP = "presence_ip"
CLE_NAVIGATEUR = "presence_navigateur"
CLE_CACHE = "comptes:connectes"


def _duree_cache():
    # Pas de cache pendant les tests : chaque test part d'un état propre.
    return 0 if getattr(settings, "EN_TEST", False) else 15


class PresenceMiddleware:
    """Note l'heure de la dernière requête de l'agent dans sa session."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reponse = self.get_response(request)
        utilisateur = getattr(request, "user", None)
        session = getattr(request, "session", None)
        if utilisateur is not None and utilisateur.is_authenticated and session is not None:
            maintenant = timezone.now().timestamp()
            vu = session.get(CLE_VU)
            if not vu or maintenant - vu >= 60:
                from core.reseau import adresse_client

                session[CLE_VU] = maintenant
                session[CLE_IP] = adresse_client(request) or ""
                session[CLE_NAVIGATEUR] = request.headers.get("User-Agent", "")[:200]
                if session.get("_session_expiry") is None:
                    # Écrire dans la session repousserait son expiration à chaque minute :
                    # la déconnexion après SESSION_COOKIE_AGE (12 h) ne viendrait jamais.
                    # L'échéance est fixée une fois pour toutes (audit du 24/11/2026).
                    session.set_expiry(timezone.now() + timedelta(seconds=settings.SESSION_COOKIE_AGE))
                if not vu:
                    cache.delete(CLE_CACHE)   # nouvel arrivant : visible tout de suite
        return reponse


def utilisateurs_connectes():
    """
    {id utilisateur: {"vu_a": datetime, "centre_id": int|None, "ip": str,
    "navigateur": str, "sessions": int}} des comptes connectés.
    """
    duree = _duree_cache()
    if duree:
        en_cache = cache.get(CLE_CACHE)
        if en_cache is not None:
            return en_cache
    from centres.access import SESSION_CENTRE_KEY

    maintenant = timezone.now()
    limite = (maintenant - DUREE_PRESENCE).timestamp()
    connectes = {}
    for session in Session.objects.filter(expire_date__gt=maintenant).iterator():
        try:
            donnees = session.get_decoded()
        except Exception:  # noqa: BLE001 - session illisible : ignorée
            continue
        uid, vu = donnees.get("_auth_user_id"), donnees.get(CLE_VU)
        if not uid or not vu or vu < limite:
            continue
        uid = int(uid)
        actuel = connectes.get(uid)
        if actuel is None or vu > actuel["vu_a"].timestamp():
            connectes[uid] = {
                "vu_a": datetime.fromtimestamp(vu, tz=fuseau_utc.utc),
                "centre_id": donnees.get(SESSION_CENTRE_KEY),
                "ip": donnees.get(CLE_IP, ""),
                "navigateur": donnees.get(CLE_NAVIGATEUR, ""),
                "sessions": (actuel or {}).get("sessions", 0) + 1,
            }
        else:
            actuel["sessions"] += 1
    if duree:
        cache.set(CLE_CACHE, connectes, duree)
    return connectes


def oublier_presence(**kwargs):
    """Connexion ou déconnexion : la liste est recalculée à la prochaine demande."""
    cache.delete(CLE_CACHE)


ROLE_ACCUEIL = "Accueil"
ROLE_RELAIS_ACCUEIL = "Infirmerie"


def accueil_connecte(centre):
    """Vrai si un agent d'accueil du centre a une page ouverte sur ce centre."""
    from core.memo import memorise

    return memorise(("accueil_connecte", centre.pk), lambda: _accueil_connecte(centre))


def _accueil_connecte(centre):
    from .models import AffectationUtilisateur

    connectes = utilisateurs_connectes()
    if not connectes:
        return False
    ids = [uid for uid, info in connectes.items() if info["centre_id"] in (None, centre.pk)]
    return AffectationUtilisateur.objects.filter(
        centre=centre, actif=True, utilisateur_id__in=ids, utilisateur__is_active=True,
        groupe__name=ROLE_ACCUEIL,
    ).exists()


def relais_accueil(affectation, centre):
    """
    Infirmerie en relais de l'accueil (décision du 24/11/2026) : quand aucun
    agent d'accueil n'est connecté, l'infirmerie fait tout le travail de
    l'accueil (file de l'accueil, validation, demandes « Autre »).
    """
    return bool(
        affectation is not None
        and affectation.groupe_id
        and affectation.groupe.name == ROLE_RELAIS_ACCUEIL
        and (not accueil_connecte(centre) or _tient_un_ticket_de_l_accueil(affectation, centre))
    )


def _tient_un_ticket_de_l_accueil(affectation, centre):
    """
    L'infirmière qui a appelé un patient à l'accueil le termine, même si un
    agent d'accueil se connecte entre-temps : sinon personne ne peut plus
    agir sur ce ticket, et elle ne peut plus appeler (audit du 24/11/2026).
    """
    from circuit.models import EntreeFile
    from core.memo import memorise

    return memorise(("tient_accueil", affectation.utilisateur_id, centre.pk), lambda: EntreeFile.objects.filter(
        agent_id=affectation.utilisateur_id, statut__in=EntreeFile.STATUTS_TENUS,
        file__service__centre=centre, file__service__est_accueil=True,
    ).exists())


def marquer_present(utilisateur, centre=None):
    """Crée une session « connectée » pour ce compte (tests et démonstrations)."""
    from importlib import import_module

    from centres.access import SESSION_CENTRE_KEY

    moteur = import_module(settings.SESSION_ENGINE)
    session = moteur.SessionStore()
    session["_auth_user_id"] = str(utilisateur.pk)
    session[CLE_VU] = timezone.now().timestamp()
    if centre is not None:
        session[SESSION_CENTRE_KEY] = centre.pk
    session.create()
    cache.delete(CLE_CACHE)
    return session
