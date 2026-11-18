
from django.contrib import admin, messages

from core.admin import CentreAdminMixin

from .models import Borne, Centre, EcranAffichage, FileAttente, HoraireService, ImprimanteTicket, Service


@admin.register(Centre)
class CentreAdmin(admin.ModelAdmin):
    list_display = (
        "code",
        "nom",
        "prochain_numero_patient",
        "fuseau_horaire",
        "actif",
    )

    list_filter = (
        "actif",
        "fuseau_horaire",
    )

    search_fields = (
        "code",
        "nom",
        "adresse",
    )

    readonly_fields = (
        "prochain_numero_patient",
        "date_creation",
        "date_modification",
    )

    fieldsets = (
        (None, {
            "fields": ("code", "nom", "adresse", "fuseau_horaire", "actif"),
        }),
        ("Paramètres de fonctionnement", {
            "fields": (
                "heure_coupure_matin",
                "heure_cloture_tickets",
                "max_reintegrations",
                "max_tickets_ouverts_par_patient",
                "duree_affichage_appel_secondes",
            ),
        }),
        ("Informations", {
            "fields": (
                "prochain_numero_patient",
                "date_creation",
                "date_modification",
            ),
        }),
    )


class HoraireServiceInline(admin.TabularInline):
    model = HoraireService
    extra = 0


class FileAttenteInline(admin.TabularInline):
    model = FileAttente
    extra = 0
    fields = (
        "code",
        "nom",
        "par_defaut",
        "nb_appels_simultanes",
        "ordre_strict",
        "actif",
    )
    readonly_fields = ("par_defaut",)
    show_change_link = True

    def has_add_permission(self, request, obj=None):
        # Les files supplémentaires se créent depuis leur propre page.
        return False


@admin.register(Service)
class ServiceAdmin(CentreAdminMixin, admin.ModelAdmin):
    list_display = (
        "nom",
        "code",
        "centre",
        "ordre",
        "actif",
        "suspendu",
        "afficher_ecran_principal",
    )

    list_filter = (
        "centre",
        "actif",
        "suspendu",
        "est_accueil",
        "est_pharmacie",
    )

    search_fields = (
        "nom",
        "code",
        "centre__nom",
    )

    list_select_related = ("centre",)

    inlines = (HoraireServiceInline, FileAttenteInline)


@admin.register(FileAttente)
class FileAttenteAdmin(CentreAdminMixin, admin.ModelAdmin):
    list_display = (
        "nom",
        "service",
        "centre",
        "par_defaut",
        "nb_appels_avant_absence",
        "delai_entre_appels_secondes",
        "nb_appels_simultanes",
        "ordre_strict",
        "actif",
    )

    list_filter = (
        "centre",
        "par_defaut",
        "ordre_strict",
        "actif",
    )

    search_fields = (
        "nom",
        "code",
        "service__nom",
    )

    list_select_related = ("service", "centre")

    readonly_fields = ("date_creation", "date_modification")


@admin.register(Borne)
class BorneAdmin(CentreAdminMixin, admin.ModelAdmin):
    list_display = (
        "code",
        "libelle",
        "centre",
        "imprimante",
        "actif",
        "derniere_activite",
    )

    list_filter = (
        "centre",
        "actif",
    )

    search_fields = (
        "code",
        "libelle",
    )

    readonly_fields = (
        "derniere_activite",
        "date_creation",
    )

    actions = ("regenerer_cle",)

    def save_model(self, request, obj, form, change):
        cle = None

        if not change:
            cle = obj.generer_cle()

        super().save_model(request, obj, form, change)

        if cle:
            messages.warning(
                request,
                f"Clé de la borne {obj.code} : {cle} — "
                "notez-la maintenant, elle ne sera plus affichée.",
            )

    @admin.action(description="Régénérer la clé des bornes sélectionnées")
    def regenerer_cle(self, request, queryset):
        for borne in queryset:
            cle = borne.generer_cle()
            borne.save(update_fields=["cle_hash"])

            messages.warning(
                request,
                f"Nouvelle clé de la borne {borne.code} : {cle} — "
                "notez-la maintenant, elle ne sera plus affichée.",
            )


@admin.register(EcranAffichage)
class EcranAffichageAdmin(CentreAdminMixin, admin.ModelAdmin):
    list_display = ("code", "libelle", "centre", "actif", "derniere_activite")
    list_filter = ("centre", "actif")
    readonly_fields = ("derniere_activite", "date_creation")
    actions = ("regenerer_cle",)

    save_model = BorneAdmin.save_model

    @admin.action(description="Régénérer la clé des écrans sélectionnés")
    def regenerer_cle(self, request, queryset):
        BorneAdmin.regenerer_cle(self, request, queryset)


@admin.register(ImprimanteTicket)
class ImprimanteTicketAdmin(CentreAdminMixin, admin.ModelAdmin):
    list_display = ("libelle", "centre", "adresse_ip", "port", "largeur_caracteres", "page_codes", "actif")
    list_filter = ("centre", "actif")
    readonly_fields = ("date_creation",)
    actions = ("imprimer_test",)

    @admin.action(description="Imprimer un ticket de test")
    def imprimer_test(self, request, queryset):
        from circuit.impression import ErreurImpression, imprimer_test

        for imprimante in queryset:
            try:
                imprimer_test(imprimante)
            except ErreurImpression as erreur:
                messages.error(request, str(erreur))
            else:
                messages.success(request, f"Ticket de test envoyé à {imprimante}.")

