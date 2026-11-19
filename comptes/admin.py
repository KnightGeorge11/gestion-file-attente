
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from centres.access import centres_autorises, est_administrateur_central
from core.admin import CentreAdminMixin

from .models import (
    Utilisateur,
    AffectationUtilisateur,
    AffectationService,
)


class AffectationServiceInline(admin.TabularInline):
    model = AffectationService
    extra = 0


class AffectationUtilisateurInline(admin.TabularInline):
    model = AffectationUtilisateur
    extra = 0
    fields = ("centre", "groupe", "role", "actif")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if est_administrateur_central(request.user):
            return queryset
        return queryset.filter(centre__in=centres_autorises(request.user))

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        if db_field.name == "centre" and not est_administrateur_central(request.user):
            kwargs["queryset"] = centres_autorises(request.user)
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
        # Administrateur d'un centre : comptes de ses centres, jamais ceux de
        # la plateforme (pas de changement de mot de passe d'un supérieur).
        return queryset.filter(
            affectations__centre__in=centres_autorises(request.user)
        ).exclude(is_superuser=True).exclude(type_compte=Utilisateur.TypeCompte.ADMINISTRATEUR).distinct()

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

    @admin.action(description="Désactiver les comptes sélectionnés")
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
