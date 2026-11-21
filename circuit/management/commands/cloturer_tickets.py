"""
Clôture automatique des tickets restés ouverts.

Décision du 21/11/2026 : clôture à 23 h 59 (heure réglable par centre) ; les
numéros repartent de 001 pour le nouveau jour.
Après l'heure de clôture du centre, les tickets du jour encore ouverts sont
clôturés, ainsi que ceux des jours précédents. Les passages en cours sont
annulés ; les horodatages déjà enregistrés sont conservés.

À planifier sur le serveur Ubuntu (crontab de l'utilisateur de l'application),
à 23 h 59, avec une seconde exécution de sécurité la nuit :
    59 23 * * *  cd /chemin/gestion_file_attente && .venv/bin/python manage.py cloturer_tickets
    30 2 * * *   cd /chemin/gestion_file_attente && .venv/bin/python manage.py cloturer_tickets
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
