"""
Adresse IP du client, y compris derrière un proxy (Nginx).

Derrière Nginx, REMOTE_ADDR est l'adresse du proxy (127.0.0.1) : toutes les
requêtes sembleraient venir du réseau local, et le blocage par IP serait
inopérant. L'en-tête X-Real-IP (posé par Nginx) n'est lu que si la requête
vient d'un proxy de confiance (DJANGO_PROXIES_CONFIANCE) : un client ne peut
pas se faire passer pour une autre adresse.
"""

import ipaddress

from django.conf import settings


def _ip_valide(valeur):
    try:
        return str(ipaddress.ip_address((valeur or "").strip()))
    except ValueError:
        return None


def adresse_client(request):
    """Adresse IP du client (texte), ou None si elle est illisible."""
    if request is None:
        return None
    distante = _ip_valide(request.META.get("REMOTE_ADDR"))
    if distante is not None and distante in getattr(settings, "PROXIES_DE_CONFIANCE", ()):
        reelle = _ip_valide(request.META.get("HTTP_X_REAL_IP"))
        if reelle is not None:
            return reelle
    return distante


def est_reseau_local(request):
    adresse = adresse_client(request)
    if adresse is None:
        return False
    ip = ipaddress.ip_address(adresse)
    return ip.is_private or ip.is_loopback


def est_adresse_privee(valeur):
    """Adresse du réseau local (imprimantes) : privée, ni boucle locale ni lien local."""
    try:
        ip = ipaddress.ip_address(str(valeur))
    except ValueError:
        return False
    return ip.is_private and not (ip.is_loopback or ip.is_link_local or ip.is_unspecified)
