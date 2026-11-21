"""
Recherche des écrans connectés au même réseau (Wi-Fi ou câble) que le serveur.

Les TV connectées, Chromecast, Android TV et la plupart des Samsung, LG,
Sony, Philips… s'annoncent sur le réseau local par UPnP (SSDP) : le serveur
envoie une question à tout le réseau (multidiffusion 239.255.255.250:1900),
les appareils répondent avec l'adresse de leur fiche descriptive (nom,
marque, modèle). Rien ne passe par Internet.

Sécurité : seules les fiches d'appareils du réseau local sont lues (jamais une
adresse Internet ni l'ordinateur lui-même), avec un délai court et une
taille limitée.
"""

import socket
import time
import urllib.request
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .reseau import est_adresse_privee

GROUPE_SSDP = ("239.255.255.250", 1900)

# Types d'appareils qui affichent une image (TV, boîtiers, projecteurs).
CIBLES = (
    "urn:dial-multiscreen-org:service:dial:1",        # TV connectées, Chromecast, Android TV
    "urn:schemas-upnp-org:device:MediaRenderer:1",    # TV et lecteurs multimédias
)
MARQUES_ECRAN = (
    "samsung", "lg", "sony", "philips", "panasonic", "tcl", "hisense", "sharp", "toshiba",
    "google", "chromecast", "xiaomi", "roku", "amazon", "vizio", "skyworth", "nvidia",
)
TAILLE_MAX_FICHE = 64 * 1024


def _requete(cible, attente):
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {GROUPE_SSDP[0]}:{GROUPE_SSDP[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {max(1, int(attente))}\r\n"
        f"ST: {cible}\r\n\r\n"
    ).encode("ascii")


def _entetes(reponse):
    entetes = {}
    for ligne in reponse.decode("utf-8", "replace").split("\r\n")[1:]:
        if ":" in ligne:
            cle, valeur = ligne.split(":", 1)
            entetes[cle.strip().lower()] = valeur.strip()
    return entetes


def _lire_fiche(adresse):
    """Nom, marque et modèle depuis la fiche UPnP de l'appareil (réseau local uniquement)."""
    hote = urlsplit(adresse).hostname or ""
    if not est_adresse_privee(hote):
        return {}
    try:
        with urllib.request.urlopen(adresse, timeout=2) as reponse:   # noqa: S310 - adresse locale vérifiée
            contenu = reponse.read(TAILLE_MAX_FICHE)
        racine = ElementTree.fromstring(contenu)
    except (OSError, ValueError, ElementTree.ParseError):
        return {}

    def champ(nom):
        for element in racine.iter():
            if element.tag.rsplit("}", 1)[-1] == nom and element.text:
                return element.text.strip()[:120]
        return ""

    return {
        "nom": champ("friendlyName"),
        "marque": champ("manufacturer"),
        "modele": champ("modelName"),
        "type": champ("deviceType"),
    }


def rechercher_ecrans(attente=3.0, envoyer=None):
    """
    Écrans annoncés sur le réseau local. Retourne une liste de dictionnaires
    {adresse_ip, nom, marque, modele, type, dial} triée par nom.

    `envoyer` permet de remplacer la partie réseau (tests).
    """
    reponses = envoyer(attente) if envoyer else _interroger_reseau(attente)
    ecrans = {}
    for ip, brut in reponses:
        if not est_adresse_privee(ip):
            continue
        entetes = _entetes(brut)
        cible = entetes.get("st", "")
        ecran = ecrans.setdefault(ip, {"adresse_ip": ip, "dial": False, "emplacement": ""})
        if "dial" in cible:
            ecran["dial"] = True
        if entetes.get("location") and not ecran["emplacement"]:
            ecran["emplacement"] = entetes["location"]
    resultat = []
    for ecran in ecrans.values():
        fiche = _lire_fiche(ecran["emplacement"]) if ecran["emplacement"] else {}
        ecran.update({cle: fiche.get(cle, "") for cle in ("nom", "marque", "modele", "type")})
        marque = f"{ecran['marque']} {ecran['modele']} {ecran['nom']}".lower()
        if ecran["dial"] or "mediarenderer" in ecran["type"].lower() or any(m in marque for m in MARQUES_ECRAN):
            ecran["nom"] = ecran["nom"] or f"Écran {ecran['adresse_ip']}"
            del ecran["emplacement"]
            resultat.append(ecran)
    return sorted(resultat, key=lambda e: e["nom"].lower())


def _interroger_reseau(attente):
    """Envoie la recherche SSDP et collecte les réponses pendant `attente` secondes."""
    prise = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    reponses = []
    try:
        prise.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        prise.settimeout(0.5)
        prise.bind(("", 0))
        for cible in CIBLES:
            prise.sendto(_requete(cible, attente), GROUPE_SSDP)
        fin = time.monotonic() + attente
        while time.monotonic() < fin:
            try:
                donnees, (ip, _) = prise.recvfrom(4096)
            except socket.timeout:
                continue
            reponses.append((ip, donnees))
    except OSError:
        pass   # pas de réseau : aucune réponse
    finally:
        prise.close()
    return reponses
