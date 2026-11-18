from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from comptes.forms import ConnexionForm
from core import views as core_views

admin.site.site_header = "SMIE FUNTOA · Administration des files d'attente"
admin.site.site_title = "Administration SMIE FUNTOA"

urlpatterns = [
    path("", core_views.accueil, name="accueil"),
    path("connexion/", auth_views.LoginView.as_view(authentication_form=ConnexionForm), name="connexion"),
    path("deconnexion/", auth_views.LogoutView.as_view(), name="deconnexion"),
    path("admin/", admin.site.urls),
    path("centres/", include("centres.urls")),
    path("circuit/", include("circuit.urls")),
    path("statistiques/", include("statistiques.urls")),
]
