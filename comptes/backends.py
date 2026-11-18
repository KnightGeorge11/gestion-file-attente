from django.contrib.auth.backends import ModelBackend

from centres.models import Centre, Service

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

    def has_perm(self, user_obj, perm, obj=None):
        if not user_obj.is_active:
            return False

        if user_obj.is_superuser:
            return True

        if isinstance(obj, Service):
            return a_droit(user_obj, perm, obj.centre, obj)

        if isinstance(obj, Centre):
            return a_droit(user_obj, perm, obj)

        return perm in self.get_all_permissions(user_obj)
