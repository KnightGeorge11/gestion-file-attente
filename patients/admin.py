
from django.contrib import admin

from core.admin import CentreAdminMixin

from .models import (
    Entreprise,
    FusionPatient,
    Patient,
    PatientCentre,
    SignalementDoublon,
)


class PatientCentreInline(admin.TabularInline):
    model = PatientCentre
    extra = 0
    fields = (
        "centre",
        "matricule",
        "archive",
        "date_creation",
    )
    readonly_fields = (
        "matricule",
        "date_creation",
    )


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = (
        "nom",
        "prenom",
        "date_naissance",
        "telephone",
        "entreprise",
    )

    search_fields = (
        "nom",
        "prenom",
        "telephone",
        "matricule_entreprise",
    )

    list_filter = (
        "sexe",
        "entreprise",
    )

    list_select_related = ("entreprise",)

    inlines = (PatientCentreInline,)


@admin.register(PatientCentre)
class PatientCentreAdmin(CentreAdminMixin, admin.ModelAdmin):
    list_display = (
        "matricule",
        "patient",
        "centre",
        "archive",
        "date_creation",
    )

    list_filter = (
        "centre",
        "archive",
    )

    search_fields = (
        "matricule",
        "patient__nom",
        "patient__prenom",
    )

    readonly_fields = (
        "matricule",
        "date_creation",
        "date_modification",
    )

    list_select_related = (
        "patient",
        "centre",
    )


@admin.register(Entreprise)
class EntrepriseAdmin(admin.ModelAdmin):
    list_display = (
        "nom",
        "telephone",
        "actif",
    )

    list_filter = (
        "actif",
    )

    search_fields = (
        "nom",
        "telephone",
    )


@admin.register(SignalementDoublon)
class SignalementDoublonAdmin(admin.ModelAdmin):
    list_display = (
        "patient_1",
        "patient_2",
        "statut",
        "score_similarite",
        "date_creation",
    )

    list_filter = (
        "statut",
        "date_creation",
    )

    search_fields = (
        "patient_1__nom",
        "patient_1__prenom",
        "patient_2__nom",
        "patient_2__prenom",
    )

    readonly_fields = (
        "date_creation",
    )


@admin.register(FusionPatient)
class FusionPatientAdmin(admin.ModelAdmin):
    list_display = (
        "patient_source",
        "patient_cible",
        "statut",
        "effectuee_par",
        "date_fusion",
    )

    list_filter = (
        "statut",
        "date_fusion",
    )

    search_fields = (
        "patient_source__nom",
        "patient_source__prenom",
        "patient_cible__nom",
        "patient_cible__prenom",
    )

    readonly_fields = (
        "patient_source",
        "patient_cible",
        "effectuee_par",
        "annulee_par",
        "motif",
        "donnees_avant_fusion",
        "statut",
        "date_fusion",
        "date_annulation",
    )
