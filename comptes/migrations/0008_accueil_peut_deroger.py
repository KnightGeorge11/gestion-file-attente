"""
L'accueil peut déroger au quota dentiste en validant le ticket, avec un motif
obligatoire (décision du 20/11/2026). La commande initialiser_roles applique
la même liste ; cette migration met à jour les bases existantes.
"""

from django.db import migrations


def ajouter(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permission = Permission.objects.filter(content_type__app_label="parcours", codename="deroger_quota").first()
    if permission is None:
        return
    for groupe in Group.objects.filter(name="Accueil"):
        groupe.permissions.add(permission)


def retirer(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permission = Permission.objects.filter(content_type__app_label="parcours", codename="deroger_quota").first()
    if permission is None:
        return
    for groupe in Group.objects.filter(name="Accueil"):
        groupe.permissions.remove(permission)


class Migration(migrations.Migration):

    dependencies = [
        ("comptes", "0007_roles_sans_absence_ni_reintegration"),
        ("parcours", "__first__"),
    ]

    operations = [migrations.RunPython(ajouter, retirer)]
