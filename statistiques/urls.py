from django.urls import path

from . import views

app_name = "statistiques"

urlpatterns = [
    path("", views.tableau, name="tableau"),
    path("export.xlsx", views.export_excel, name="export_excel"),
    path("export.pdf", views.export_pdf, name="export_pdf"),
]
