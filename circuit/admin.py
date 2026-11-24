from django.contrib import admin

from core.admin import CentreAdminMixin, etat_actif

from .models import (
    Appel,
    DemandeAutre,
    Evenement,
    MotifDemande,
    Notification,
    Passage,
    ReservationQuota,
    Ticket,
    Transfert,
)


@admin.register(MotifDemande)
class MotifDemandeAdmin(CentreAdminMixin, admin.ModelAdmin):
    aide_liste = ("Motifs proposés à la borne pour le mode « Autre » (rendez-vous, cotisation, rencontrer la direction…). "
                  "L'accueil oriente ensuite chaque demande vers une personne ou un service.")
    list_display = ("libelle", "centre", "ordre", "etat")
    list_filter = ("centre", "actif")
    search_fields = ("libelle",)

    @admin.display(description="État", ordering="actif")
    def etat(self, obj):
        return etat_actif(obj.actif, "Proposé", "Retiré")


class LectureSeuleAdmin(CentreAdminMixin, admin.ModelAdmin):
    """Données opérationnelles : consultation seulement (modifiées par le moteur)."""

    aide_liste = "Consultation seulement : ces données sont enregistrées automatiquement par la plateforme."

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PassageInline(admin.TabularInline):
    model = Passage
    extra = 0
    can_delete = False
    fields = ("nom_service", "origine", "statut", "date_arrivee", "date_debut", "date_fin", "pris_en_charge_par")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Ticket)
class TicketAdmin(LectureSeuleAdmin):
    list_display = ("numero", "jour", "centre", "mode", "parcours", "statut", "patient")
    list_filter = ("centre", "statut", "mode", "jour")
    search_fields = ("numero", "patient__nom", "patient__prenom")
    list_select_related = ("centre", "parcours", "patient")
    inlines = (PassageInline,)


@admin.register(Appel)
class AppelAdmin(LectureSeuleAdmin):
    list_display = ("date", "numero_ticket", "nom_service", "numero_appel", "mode", "agent", "centre")
    list_filter = ("centre", "mode", "service")


@admin.register(Transfert)
class TransfertAdmin(LectureSeuleAdmin):
    list_display = ("date", "ticket", "passage_origine", "service_destination", "decide_par")
    list_filter = ("centre",)


@admin.register(ReservationQuota)
class ReservationQuotaAdmin(LectureSeuleAdmin):
    list_display = ("ticket", "jour", "periode", "statut", "derogation", "reservee_par", "liberee_par")
    list_filter = ("centre", "statut", "derogation", "periode")


@admin.register(DemandeAutre)
class DemandeAutreAdmin(LectureSeuleAdmin):
    list_display = ("ticket", "motif", "statut", "date_creation")
    list_filter = ("centre", "statut")


@admin.register(Notification)
class NotificationAdmin(LectureSeuleAdmin):
    list_display = ("demande", "destinataire", "service", "transmise_par", "date", "date_lecture")
    list_filter = ("centre",)


@admin.register(Evenement)
class EvenementAdmin(LectureSeuleAdmin):
    list_display = ("date", "ticket", "type", "agent", "centre")
    list_filter = ("centre", "type")
    search_fields = ("ticket__numero",)
