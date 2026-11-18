
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

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

    def has_delete_permission(self, request, obj=None):
        # Les comptes ne sont jamais supprimés, seulement désactivés.
        return False

    @admin.action(description="Désactiver les comptes sélectionnés")
    def desactiver(self, request, queryset):
        for utilisateur in queryset:
            utilisateur.is_active = False
            utilisateur.save(update_fields=["is_active"])


@admin.register(AffectationUtilisateur)
class AffectationUtilisateurAdmin(admin.ModelAdmin):
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
