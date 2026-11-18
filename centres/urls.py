
from django.urls import path

from . import views

app_name = "centres"

urlpatterns = [
    path(
        "selection/",
        views.selection_centre,
        name="selection",
    ),
]
