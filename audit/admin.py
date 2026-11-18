from django.contrib import admin

from .models import JournalAudit


@admin.register(JournalAudit)
class JournalAuditAdmin(admin.ModelAdmin):
    list_display = ("date", "action", "utilisateur", "identifiant_saisi", "centre", "objet_libelle", "adresse_ip")
    list_filter = ("action", "centre")
    search_fields = ("objet_libelle", "identifiant_saisi", "utilisateur__username")
    list_select_related = ("utilisateur", "centre")
    readonly_fields = [f.name for f in JournalAudit._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
