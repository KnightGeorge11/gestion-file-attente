from django.urls import path

from . import views, views_postes

app_name = "circuit"

urlpatterns = [
    # Interfaces des services
    path("services/", views.guichets, name="guichets"),
    path("ma-salle/", views.ma_salle, name="ma_salle"),
    path("services/<int:service_id>/", views.guichet, name="guichet"),
    path("services/<int:service_id>/liste/", views.guichet_liste, name="guichet_liste"),
    path("services/<int:service_id>/indicateurs/", views.guichet_indicateurs, name="guichet_indicateurs"),
    path("services/<int:service_id>/patient/", views.guichet_zone, name="guichet_zone"),
    path("services/<int:service_id>/action/<slug:action>/", views.guichet_action, name="guichet_action"),
    # Tickets
    path("tickets/", views.tickets_du_jour, name="tickets"),
    path("tickets/<int:ticket_id>/", views.ticket_detail, name="ticket"),
    path("tickets/<int:ticket_id>/action/<slug:action>/", views.ticket_action, name="ticket_action"),
    # Mode Autre
    path("demandes/", views.demandes, name="demandes"),
    path("notifications/", views.notifications, name="notifications"),
    path("notifications/badge/", views.badge_notifications, name="badge_notifications"),
    path("notifications/<int:notification_id>/appeler/", views.notification_appeler, name="notification_appeler"),
    # Borne et écran principal (sans compte)
    path("borne/", views_postes.borne, name="borne"),
    path("borne/emettre/", views_postes.borne_emettre, name="borne_emettre"),
    path("borne/ticket/<int:ticket_id>/", views_postes.borne_ticket, name="borne_ticket"),
    path("borne/ticket/<int:ticket_id>/reimprimer/", views_postes.borne_reimprimer, name="borne_reimprimer"),
    path("borne/ticket/<int:ticket_id>/escpos/", views_postes.borne_ticket_escpos, name="borne_ticket_escpos"),
    path("borne/ticket/<int:ticket_id>/impression-android/", views_postes.borne_impression_android, name="borne_impression_android"),
    path("ecran/", views_postes.ecran, name="ecran"),
    path("ecran/appels/", views_postes.ecran_appels, name="ecran_appels"),
    path("ecran/connexion/", views.ecran_connexion, name="ecran_connexion"),
    path("bornes/", views.bornes, name="bornes"),
    path("bornes/<int:borne_id>/basculer/", views.borne_basculer, name="borne_basculer"),
    path("bornes/<int:borne_id>/services/", views.borne_services, name="borne_services"),
    path("ecran/annonce/<int:appel_id>/", views_postes.ecran_annonce, name="ecran_annonce"),
    path("ecran/reseau/", views.ecrans_reseau, name="ecrans_reseau"),
    path("ecran/wifi/choisir/", views.ecran_wifi_choisir, name="ecran_wifi_choisir"),
    path("ecran/salle/<str:code_centre>/", views_postes.ecran_salle, name="ecran_salle"),
    path("ecran/salle/<str:code_centre>/appels/", views_postes.ecran_salle_appels, name="ecran_salle_appels"),
    path("poste/deconnexion/", views_postes.poste_deconnexion, name="poste_deconnexion"),
]
