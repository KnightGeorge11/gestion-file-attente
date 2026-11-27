from django.contrib import admin, messages
from django.core.exceptions import ValidationError

from core.admin import CentreAdminMixin, etat_actif, oui_non, pastille

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
    aide_liste = ("Un parcours = un bouton de la borne (Médecin, Dentiste…) : la lettre des tickets (M 001) et le "
                  "chemin du patient (Accueil → Infirmerie → Médecin). Pour ajouter un service à la borne ou l'en "
                  "retirer, le plus simple est l'onglet « Borne » de l'application. Ici : réglages avancés (ordre, "
                  "places limitées du dentiste, changement du chemin par « Créer un brouillon », puis publication).")
    list_display = ("libelle_borne", "prefixe", "centre", "chemin", "ordre_affichage", "a_la_borne", "quota",
                    "etat")
    list_display_links = ("libelle_borne",)

    @admin.display(description="À la borne", ordering="visible_borne")
    def a_la_borne(self, obj):
        if not obj.visible_borne:
            return pastille("Non", "neutre")
        return pastille("Accueil seulement", "attention") if obj.reserve_accueil else pastille("Oui", "ok")

    @admin.display(description="Quota", ordering="soumis_quota")
    def quota(self, obj):
        return oui_non(obj.soumis_quota)

    @admin.display(description="État", ordering="actif")
    def etat(self, obj):
        return etat_actif(obj.actif)

    list_filter = ("centre", "visible_borne", "soumis_quota", "actif")
    search_fields = ("code", "libelle_borne")
    fieldsets = (
        (None, {"fields": ("centre", "libelle_borne", "prefixe", "visible_borne", "actif")}),
        ("Places limitées", {"fields": ("soumis_quota",),
                             "description": "Pour le dentiste : nombre de patients par demi-journée (onglet Réglages)."}),
        ("Avancé", {"fields": ("code", "ordre_affichage", "reserve_accueil"), "classes": ("collapse",)}),
    )
    list_select_related = ("centre",)
    actions = ("creer_brouillon",)

    @admin.display(description="Chemin du patient")
    def chemin(self, obj):
        from .simple import circuit

        return circuit(obj) or "— (pas encore publié)"

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

    aide_liste = ("Versions des parcours : une version publiée ne change plus (les tickets déjà émis la gardent). "
                  "On modifie un brouillon, puis on le publie.")
    list_display = ("parcours", "numero", "etat", "date_publication", "publiee_par")

    @admin.display(description="Statut", ordering="statut")
    def etat(self, obj):
        genre = {"PUBLIEE": "ok", "BROUILLON": "attention"}.get(obj.statut, "neutre")
        return pastille(obj.get_statut_display(), genre)

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

    aide_liste = ("Nombre de places par jour et par période (matin, après-midi), pour les parcours soumis à quota "
                  "(dentiste). Chaque changement est enregistré dans « Modifications de quotas ».")
    list_display = ("parcours", "jour_semaine", "periode", "nombre_places", "validite")

    @admin.display(description="Valable", ordering="date_debut")
    def validite(self, obj):
        debut = obj.date_debut.strftime("%d/%m/%Y")
        return f"du {debut} au {obj.date_fin.strftime('%d/%m/%Y')}" if obj.date_fin else f"depuis le {debut}"

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

    aide_liste = "Historique des changements de quotas : qui, quand, valeurs avant et après."
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
