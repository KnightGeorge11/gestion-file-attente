from django.contrib.auth.backends import ModelBackend

from centres.models import Centre, Service

from centres.access import est_administrateur_central

from .permissions import a_droit, permissions_tous_centres


class AffectationBackend(ModelBackend):
    """
    Backend d'authentification du projet.

    - L'identifiant est comparé en minuscules.
    - is_superuser est traité en premier dans has_perm.
    - Avec un objet Centre ou Service, le droit est vérifié dans la portée
      de l'affectation correspondante. Sans objet, le contrôle sert
      uniquement à l'affichage des menus (union des affectations) et aux
      droits de l'administration Django.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username:
            username = username.strip().lower()

        return super().authenticate(
            request, username=username, password=password, **kwargs
        )

    def get_all_permissions(self, user_obj, obj=None):
        if not user_obj.is_active or user_obj.is_anonymous:
            return set()

        if obj is not None:
            return set()

        return super().get_all_permissions(user_obj) | permissions_tous_centres(user_obj)

    def has_module_perms(self, user_obj, app_label):
        if user_obj.is_active and est_administrateur_central(user_obj):
            return True
        return super().has_module_perms(user_obj, app_label)

    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_active:
            return False

        # Administrateur de la plateforme (superutilisateur ou type « ADMIN ») :
        # tous les droits, sur tous les centres.
        if user_obj.is_superuser or est_administrateur_central(user_obj):
            return True

        if isinstance(obj, Service):
            return a_droit(user_obj, perm, obj.centre, obj)

        if isinstance(obj, Centre):
            return a_droit(user_obj, perm, obj)

        return perm in self.get_all_permissions(user_obj)
