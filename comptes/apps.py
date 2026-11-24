from django.apps import AppConfig


class ComptesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'comptes'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_logged_out

        from .presence import oublier_presence

        user_logged_in.connect(oublier_presence, dispatch_uid="presence_connexion")
        user_logged_out.connect(oublier_presence, dispatch_uid="presence_deconnexion")
