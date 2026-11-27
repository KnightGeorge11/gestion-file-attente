from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'

    def ready(self):
        from django.contrib import admin

        from . import checks  # noqa: F401 - enregistre les vérifications

        # Administration réservée à l'administrateur de la plateforme (décision
        # du 24/11/2026) : l'administrateur de centre n'y entre plus, même en
        # tapant l'adresse ; il garde ses pages de l'application (Borne, Écran…).
        admin.site.has_permission = lambda request: acces_administration(request)
        # Pas de menu latéral listant les 24 modules (27/11/2026) : l'accueil est rangé par tâche.
        admin.site.enable_nav_sidebar = False


def acces_administration(request):
    from centres.access import est_administrateur_central

    return request.user.is_active and request.user.is_staff and est_administrateur_central(request.user)
