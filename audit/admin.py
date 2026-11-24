from django.contrib import admin

from centres.access import est_administrateur_central
from core.admin import ListeLisibleMixin

from .models import JournalAudit


@admin.register(JournalAudit)
class JournalAuditAdmin(ListeLisibleMixin, admin.ModelAdmin):
    aide_liste = ("Journal de sécurité de la plateforme : connexions, changements de droits, de quotas, exports… "
                  "Il ne peut être ni modifié ni effacé.")

    list_display = ("date", "action", "utilisateur", "identifiant_saisi", "centre", "objet_libelle", "adresse_ip")
    list_filter = ("action", "centre")
    search_fields = ("objet_libelle", "identifiant_saisi", "utilisateur__username")
    list_select_related = ("utilisateur", "centre")
    readonly_fields = [f.name for f in JournalAudit._meta.fields]

    # Journal de toute la plateforme : réservé à son administrateur.
    def has_module_permission(self, request):
        return est_administrateur_central(request.user)

    def has_view_permission(self, request, obj=None):
        return est_administrateur_central(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
