
from django.contrib.postgres.operations import TrigramExtension
from django.contrib.postgres.indexes import GinIndex
from django.db import migrations, models
import unicodedata


def normaliser_texte(texte):
    texte = unicodedata.normalize("NFKD", texte or "")
    texte = "".join(
        caractere
        for caractere in texte
        if not unicodedata.combining(caractere)
    )
    return " ".join(texte.casefold().split())


def remplir_noms_normalises(apps, schema_editor):
    Patient = apps.get_model("patients", "Patient")
    base = schema_editor.connection.alias

    for patient in Patient.objects.using(base).all().iterator():
        patient.nom_recherche = normaliser_texte(
            f"{patient.nom} {patient.prenom}"
        )
        patient.save(update_fields=["nom_recherche"])


class Migration(migrations.Migration):

    dependencies = [
        ("patients", "0002_signalementdoublon_doublon_patients_distincts"),
    ]

    operations = [
        TrigramExtension(),

        migrations.AddField(
            model_name="patient",
            name="nom_recherche",
            field=models.CharField(
                max_length=310,
                blank=True,
                default="",
                editable=False,
            ),
        ),

        migrations.RunPython(
            remplir_noms_normalises,
            migrations.RunPython.noop,
        ),

        migrations.AddIndex(
            model_name="patient",
            index=GinIndex(
                fields=["nom_recherche"],
                opclasses=["gin_trgm_ops"],
                name="patient_nom_recherche_trgm",
            ),
        ),
    ]
