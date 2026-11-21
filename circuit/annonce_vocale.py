"""
Annonce vocale des appels, fabriquée par le serveur (décision du 21/11/2026).

Les navigateurs des TV Android (TV Bro, Puffin TV… : moteur WebView) n'ont
pas de synthèse vocale : la voix ne peut pas venir de la TV. Le serveur
fabrique donc un fichier audio (« Ticket M 0 0 1, Infirmerie, Salle 2 ») avec
une voix française installée sur la machine, sans Internet, et l'écran le
joue. Les fichiers sont gardés en cache (un par texte).

Voix utilisée, selon le système du serveur :
- Windows : voix française de Windows (ex. Microsoft Hortense), par System.Speech ;
- Ubuntu : pico2wave (paquet libttspico-utils, meilleure qualité) ou espeak-ng.

Le texte vient toujours de l'appel enregistré (numéro, service, poste) :
jamais d'un texte envoyé par le navigateur.
"""

import hashlib
import logging
import os
import platform
import shutil
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

from django.conf import settings

journal = logging.getLogger(__name__)

DELAI_SECONDES = 20

SCRIPT_WINDOWS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$voix = New-Object System.Speech.Synthesis.SpeechSynthesizer
$francaise = $voix.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -like 'fr*' } | Select-Object -First 1
if ($null -eq $francaise) { exit 3 }
if ($env:ANNONCE_FICHIER -eq '') { exit 0 }
$voix.SelectVoice($francaise.VoiceInfo.Name)
$voix.Rate = -1
$voix.SetOutputToWaveFile($env:ANNONCE_FICHIER)
$voix.Speak($env:ANNONCE_TEXTE)
$voix.Dispose()
"""


def texte_annonce(appel):
    """« Ticket M 0 0 1, Infirmerie, Salle 2 » (comme l'annonce affichée sur l'écran)."""
    texte = f"Ticket {' '.join(appel.numero_ticket)}, {appel.nom_service}"
    if appel.poste:
        texte += f", {appel.poste}"
    return texte


def _commande(fichier, texte):
    """Commande de synthèse du système, ou None si aucune voix française n'est disponible."""
    if platform.system() == "Windows":
        powershell = shutil.which("powershell") or shutil.which("powershell.exe")
        if powershell:
            return [powershell, "-NoProfile", "-NonInteractive", "-Command", SCRIPT_WINDOWS]
        return None
    if shutil.which("pico2wave"):
        return ["pico2wave", "-l", "fr-FR", "-w", str(fichier), texte]
    if shutil.which("espeak-ng"):
        return ["espeak-ng", "-v", "fr", "-s", "150", "-w", str(fichier), texte]
    return None


def _executer(commande, fichier, texte):
    environnement = {**os.environ, "ANNONCE_FICHIER": str(fichier), "ANNONCE_TEXTE": texte}
    resultat = subprocess.run(   # noqa: S603 - commande fixe, texte passé hors de la ligne de commande sous Windows
        commande, env=environnement, capture_output=True, timeout=DELAI_SECONDES, check=False,
    )
    return resultat.returncode


@lru_cache(maxsize=1)
def voix_disponible():
    """Vrai si le serveur sait fabriquer une annonce en français."""
    if not getattr(settings, "ANNONCE_VOCALE_SERVEUR", True):
        return False
    commande = _commande(Path("essai.wav"), "essai")
    if commande is None:
        return False
    if platform.system() != "Windows":
        return True
    try:
        return _executer(commande, "", "") == 0   # fichier vide : vérifie seulement la voix
    except (OSError, subprocess.SubprocessError):
        return False


def dossier_annonces():
    dossier = Path(settings.MEDIA_ROOT) / "annonces"
    dossier.mkdir(parents=True, exist_ok=True)
    return dossier


def fichier_annonce(texte):
    """
    Chemin du fichier WAV de ce texte (fabriqué au premier besoin), ou None si
    la synthèse est impossible.
    """
    empreinte = hashlib.sha256(texte.encode("utf-8")).hexdigest()[:32]
    fichier = dossier_annonces() / f"{empreinte}.wav"
    if fichier.exists() and fichier.stat().st_size > 0:
        return fichier
    if not voix_disponible():
        return None
    # Fabriqué dans un fichier temporaire puis renommé : deux écrans qui
    # demandent la même annonce en même temps ne lisent jamais un fichier à moitié écrit.
    descripteur, temporaire = tempfile.mkstemp(suffix=".wav", dir=dossier_annonces())
    os.close(descripteur)
    try:
        commande = _commande(Path(temporaire), texte)
        if commande is None or _executer(commande, temporaire, texte) != 0 or os.path.getsize(temporaire) == 0:
            journal.warning("Annonce vocale impossible pour « %s ».", texte)
            return None
        os.replace(temporaire, fichier)
        return fichier
    except (OSError, subprocess.SubprocessError) as erreur:
        journal.warning("Annonce vocale impossible pour « %s » : %s", texte, erreur)
        return None
    finally:
        if os.path.exists(temporaire):
            os.remove(temporaire)
