
from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, Permission
from django.db.models import Q

from centres.access import est_administrateur_central
from core.admin import CentreAdminMixin, centres_administres

from .models import (
    Utilisateur,
    AffectationUtilisateur,
    AffectationService,
)


def permissions_metier():
    """
    Permissions qu'un administrateur de centre peut ajouter à une
    affectation : uniquement les permissions métier des rôles (appeler,
    orienter, quota…), jamais celles de l'administration Django (centres,
    rôles, journal d'audit…). Audit du 20/11/2026 : sans cette liste, il
    pouvait s'attribuer des droits sur toute la plateforme.
    """
    from .management.commands.initialiser_roles import ROLES

    codes = {code for liste in ROLES.values() for code in liste}
    filtre = Q(pk__in=[])
    for code in codes:
        app, codename = code.split(".")
        filtre |= Q(content_type__app_label=app, codename=codename)
    return Permission.objects.filter(filtre)


ROLE_ADMIN_CENTRE = "Administrateur de centre"


def roles_metier():
    """
    Rôles (groupes) qu'un administrateur de centre peut attribuer : tous les
    rôles métier, sauf « Administrateur de centre » (réservé à
    l'administrateur de la plateforme).
    """
    from .management.commands.initialiser_roles import ROLES

    return Group.objects.filter(name__in=ROLES).exclude(name=ROLE_ADMIN_CENTRE)


def comptes_geres(utilisateur):
    """
    Comptes qu'un administrateur de centre peut voir, modifier et dont il
    peut changer le mot de passe : ni les comptes de la plateforme, ni ceux
    qui ont une affectation dans un centre qu'il n'administre pas (sinon il
    pourrait prendre la main sur un compte d'un autre centre).
    """
    comptes = Utilisateur.objects.all()
    if est_administrateur_central(utilisateur):
        return comptes
    centres = centres_administres(utilisateur, Utilisateur)
    hors_perimetre = AffectationUtilisateur.objects.exclude(centre__in=centres).values("utilisateur")
    # Les autres administrateurs de centre relèvent de la plateforme seule
    # (relecture du 23/11/2026) : pas de changement de mot de passe, de
    # désactivation ni de rétrogradation entre administrateurs d'un centre.
    autres_administrateurs = (
        comptes.filter(
            Q(type_compte=Utilisateur.TypeCompte.ADMIN_CENTRE)
            | Q(affectations__groupe__name=ROLE_ADMIN_CENTRE)
        )
        .exclude(pk=utilisateur.pk)
        .values("pk")
    )
    return (
        comptes.exclude(is_superuser=True)
        .exclude(type_compte=Utilisateur.TypeCompte.ADMINISTRATEUR)
        .exclude(pk__in=hors_perimetre)
        .exclude(pk__in=autres_administrateurs)
    )


class AffectationServiceInline(admin.TabularInline):
    model = AffectationService
    extra = 0

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        champ = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if db_field.name == "service" and not est_administrateur_central(request.user):
            champ.queryset = champ.queryset.filter(
                centre__in=centres_administres(request.user, AffectationUtilisateur)
            )
        return champ


class AffectationUtilisateurInline(admin.TabularInline):
    model = AffectationUtilisateur
    extra = 0
    fields = ("centre", "groupe", "role", "actif")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if est_administrateur_central(request.user):
            return queryset
        # Une affectation « Administrateur de centre » n'est jamais dans le
        # formulaire : son rôle n'est pas dans la liste proposée, le navigateur
        # enverrait un rôle vide et l'administrateur perdrait ses droits sans
        # le voir (relecture du 23/11/2026).
        return queryset.filter(
            centre__in=centres_administres(request.user, AffectationUtilisateur)
        ).exclude(groupe__name=ROLE_ADMIN_CENTRE)

    def _sa_propre_fiche(self, request, obj):
        return (obj is not None and obj.pk == request.user.pk
                and not est_administrateur_central(request.user))

    # Sur sa propre fiche, l'administrateur de centre ne touche pas à ses
    # affectations (même règle que la page Affectation).
    def has_add_permission(self, request, obj=None):
        return not self._sa_propre_fiche(request, obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return not self._sa_propre_fiche(request, obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return not self._sa_propre_fiche(request, obj) and super().has_delete_permission(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if not est_administrateur_central(request.user):
            if db_field.name == "centre":
                kwargs["queryset"] = centres_administres(request.user, AffectationUtilisateur)
            elif db_field.name == "groupe":
                kwargs["queryset"] = roles_metier()
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Utilisateur)
class UtilisateurAdmin(UserAdmin):
    list_display = (
        "username",
        "first_name",
        "last_name",
        "email",
        "type_compte",
        "is_active",
    )

    list_filter = (
        "type_compte",
        "is_active",
        "is_staff",
        "is_superuser",
    )

    fieldsets = UserAdmin.fieldsets + (
        (
            "Informations professionnelles",
            {
                "fields": (
                    "type_compte",
                    "telephone",
                )
            },
        ),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        (
            "Informations professionnelles",
            {
                "fields": (
                    "type_compte",
                    "telephone",
                )
            },
        ),
    )

    inlines = (AffectationUtilisateurInline,)
    actions = ("desactiver",)

    # Champs donnant des droits transversaux : modifiables uniquement par
    # l'administrateur central (pas d'élévation de privilèges).
    CHAMPS_SENSIBLES = ("is_superuser", "is_staff", "type_compte", "groups", "user_permissions")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if est_administrateur_central(request.user):
            return queryset
        # Administrateur d'un centre : comptes des centres qu'il administre,
        # jamais ceux de la plateforme ni ceux rattachés à un autre centre
        # (pas de changement de mot de passe hors de son périmètre).
        return queryset.filter(
            affectations__centre__in=centres_administres(request.user, Utilisateur),
            pk__in=comptes_geres(request.user),
        ).distinct()

    def get_readonly_fields(self, request, obj=None):
        champs = super().get_readonly_fields(request, obj)
        if est_administrateur_central(request.user):
            return champs
        return tuple(champs) + self.CHAMPS_SENSIBLES

    def get_fieldsets(self, request, obj=None):
        fieldsets = super().get_fieldsets(request, obj)
        if obj is None and not est_administrateur_central(request.user):
            # Création : les champs sensibles sont absents du formulaire.
            return [
                (titre, {**options, "fields": tuple(
                    f for f in options["fields"] if f not in self.CHAMPS_SENSIBLES
                )})
                for titre, options in fieldsets
            ]
        return fieldsets

    def has_delete_permission(self, request, obj=None):
        # Les comptes ne sont jamais supprimés, seulement désactivés.
        return False

    @admin.action(description="Désactiver les comptes sélectionnés", permissions=["change"])
    def desactiver(self, request, queryset):
        for utilisateur in queryset:
            utilisateur.is_active = False
            utilisateur.save(update_fields=["is_active"])


@admin.register(AffectationUtilisateur)
class AffectationUtilisateurAdmin(CentreAdminMixin, admin.ModelAdmin):
    inlines = (AffectationServiceInline,)
    filter_horizontal = ("permissions",)

    list_display = (
        "utilisateur",
        "centre",
        "groupe",
        "role",
        "actif",
    )

    list_filter = (
        "centre",
        "groupe",
        "role",
        "actif",
    )

    def has_delete_permission(self, request, obj=None):
        # Désactiver l'affectation plutôt que la supprimer (historique).
        return False

    def has_change_permission(self, request, obj=None):
        # Personne ne modifie sa propre affectation, sauf l'administrateur
        # de la plateforme (pas d'auto-attribution de droits).
        if obj is not None and obj.utilisateur_id == request.user.pk and not est_administrateur_central(request.user):
            return False
        return super().has_change_permission(request, obj)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        champ = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if not est_administrateur_central(request.user):
            if db_field.name == "utilisateur":
                # Seulement les comptes déjà affectés à ses centres : un compte
                # sans affectation (créé par la plateforme pour un autre
                # centre) n'est pas récupérable (relecture du 23/11/2026).
                champ.queryset = comptes_geres(request.user).filter(
                    affectations__centre__in=centres_administres(request.user, AffectationUtilisateur)
                ).exclude(pk=request.user.pk).distinct()
            elif db_field.name == "groupe":
                champ.queryset = roles_metier()
        return champ

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        champ = super().formfield_for_manytomany(db_field, request, **kwargs)
        if db_field.name == "permissions" and not est_administrateur_central(request.user):
            champ.queryset = permissions_metier()
        return champ

    search_fields = (
        "utilisateur__username",
        "utilisateur__first_name",
        "utilisateur__last_name",
        "centre__nom",
    )

    list_select_related = (
        "utilisateur",
        "centre",
    )


# Les rôles (groupes) sont communs à tous les centres : seul l'administrateur
# de la plateforme les voit et les modifie.
admin.site.unregister(Group)


@admin.register(Group)
class RoleAdmin(GroupAdmin):
    def _central(self, request):
        return est_administrateur_central(request.user)

    def has_module_permission(self, request):
        return self._central(request)

    def has_view_permission(self, request, obj=None):
        return self._central(request)

    def has_add_permission(self, request):
        return self._central(request)

    def has_change_permission(self, request, obj=None):
        return self._central(request)

    def has_delete_permission(self, request, obj=None):
        return self._central(request)
