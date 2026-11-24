
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme

from centres.access import est_administrateur_central
from core.admin import CentreAdminMixin

from .models import (
    AppareilEcran, Borne, Centre, EcranAffichage, EcranConnecte, FileAttente, HoraireService, ImprimanteTicket, Service,
)


@admin.register(Centre)
class CentreAdmin(admin.ModelAdmin):
    # Réservé à l'administrateur de la plateforme (audit du 20/11/2026).
    def has_module_permission(self, request):
        return est_administrateur_central(request.user)

    def has_view_permission(self, request, obj=None):
        return est_administrateur_central(request.user)

    def has_add_permission(self, request):
        return est_administrateur_central(request.user)

    def has_change_permission(self, request, obj=None):
        return est_administrateur_central(request.user)

    def has_delete_permission(self, request, obj=None):
        return est_administrateur_central(request.user)

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

    # Médecin en chef : plus un service (décision du 23/11/2026), mais une
    # personne qui reçoit les demandes « Autre » comme la direction. La case
    # « Peut envoyer au médecin en chef » (les médecins) reste utilisée.
    exclude = ("est_medecin_chef",)

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
        "etat_et_bascule",
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

    # Services proposés par la borne (décision du 21/11/2026).
    filter_horizontal = ("parcours",)

    # Plus de clé (décision du 21/11/2026) : la borne est activée sur son
    # appareil par un administrateur connecté (page …/circuit/borne/).
    actions = ("activer", "desactiver")

    # -- Activer / désactiver en un clic -----------------------------------

    def get_urls(self):
        return [
            path(
                "<int:pk>/basculer/",
                self.admin_site.admin_view(self.basculer),
                name="centres_borne_basculer",
            ),
        ] + super().get_urls()

    @admin.display(description="État", ordering="actif")
    def etat_et_bascule(self, obj):
        """
        État de la borne et bouton pour l'inverser. Le bouton envoie le
        formulaire de la liste (jeton CSRF compris) vers l'adresse « basculer ».
        """
        url = reverse("admin:centres_borne_basculer", args=[obj.pk])
        if obj.actif:
            return format_html(
                '<span class="etat-poste actif">Active</span> '
                '<button type="submit" class="bascule-poste" formaction="{}" formmethod="post" '
                'name="basculer" value="{}" title="Désactiver la borne">Désactiver</button>',
                url, obj.pk,
            )
        return format_html(
            '<span class="etat-poste inactif">Désactivée</span> '
            '<button type="submit" class="bascule-poste activer" formaction="{}" formmethod="post" '
            'name="basculer" value="{}" title="Activer la borne">Activer</button>',
            url, obj.pk,
        )

    def basculer(self, request, pk):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        borne = self.get_queryset(request).filter(pk=pk).first()   # isolation par centre
        if borne is None:
            raise Http404
        if not self.has_change_permission(request, borne):
            raise PermissionDenied
        borne.actif = not borne.actif
        borne.save(update_fields=["actif"])
        etat = "activée" if borne.actif else "désactivée"
        self.log_change(request, borne, f"Borne {etat} (bouton de la liste).")
        self.message_user(request, f"Borne « {borne.libelle} » {etat}.", messages.SUCCESS)
        retour = request.META.get("HTTP_REFERER", "")
        if not url_has_allowed_host_and_scheme(retour, allowed_hosts={request.get_host()}):
            retour = reverse("admin:centres_borne_changelist")
        return redirect(retour)

    # save() borne par borne (et non update()) : chaque changement passe par le
    # journal d'audit (relecture du 23/11/2026).
    @admin.action(description="Activer les bornes sélectionnées", permissions=["change"])
    def activer(self, request, queryset):
        nombre = 0
        for borne in queryset.filter(actif=False):
            borne.actif = True
            borne.save(update_fields=["actif"])
            nombre += 1
        self.message_user(request, f"{nombre} borne(s) activée(s).", messages.SUCCESS)

    @admin.action(description="Désactiver les bornes sélectionnées", permissions=["change"])
    def desactiver(self, request, queryset):
        nombre = 0
        for borne in queryset.filter(actif=True):
            borne.actif = False
            borne.save(update_fields=["actif"])
            nombre += 1
        self.message_user(request, f"{nombre} borne(s) désactivée(s).", messages.SUCCESS)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            messages.info(
                request,
                f"Borne {obj.code} créée. Pour l'activer, connectez-vous une fois avec un compte "
                "administrateur sur l'appareil de la borne, page …/circuit/borne/, puis choisissez-la.",
            )


def _sauver_ecran_avec_cle(self, request, obj, form, change):
    """Écran de TV à clé (ancien mode) : la clé est affichée une seule fois."""
    cle = obj.generer_cle() if not change else None
    admin.ModelAdmin.save_model(self, request, obj, form, change)
    if cle:
        messages.warning(
            request,
            f"Clé de l'écran {obj.code} : {cle} — notez-la maintenant, elle ne sera plus affichée.",
        )


def _regenerer_cle_ecrans(self, request, queryset):
    for ecran in queryset:
        cle = ecran.generer_cle()
        ecran.save(update_fields=["cle_hash"])
        messages.warning(
            request,
            f"Nouvelle clé de l'écran {ecran.code} : {cle} — notez-la maintenant, elle ne sera plus affichée.",
        )


@admin.register(EcranAffichage)
class EcranAffichageAdmin(CentreAdminMixin, admin.ModelAdmin):
    list_display = ("code", "libelle", "centre", "actif", "derniere_activite")
    list_filter = ("centre", "actif")
    readonly_fields = ("derniere_activite", "date_creation")
    # Services affichés sur l'écran (décision du 24/11/2026).
    filter_horizontal = ("services",)
    actions = ("regenerer_cle",)

    save_model = _sauver_ecran_avec_cle

    @admin.action(description="Régénérer la clé des écrans sélectionnés", permissions=["change"])
    def regenerer_cle(self, request, queryset):
        _regenerer_cle_ecrans(self, request, queryset)


@admin.register(AppareilEcran)
class AppareilEcranAdmin(admin.ModelAdmin):
    """Écrans Wi-Fi (TV qui ont ouvert …/tv/) : on peut les nommer ou les retirer ; réservé à la plateforme."""

    list_display = ("__str__", "code", "adresse_ip", "centre", "en_ligne", "derniere_activite")
    list_filter = ("centre",)
    fields = ("nom", "code", "centre", "affichage", "adresse_ip", "navigateur", "date_creation", "derniere_activite")
    readonly_fields = ("code", "adresse_ip", "navigateur", "date_creation", "derniere_activite")

    @admin.display(boolean=True, description="En ligne")
    def en_ligne(self, obj):
        return obj.en_ligne

    def has_add_permission(self, request):
        return False

    def has_module_permission(self, request):
        return est_administrateur_central(request.user)

    has_view_permission = has_change_permission = has_delete_permission = (
        lambda self, request, obj=None: est_administrateur_central(request.user)
    )


@admin.register(EcranConnecte)
class EcranConnecteAdmin(CentreAdminMixin, admin.ModelAdmin):
    """Appareils qui affichent les appels, repérés automatiquement (on peut seulement les nommer)."""

    list_display = ("__str__", "adresse_ip", "centre", "en_ligne", "derniere_activite")
    list_filter = ("centre",)
    fields = ("centre", "nom", "adresse_ip", "navigateur", "premiere_activite", "derniere_activite")
    readonly_fields = ("centre", "adresse_ip", "navigateur", "premiere_activite", "derniere_activite")

    @admin.display(boolean=True, description="En ligne")
    def en_ligne(self, obj):
        return obj.en_ligne

    def has_add_permission(self, request):
        return False


@admin.register(ImprimanteTicket)
class ImprimanteTicketAdmin(CentreAdminMixin, admin.ModelAdmin):
    list_display = ("libelle", "centre", "adresse_ip", "port", "largeur_caracteres", "page_codes", "actif")
    list_filter = ("centre", "actif")
    readonly_fields = ("date_creation",)
    actions = ("imprimer_test",)

    @admin.action(description="Imprimer un ticket de test", permissions=["change"])
    def imprimer_test(self, request, queryset):
        from circuit.impression import ErreurImpression, imprimer_test

        for imprimante in queryset:
            try:
                imprimer_test(imprimante)
            except ErreurImpression as erreur:
                messages.error(request, str(erreur))
            else:
                messages.success(request, f"Ticket de test envoyé à {imprimante}.")

