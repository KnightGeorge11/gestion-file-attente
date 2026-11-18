"""
Imprimante thermique SIMULÉE, pour tester l'impression réseau sans matériel.

Elle écoute sur le port TCP indiqué (9100 par défaut, comme une vraie
imprimante), reçoit les tickets ESC/POS envoyés par le serveur, et affiche
leur texte lisible dans la console. Les octets bruts peuvent être enregistrés.

Usage :
    python manage.py imprimante_simulee                 # 127.0.0.1:9100
    python manage.py imprimante_simulee --port 9101 --dossier D:\\tickets_test

Puis, dans l'administration : créer une « Imprimante de tickets » avec
l'adresse 127.0.0.1 et ce port, et l'associer à la borne.
"""

import re
import socketserver
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand

# Séquences ESC/POS utilisées par circuit.impression (retirées pour l'affichage).
SEQUENCES = re.compile(rb"\x1b@|\x1b[aEtd].|\x1d!.|\x1dV..", re.S)


def texte_lisible(donnees, encodage="cp858"):
    propre = SEQUENCES.sub(b"", donnees)
    return propre.decode(encodage, "replace").strip()


class Command(BaseCommand):
    help = "Imprimante thermique simulée (TCP 9100) pour tester l'impression des tickets."

    def add_arguments(self, parser):
        parser.add_argument("--adresse", default="127.0.0.1")
        parser.add_argument("--port", type=int, default=9100)
        parser.add_argument("--dossier", help="Enregistre chaque ticket reçu (.bin et .txt).")

    def handle(self, *args, **options):
        sortie = self.stdout
        dossier = Path(options["dossier"]) if options["dossier"] else None
        if dossier:
            dossier.mkdir(parents=True, exist_ok=True)

        class Gestionnaire(socketserver.BaseRequestHandler):
            def handle(self):
                morceaux = []
                while True:
                    morceau = self.request.recv(4096)
                    if not morceau:
                        break
                    morceaux.append(morceau)
                donnees = b"".join(morceaux)
                texte = texte_lisible(donnees)
                horodatage = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                sortie.write(f"\n=== Ticket reçu ({len(donnees)} octets) ===\n{texte}\n" + "=" * 34)
                if dossier:
                    (dossier / f"ticket_{horodatage}.bin").write_bytes(donnees)
                    (dossier / f"ticket_{horodatage}.txt").write_text(texte, encoding="utf-8")

        socketserver.TCPServer.allow_reuse_address = True
        with socketserver.TCPServer((options["adresse"], options["port"]), Gestionnaire) as serveur:
            sortie.write(self.style.SUCCESS(
                f"Imprimante simulée à l'écoute sur {options['adresse']}:{options['port']} (Ctrl+C pour arrêter)."
            ))
            try:
                serveur.serve_forever()
            except KeyboardInterrupt:
                sortie.write("Imprimante simulée arrêtée.")
