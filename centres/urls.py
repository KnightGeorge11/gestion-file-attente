from django.urls import path

from . import reglages, views

app_name = "centres"

urlpatterns = [
    path(
        "selection/",
        views.selection_centre,
        name="selection",
    ),
    # Réglages du centre en formulaires courts (décision du 27/11/2026).
    path("reglages/", reglages.reglages, name="reglages"),
    path("reglages/motif/", reglages.motif, name="reglages_motif"),
    path("reglages/imprimante/", reglages.imprimante, name="reglages_imprimante"),
    path("reglages/places/<int:parcours_id>/", reglages.quotas, name="reglages_quotas"),
    path("reglages/centre/", reglages.centre_modifier, name="reglages_centre"),
    path("reglages/nouveau-centre/", reglages.centre_nouveau, name="reglages_nouveau_centre"),
]
