"""
Outils de test.

Depuis le 24/11/2026, l'administration est réservée à l'administrateur de la
plateforme. Le filtrage par centre de l'administration reste dans le code
(seconde protection) : ses tests simulent l'ancien accès de l'administrateur
de centre pour continuer à le vérifier.
"""

from unittest import mock


def _acces_avant_le_24_11(request):
    return request.user.is_active and request.user.is_staff


acces_administration_de_centre = mock.patch("core.apps.acces_administration", _acces_avant_le_24_11)
