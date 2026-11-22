from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from core.admin import CentreAdminMixin

from .models import (
    EtapeParcours,
    ModificationQuota,
    Parcours,
    ParcoursVersion,
    QuotaParcours,
)
from .quotas import enregistrer_quota, supprimer_quota


@admin.register(Parcours)
class ParcoursAdmin(CentreAdminMixin, admin.ModelAdmin):
    list_display = (
        "prefixe",
        "libelle_borne",
        "centre",
        "ordre_affichage",
        "visible_borne",
        "reserve_accueil",
        "soumis_quota",
        "actif",
        "version_en_vigueur",
    )

    list_filter = ("centre", "visible_borne", "soumis_quota", "actif")
    search_fields = ("code", "libelle_borne")
    list_select_related = ("centre",)
    actions = ("creer_brouillon",)

    @admin.display(description="Version publiée")
    def version_en_vigueur(self, obj):
        version = obj.version_publiee()
        return f"v{version.numero}" if version else "—"

    @admin.action(description="Créer un brouillon (nouvelle version)", permissions=["change"])
    def creer_brouillon(self, request, queryset):
        for parcours in queryset:
            brouillon = parcours.creer_brouillon()
            messages.info(
                request,
                f"{parcours} : brouillon v{brouillon.numero} prêt à modifier.",
            )


class EtapeParcoursInline(admin.TabularInline):
    model = EtapeParcours
    extra = 0

    def _modifiable(self, obj):
        return obj is not None and obj.modifiable

    def has_add_permission(self, request, obj=None):
        return self._modifiable(obj) and super().has_add_permission(request, obj)

    def has_change_permission(self, request, obj=None):
        return self._modifiable(obj) and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return self._modifiable(obj) and super().has_delete_permission(request, obj)


@admin.register(ParcoursVersion)
class ParcoursVersionAdmin(CentreAdminMixin, admin.ModelAdmin):
    chemin_centre = "parcours__centre"

    list_display = (
        "parcours",
        "numero",
        "statut",
        "date_publication",
        "publiee_par",
    )

    list_filter = ("statut", "parcours__centre")
    list_select_related = ("parcours", "parcours__centre", "publiee_par")
    readonly_fields = (
        "parcours",
        "numero",
        "statut",
        "date_creation",
        "date_publication",
        "publiee_par",
    )
    inlines = (EtapeParcoursInline,)
    actions = ("publier",)

    def has_add_permission(self, request):
        # Les versions se créent depuis la liste des parcours
        # (action « Créer un brouillon »).
        return False

    def has_delete_permission(self, request, obj=None):
        if obj is not None and not obj.modifiable:
            return False
        return super().has_delete_permission(request, obj)

    @admin.action(description="Publier les brouillons sélectionnés", permissions=["change"])
    def publier(self, request, queryset):
        for version in queryset:
            try:
                version.publier(request.user)
            except ValidationError as erreur:
                messages.error(
                    request,
                    f"{version} : " + " ".join(erreur.messages),
                )
            else:
                messages.success(request, f"{version.parcours} : v{version.numero} publiée.")


@admin.register(QuotaParcours)
class QuotaParcoursAdmin(CentreAdminMixin, admin.ModelAdmin):
    chemin_centre = "parcours__centre"

    list_display = (
        "parcours",
        "jour_semaine",
        "periode",
        "nombre_places",
        "date_debut",
        "date_fin",
    )

    list_filter = ("parcours", "jour_semaine", "periode")
    list_select_related = ("parcours", "parcours__centre")

    def save_model(self, request, obj, form, change):
        enregistrer_quota(obj, request.user)

    def delete_model(self, request, obj):
        supprimer_quota(obj, request.user)

    def delete_queryset(self, request, queryset):
        for quota in queryset:
            supprimer_quota(quota, request.user)


@admin.register(ModificationQuota)
class ModificationQuotaAdmin(CentreAdminMixin, admin.ModelAdmin):
    chemin_centre = "parcours__centre"

    list_display = ("date", "parcours", "action", "auteur")
    list_filter = ("action", "parcours")
    list_select_related = ("parcours", "parcours__centre", "auteur")
    readonly_fields = (
        "quota",
        "parcours",
        "action",
        "auteur",
        "valeurs_avant",
        "valeurs_apres",
        "date",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
