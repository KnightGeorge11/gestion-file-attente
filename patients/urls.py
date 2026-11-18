from django.urls import path

from . import views

app_name = "patients"

urlpatterns = [
    path(
        "recherche/",
        views.recherche_patients,
        name="recherche",
    ),
    path(
        "nouveau/",
        views.creer_patient,
        name="creer",
    ),
    path(
        "doublons/",
        views.doublons,
        name="doublons",
    ),
]