from django.urls import path

from . import views, views_postes

app_name = "circuit"

urlpatterns = [
    # Interfaces des services
    path("services/", views.guichets, name="guichets"),
    path("services/<int:service_id>/", views.guichet, name="guichet"),
    path("services/<int:service_id>/liste/", views.guichet_liste, name="guichet_liste"),
    path("services/<int:service_id>/action/<slug:action>/", views.guichet_action, name="guichet_action"),
    # Tickets
    path("tickets/", views.tickets_du_jour, name="tickets"),
    path("tickets/<int:ticket_id>/", views.ticket_detail, name="ticket"),
    path("tickets/<int:ticket_id>/identifier/", views.identifier, name="identifier"),
    path("tickets/<int:ticket_id>/action/<slug:action>/", views.ticket_action, name="ticket_action"),
    # Mode Autre
    path("demandes/", views.demandes, name="demandes"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/badge/", views.badge_notifications, name="badge_notifications"),
    # Borne et écran principal (sans compte)
    path("borne/", views_postes.borne, name="borne"),
    path("borne/emettre/", views_postes.borne_emettre, name="borne_emettre"),
    path("borne/ticket/<int:ticket_id>/", views_postes.borne_ticket, name="borne_ticket"),
    path("ecran/", views_postes.ecran, name="ecran"),
    path("ecran/appels/", views_postes.ecran_appels, name="ecran_appels"),
    path("poste/deconnexion/", views_postes.poste_deconnexion, name="poste_deconnexion"),
]
