"""
Direction, Responsable et Administrateur de centre n'appellent aucun patient
(tout passe par l'accueil) : on leur retire « marquer absent » et
« réintégrer » dans les bases existantes (audit du 20/11/2026, I20).
La commande initialiser_roles applique la même liste.
"""

from django.db import migrations

ROLES = ("Direction", "Responsable", "Administrateur de centre")
CODES = ("marquer_absent", "reintegrer")


def retirer(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Permission = apps.get_model("auth", "Permission")
    permissions = list(Permission.objects.filter(content_type__app_label="circuit", codename__in=CODES))
    if not permissions:
        return
    for groupe in Group.objects.filter(name__in=ROLES):
        groupe.permissions.remove(*permissions)


class Migration(migrations.Migration):

    dependencies = [
        ("comptes", "0006_deux_types_administrateur"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [migrations.RunPython(retirer, migrations.RunPython.noop)]
