from django.urls import path

from . import views

app_name = "comptes"

urlpatterns = [
    path("", views.comptes, name="comptes"),
    path("<int:utilisateur_id>/", views.profil, name="profil"),
    path("nouveau/", views.compte_nouveau, name="nouveau"),
    path("plusieurs/", views.comptes_liste, name="plusieurs"),
    path("<int:utilisateur_id>/modifier/", views.compte_modifier, name="modifier"),
    path("<int:utilisateur_id>/<str:action>/", views.compte_action, name="action"),
]
