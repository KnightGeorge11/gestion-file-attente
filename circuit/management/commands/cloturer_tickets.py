"""
Clôture automatique des tickets restés ouverts des jours précédents.

À planifier chaque nuit (Planificateur de tâches Windows ou cron), par exemple
à 00 h 30 : python manage.py cloturer_tickets

Règle provisoire (à confirmer avec le centre) : les tickets d'un jour
antérieur encore ouverts sont clôturés ; les passages en cours sont annulés
et les horodatages déjà enregistrés sont conservés.
"""

from django.core.management.base import BaseCommand

from centres.models import Centre
from circuit.services import cloturer_tickets


class Command(BaseCommand):
    help = "Clôture les tickets restés ouverts des jours précédents."

    def handle(self, *args, **options):
        total = 0
        for centre in Centre.objects.filter(actif=True):
            nombre = cloturer_tickets(centre)
            total += nombre
            if nombre:
                self.stdout.write(f"  {centre} : {nombre} ticket(s) clôturé(s)")
        self.stdout.write(self.style.SUCCESS(f"{total} ticket(s) clôturé(s)."))
