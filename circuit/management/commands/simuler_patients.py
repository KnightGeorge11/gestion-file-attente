"""
Simule l'arrivée et le parcours de tickets (patients anonymes) dans un centre.
Aucune information patient n'est créée (décision du 18/11/2026).

Prérequis : python manage.py creer_comptes_test

Exemples :
    # 15 tickets, 10 étapes immédiates (files remplies à divers stades)
    python manage.py simuler_patients 001

    # Démonstration en direct (écran principal) : nouveaux tickets et avancée
    # toutes les 15 secondes pendant 30 minutes
    python manage.py simuler_patients 001 --continu --intervalle 15 --duree 30

À utiliser sur une base de développement ou de test : les tickets simulés
restent dans l'historique et dans les statistiques (rien n'est supprimé).
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from centres.models import Centre
from circuit.services import ErreurCircuit
from circuit.simulation import Simulateur


class Command(BaseCommand):
    help = "Simule des patients qui prennent un ticket et avancent dans leur parcours."

    def add_arguments(self, parser):
        parser.add_argument("code_centre")
        parser.add_argument("--tickets", type=int, default=15, help="Tickets émis au départ (défaut 15).")
        parser.add_argument("--etapes", type=int, default=10, help="Étapes immédiates (défaut 10).")
        parser.add_argument("--continu", action="store_true", help="Simulation en direct.")
        parser.add_argument("--intervalle", type=float, default=15, help="Secondes entre deux étapes (continu).")
        parser.add_argument("--duree", type=float, default=30, help="Durée en minutes (continu).")
        parser.add_argument("--graine", type=int, help="Graine du hasard (résultats reproductibles).")
        parser.add_argument("--force", action="store_true", help="Autoriser malgré DEBUG désactivé.")

    def handle(self, *args, **options):
        if not settings.DEBUG and not options["force"]:
            raise CommandError(
                "DEBUG est désactivé (production) : la simulation ajouterait de faux tickets à l'historique. "
                "Utilisez --force uniquement sur une base de test."
            )
        centre = Centre.objects.filter(code__iexact=options["code_centre"], actif=True).first()
        if centre is None:
            raise CommandError(f"Centre « {options['code_centre']} » introuvable.")

        verbeux = options["verbosity"] >= 1
        try:
            simulateur = Simulateur(
                centre, graine=options["graine"], journal=self.stdout.write if verbeux else None
            )
        except ErreurCircuit as erreur:
            raise CommandError(" ".join(erreur.messages))

        simulateur.preparer()
        simulateur.emettre(options["tickets"])
        for _ in range(options["etapes"]):
            simulateur.etape()

        if options["continu"]:
            fin = time.monotonic() + options["duree"] * 60
            self.stdout.write(f"Simulation en direct ({options['duree']} min). Ctrl+C pour arrêter.")
            try:
                while time.monotonic() < fin:
                    time.sleep(options["intervalle"])
                    simulateur.emettre(simulateur.hasard.choice([0, 1, 1, 2]))
                    simulateur.etape()
            except KeyboardInterrupt:
                self.stdout.write("Simulation arrêtée.")

        etat = simulateur.etat()
        self.stdout.write(self.style.SUCCESS(
            f"Centre {centre.code}, tickets simulés aujourd'hui : {etat['tickets']}, {etat['ouverts']} en cours, "
            f"{etat['termines']} terminés, {etat['annules']} annulés, {etat['en_attente']} en attente."
        ))
