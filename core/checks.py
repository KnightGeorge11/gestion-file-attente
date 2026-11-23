"""
Vérifications au démarrage (relecture du 23/11/2026) : en production,
un réglage oublié ouvrirait la borne et l'écran à tous les clients
(proxy non déclaré) ou enverrait les cookies sans « Secure ».
"""

from django.conf import settings
from django.core.checks import Warning, register


@register(deploy=False)
def reglages_production(app_configs, **kwargs):
    if settings.DEBUG or getattr(settings, "EN_TEST", False):
        return []
    avertissements = []
    if not settings.PROXIES_DE_CONFIANCE:
        avertissements.append(Warning(
            "DJANGO_PROXIES_CONFIANCE est vide : derrière Nginx, tous les clients ont l'adresse "
            "du proxy (borne, écran et blocage des connexions faussés).",
            hint="Mettre DJANGO_PROXIES_CONFIANCE=127.0.0.1 dans .env (Nginx sur le serveur).",
            id="core.W001",
        ))
    if not settings.HTTPS:
        avertissements.append(Warning(
            "DJANGO_HTTPS=False : les cookies de session et de borne partent sans l'option Secure.",
            hint="Mettre DJANGO_HTTPS=True une fois le site servi en HTTPS par Nginx.",
            id="core.W002",
        ))
    return avertissements
