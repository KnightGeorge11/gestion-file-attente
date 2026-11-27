
from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.http import url_has_allowed_host_and_scheme

from centres.access import est_administrateur_central
from core.admin import CentreAdminMixin, ListeLisibleMixin, etat_actif, oui_non, pastille

from .models import (
    AppareilEcran, Borne, Centre, EcranAffichage, EcranConnecte, FileAttente, HoraireService, ImprimanteTicket, Service,
)


@admin.register(Centre)
class CentreAdmin(ListeLisibleMixin, admin.ModelAdmin):
    aide_liste = "Les centres de santé de la plateforme. Un centre hors service n'émet plus de tickets et n'est plus proposé aux agents."

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

    list_display = ("nom", "code", "nb_services", "fuseau_horaire", "etat")
    list_display_links = ("nom",)

    def get_queryset(self, request):
        from django.db.models import Count, Q

        return super().get_queryset(request).annotate(
            nombre_services=Count("services", filter=Q(services__actif=True))
        )

    @admin.display(description="Services actifs", ordering="nombre_services")
    def nb_services(self, obj):
        return obj.nombre_services

    @admin.display(description="État", ordering="actif")
    def etat(self, obj):
        return etat_actif(obj.actif)

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
            "fields": ("nom", "code", "adresse", "actif"),
        }),
        ("Fonctionnement", {
            "fields": (
                "accueil_delivre_tickets",
                "heure_coupure_matin",
                "heure_cloture_tickets",
            ),
        }),
        ("Avancé", {
            "fields": (
                "fuseau_horaire",
                "max_reintegrations",
                "max_tickets_ouverts_par_patient",
                "duree_affichage_appel_secondes",
                "prochain_numero_patient",
                "date_creation",
                "date_modification",
            ),
            "classes": ("collapse",),
        }),
    )


class HorairesFormSet(forms.BaseInlineFormSet):
    """Refuse deux plages du même jour qui se chevauchent, saisies ensemble (la base ne les voit pas encore)."""

    def clean(self):
        super().clean()
        plages = []
        for formulaire in self.forms:
            donnees = getattr(formulaire, "cleaned_data", None) or {}
            if donnees.get("DELETE") or not donnees.get("heure_debut") or not donnees.get("heure_fin"):
                continue
            plage = (donnees.get("jour_semaine"), donnees["heure_debut"], donnees["heure_fin"])
            for jour, debut, fin in plages:
                if jour == plage[0] and debut < plage[2] and plage[1] < fin:
                    raise forms.ValidationError("Deux plages du même jour se chevauchent.")
            plages.append(plage)


class HoraireServiceInline(admin.TabularInline):
    model = HoraireService
    formset = HorairesFormSet
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
    aide_liste = ("Services du centre (accueil, soins, pharmacie). « Suspendu » : le service ne reçoit plus de "
                  "nouveaux patients pour le moment. L'ordre est celui des tuiles de la page Services.")
    list_display = ("nom", "centre", "type_service", "ordre", "a_l_ecran", "nb_files", "etat")
    list_display_links = ("nom",)

    def get_queryset(self, request):
        from django.db.models import Count, Q

        return super().get_queryset(request).annotate(nombre_files=Count("files", filter=Q(files__actif=True)))

    @admin.display(description="Type")
    def type_service(self, obj):
        if obj.est_accueil:
            return pastille("Accueil", "info")
        if obj.est_pharmacie:
            return pastille("Pharmacie", "info")
        return "Soins"

    @admin.display(description="Appels à l'écran", ordering="afficher_ecran_principal")
    def a_l_ecran(self, obj):
        return oui_non(obj.afficher_ecran_principal)

    @admin.display(description="Files", ordering="nombre_files")
    def nb_files(self, obj):
        return obj.nombre_files

    @admin.display(description="État", ordering="actif")
    def etat(self, obj):
        if not obj.actif:
            return pastille("Désactivé", "arret")
        if obj.suspendu:
            return pastille("Suspendu", "attention")
        return pastille("En service", "ok")

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
    # Formulaire simplifié (27/11/2026) : nom, ordre, en service ou en pause ;
    # le rôle particulier du service (accueil, pharmacie…) est replié.
    fieldsets = (
        (None, {"fields": ("centre", "nom", "code", "ordre", "actif")}),
        ("Pause", {"fields": ("suspendu", "motif_suspension"),
                   "description": "Service en pause : il ne reçoit plus de nouveaux patients pour le moment."}),
        ("Avancé", {"fields": ("destinataire_demandes", "afficher_ecran_principal", "envoie_medecin_chef",
                               "accepte_transferts", "appel_oral_autorise", "est_accueil", "est_pharmacie"),
                    "classes": ("collapse",)}),
    )

    inlines = (HoraireServiceInline, FileAttenteInline)


@admin.register(FileAttente)
class FileAttenteAdmin(CentreAdminMixin, admin.ModelAdmin):
    aide_liste = ("Files d'attente de chaque service. Un patient est marqué absent après le nombre d'appels indiqué, "
                  "espacés du délai entre deux appels. « Ordre » : strict = seul le premier patient peut être appelé.")
    list_display = ("nom", "service", "centre", "principale", "absence_apres", "delai", "agents_en_parallele",
                    "ordre", "etat")
    list_display_links = ("nom",)

    @admin.display(description="File principale", ordering="par_defaut")
    def principale(self, obj):
        return oui_non(obj.par_defaut)

    @admin.display(description="Absent après", ordering="nb_appels_avant_absence")
    def absence_apres(self, obj):
        return f"{obj.nb_appels_avant_absence} appels"

    @admin.display(description="Délai entre appels", ordering="delai_entre_appels_secondes")
    def delai(self, obj):
        secondes = obj.delai_entre_appels_secondes
        return f"{secondes // 60} min {secondes % 60:02d} s" if secondes >= 60 else f"{secondes} s"

    @admin.display(description="Appels en parallèle", ordering="nb_appels_simultanes")
    def agents_en_parallele(self, obj):
        return obj.nb_appels_simultanes

    @admin.display(description="Ordre", ordering="ordre_strict")
    def ordre(self, obj):
        return "Strict" if obj.ordre_strict else "Libre"

    @admin.display(description="État", ordering="actif")
    def etat(self, obj):
        return etat_actif(obj.actif)

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

    list_select_related = ("service__centre", "centre")

    readonly_fields = ("date_creation", "date_modification")

    fieldsets = (
        (None, {"fields": ("service", "nom", "actif")}),
        ("Appels", {"fields": ("nb_appels_avant_absence", "delai_entre_appels_secondes", "nb_appels_simultanes",
                               "ordre_strict")}),
        ("Avancé", {"fields": ("centre", "code", "par_defaut", "date_creation", "date_modification"),
                    "classes": ("collapse",)}),
    )


@admin.register(Borne)
class BorneAdmin(CentreAdminMixin, admin.ModelAdmin):
    aide_liste = ("Bornes où les patients prennent leur ticket. Activez une borne sur sa tablette depuis l'onglet "
                  "« Borne » de l'application ; vous y choisissez aussi les services affichés.")
    list_display = ("libelle", "code", "centre", "services_proposes", "imprimante_ou_navigateur",
                    "etat_et_bascule", "derniere_activite")
    list_display_links = ("libelle",)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("centre", "imprimante").prefetch_related("parcours")

    @admin.display(description="Services proposés")
    def services_proposes(self, obj):
        choisis = [p.libelle_borne for p in obj.parcours.all()]
        return ", ".join(choisis) if choisis else pastille("Tous", "neutre")

    @admin.display(description="Impression", ordering="imprimante__libelle")
    def imprimante_ou_navigateur(self, obj):
        return obj.imprimante.libelle if obj.imprimante_id else pastille("Par la tablette", "neutre")

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

    # Formulaire simplifié (27/11/2026) ; services proposés en cases à cocher.
    fieldsets = (
        (None, {"fields": ("centre", "libelle", "imprimante", "actif")}),
        ("Services proposés", {"fields": ("parcours",),
                               "description": "Aucun coché : tous les services de la borne du centre."}),
        ("Avancé", {"fields": ("code", "derniere_activite", "date_creation"), "classes": ("collapse",)}),
    )

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
    aide_liste = ("Ce que les écrans affichent : services dont les appels s'affichent, nombre d'appels, voix. "
                  "Choisissez l'affichage de chaque écran depuis la page « Écran » de l'application.")
    list_display = ("libelle", "centre", "services_affiches", "nb_appels_affiches", "voix", "ecrans_relies", "etat",
                    "derniere_activite")
    list_display_links = ("libelle",)
    list_filter = ("centre", "actif")
    search_fields = ("libelle", "code")
    readonly_fields = ("derniere_activite", "date_creation")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("services", "appareils")

    @admin.display(description="Services affichés")
    def services_affiches(self, obj):
        noms = [s.nom for s in obj.services.all()]
        return ", ".join(noms) if noms else pastille("Tous", "neutre")

    @admin.display(description="Voix", ordering="annonce_vocale")
    def voix(self, obj):
        return oui_non(obj.annonce_vocale)

    @admin.display(description="TV Wi-Fi reliées")
    def ecrans_relies(self, obj):
        return len(obj.appareils.all())

    @admin.display(description="État", ordering="actif")
    def etat(self, obj):
        return etat_actif(obj.actif, "Utilisé", "Retiré")
    # Formulaire simplifié (27/11/2026) ; services en cases à cocher.
    fieldsets = (
        (None, {"fields": ("centre", "libelle", "services", "nb_appels_affiches", "annonce_vocale", "actif")}),
        ("Avancé", {"fields": ("code", "derniere_activite", "date_creation"), "classes": ("collapse",)}),
    )
    actions = ("regenerer_cle",)

    save_model = _sauver_ecran_avec_cle

    @admin.action(description="Régénérer la clé des écrans sélectionnés", permissions=["change"])
    def regenerer_cle(self, request, queryset):
        _regenerer_cle_ecrans(self, request, queryset)


@admin.register(AppareilEcran)
class AppareilEcranAdmin(ListeLisibleMixin, admin.ModelAdmin):
    aide_liste = ("TV qui ont ouvert une fois l'adresse …/tv/. Choisissez ce qu'elles affichent depuis la page "
                  "« Écran » de l'application.")

    """Écrans Wi-Fi (TV qui ont ouvert …/tv/) : on peut les nommer ou les retirer ; réservé à la plateforme."""

    list_display = ("__str__", "code", "centre", "affichage", "adresse_ip", "etat", "derniere_activite")
    list_select_related = ("centre", "affichage__centre")
    list_filter = ("centre",)
    search_fields = ("nom", "code", "adresse_ip")
    fields = ("nom", "code", "centre", "affichage", "adresse_ip", "navigateur", "date_creation", "derniere_activite")
    readonly_fields = ("code", "adresse_ip", "navigateur", "date_creation", "derniere_activite")

    @admin.display(description="État")
    def etat(self, obj):
        if not obj.centre_id:
            return pastille("Libre", "neutre")
        return pastille("En ligne", "ok") if obj.en_ligne else pastille("Hors ligne", "arret")

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

    aide_liste = "Appareils du réseau qui affichent l'écran des appels (repérés automatiquement). Donnez-leur un nom pour les reconnaître."
    list_display = ("__str__", "adresse_ip", "centre", "etat", "derniere_activite")
    search_fields = ("nom", "adresse_ip")
    list_filter = ("centre",)
    fields = ("centre", "nom", "adresse_ip", "navigateur", "premiere_activite", "derniere_activite")
    readonly_fields = ("centre", "adresse_ip", "navigateur", "premiere_activite", "derniere_activite")

    @admin.display(description="État")
    def etat(self, obj):
        return pastille("En ligne", "ok") if obj.en_ligne else pastille("Hors ligne", "arret")

    def has_add_permission(self, request):
        return False


class ImprimanteTicketForm(forms.ModelForm):
    """Libellés lisibles (sans migration : seulement le formulaire)."""

    class Meta:
        model = ImprimanteTicket
        fields = "__all__"
        labels = {
            "libelle": "Nom de l'imprimante",
            "adresse_ip": "Adresse IP",
            "port": "Port",
            "largeur_caracteres": "Caractères par ligne",
            "page_codes": "Jeu de caractères (accents)",
            "actif": "En service",
        }


@admin.register(ImprimanteTicket)
class ImprimanteTicketAdmin(CentreAdminMixin, admin.ModelAdmin):
    """Imprimantes thermiques réseau des bornes (présentation revue le 24/11/2026)."""

    form = ImprimanteTicketForm
    aide_liste = ("Imprimantes thermiques branchées au réseau du centre. Chaque borne imprime ses tickets sur "
                  "l'imprimante choisie dans sa fiche (Bornes). « Imprimer un test » vérifie qu'une imprimante "
                  "répond ; une borne sans imprimante imprime par la tablette.")
    list_display = ("nom", "centre", "adresse_reseau", "papier", "accents", "bornes_reliees", "etat", "bouton_test")
    list_display_links = ("nom",)
    list_filter = ("centre", "actif")
    search_fields = ("libelle", "adresse_ip")
    readonly_fields = ("date_creation",)
    actions = ("imprimer_test",)
    fieldsets = (
        ("Imprimante", {"fields": ("libelle", "centre", "actif")}),
        ("Réseau", {
            "fields": ("adresse_ip", "port"),
            "description": "Imprimante thermique branchée au réseau du centre (câble ou Wi-Fi), avec une adresse IP fixe.",
        }),
        ("Papier et accents", {
            "fields": ("largeur_caracteres", "page_codes"),
            "description": "48 caractères pour du papier de 80 mm, 32 pour du 58 mm. En cas d'accents mal imprimés : « Sans accents ».",
        }),
        ("Suivi", {"fields": ("date_creation",), "classes": ("collapse",)}),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("centre").prefetch_related("bornes")

    @admin.display(description="Imprimante", ordering="libelle")
    def nom(self, obj):
        return obj.libelle

    @admin.display(description="Adresse réseau", ordering="adresse_ip")
    def adresse_reseau(self, obj):
        return format_html("<code>{}:{}</code>", obj.adresse_ip, obj.port)

    @admin.display(description="Papier", ordering="largeur_caracteres")
    def papier(self, obj):
        largeur = {48: "80 mm", 42: "80 mm", 32: "58 mm"}.get(obj.largeur_caracteres)
        return f"{largeur} ({obj.largeur_caracteres} car.)" if largeur else f"{obj.largeur_caracteres} car."

    @admin.display(description="Accents", ordering="page_codes")
    def accents(self, obj):
        return {"cp858": "Oui (avec €)", "cp850": "Oui", "ascii": "Sans accents"}.get(obj.page_codes, obj.page_codes)

    @admin.display(description="Bornes reliées")
    def bornes_reliees(self, obj):
        bornes = [b.libelle for b in obj.bornes.all()]
        return ", ".join(bornes) if bornes else format_html('<span class="discret-admin">{}</span>', "Aucune")

    @admin.display(description="État", ordering="actif")
    def etat(self, obj):
        return etat_actif(obj.actif)

    @admin.display(description="")
    def bouton_test(self, obj):
        return format_html(
            '<button type="submit" class="button" formmethod="post" formaction="{}">{}</button>',
            reverse("admin:centres_imprimanteticket_tester", args=[obj.pk]), "Imprimer un test",
        )

    def get_urls(self):
        urls = [
            path("<int:imprimante_id>/tester/", self.admin_site.admin_view(self.tester),
                 name="centres_imprimanteticket_tester"),
        ]
        return urls + super().get_urls()

    def tester(self, request, imprimante_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        imprimante = self.get_object(request, str(imprimante_id))
        if imprimante is None or not self.has_change_permission(request, imprimante):
            raise PermissionDenied
        self.imprimer_test(request, ImprimanteTicket.objects.filter(pk=imprimante.pk))
        return redirect("admin:centres_imprimanteticket_changelist")

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

