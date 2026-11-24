from django.urls import path

from . import views

app_name = "comptes"

urlpatterns = [
    path("", views.comptes, name="comptes"),
    path("<int:utilisateur_id>/", views.profil, name="profil"),
]
