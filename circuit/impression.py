"""
Impression des tickets sur une imprimante thermique réseau (option A).

La tablette n'imprime rien : quand le patient prend son ticket, le serveur
envoie directement le ticket à l'imprimante de la borne (Ethernet ou Wi-Fi),
en ESC/POS sur le port TCP 9100 (« impression brute », gérée par la quasi
totalité des imprimantes thermiques de caisse). Aucune boîte de dialogue.

Si l'imprimante ne répond pas, le ticket reste valable : la borne affiche le
numéro au patient et l'accueil voit une alerte.
"""

import socket
import unicodedata

from django.utils import timezone

from .models import Evenement

ESC = b"\x1b"
GS = b"\x1d"

INITIALISER = ESC + b"@"
CENTRER = ESC + b"a\x01"
ALIGNER_GAUCHE = ESC + b"a\x00"
GRAS = ESC + b"E\x01"
NORMAL_GRAS = ESC + b"E\x00"
TAILLE_NORMALE = GS + b"!\x00"
TAILLE_DOUBLE = GS + b"!\x11"      # double largeur et hauteur
TAILLE_GEANTE = GS + b"!\x44"      # 5 x largeur et hauteur (numéro du ticket)
AVANCER_ET_COUPER = ESC + b"d\x04" + GS + b"V\x42\x00"

# Sélection de la table de caractères (ESC t n) : n = 19 pour PC858, 2 pour PC850.
TABLES = {"cp858": (b"\x13", "cp858"), "cp850": (b"\x02", "cp850")}

DELAI_SECONDES = 4


class ErreurImpression(Exception):
    """L'imprimante n'a pas pu recevoir le ticket."""


def _texte(valeur, page_codes):
    if page_codes == "ascii" or page_codes not in TABLES:
        sans_accents = unicodedata.normalize("NFKD", valeur)
        return "".join(c for c in sans_accents if not unicodedata.combining(c)).encode("ascii", "replace")
    return valeur.encode(TABLES[page_codes][1], "replace")


def contenu_ticket(ticket, imprimante, devant=0):
    """Octets ESC/POS du ticket (testables sans imprimante)."""
    pc = imprimante.page_codes
    t = lambda valeur: _texte(valeur, pc)  # noqa: E731
    local = timezone.localtime(ticket.date_emission)
    if ticket.parcours_id:
        destination = ticket.parcours.libelle_borne
    else:
        demande = getattr(ticket, "demande", None)
        destination = (demande.motif.libelle if demande and demande.motif_id else "Autre demande")
    trait = t("-" * imprimante.largeur_caracteres)

    morceaux = [INITIALISER]
    if pc in TABLES:
        morceaux.append(ESC + b"t" + TABLES[pc][0])
    morceaux += [
        CENTRER, GRAS, t(ticket.centre.nom), b"\n", NORMAL_GRAS,
        trait, b"\n",
        TAILLE_GEANTE, GRAS, t(ticket.numero), b"\n", NORMAL_GRAS, TAILLE_NORMALE,
        TAILLE_DOUBLE, t(destination), b"\n", TAILLE_NORMALE,
        t(ticket.get_mode_display()), b"\n",
        t(local.strftime("%d/%m/%Y  %H:%M")), b"\n",
        trait, b"\n",
        GRAS, t("Présentez-vous à l'accueil."), b"\n", NORMAL_GRAS,
        t(f"{devant} personne(s) avant vous."), b"\n",
        t("Surveillez l'écran d'appel."), b"\n",
        ALIGNER_GAUCHE, AVANCER_ET_COUPER,
    ]
    return b"".join(morceaux)


def envoyer(imprimante, donnees):
    """Envoie les octets à l'imprimante (TCP brut). Lève ErreurImpression."""
    try:
        with socket.create_connection((imprimante.adresse_ip, imprimante.port), timeout=DELAI_SECONDES) as connexion:
            connexion.sendall(donnees)
    except OSError as erreur:
        raise ErreurImpression(
            f"Imprimante {imprimante.libelle} ({imprimante.adresse_ip}:{imprimante.port}) injoignable : {erreur}"
        ) from erreur


def imprimer_ticket(ticket, borne, devant=0):
    """
    Imprime le ticket sur l'imprimante de la borne et l'inscrit dans
    l'historique. Retourne True (imprimé), False (échec) ou None (aucune
    imprimante réseau : impression par le navigateur).
    """
    imprimante = borne.imprimante if borne else None
    if imprimante is None or not imprimante.actif:
        return None
    try:
        envoyer(imprimante, contenu_ticket(ticket, imprimante, devant))
    except ErreurImpression as erreur:
        _evenement(ticket, Evenement.Type.ECHEC_IMPRESSION, borne, imprimante, erreur=str(erreur))
        return False
    _evenement(ticket, Evenement.Type.IMPRESSION, borne, imprimante)
    return True


def _evenement(ticket, type_, borne, imprimante, **details):
    Evenement.objects.create(
        centre_id=ticket.centre_id,
        ticket=ticket,
        type=type_,
        date=timezone.now(),
        details={"borne": borne.code, "imprimante": imprimante.libelle, **details},
    )


def imprimer_test(imprimante):
    """Ticket de test (administration). Lève ErreurImpression en cas d'échec."""
    pc = imprimante.page_codes
    donnees = b"".join([
        INITIALISER,
        ESC + b"t" + TABLES[pc][0] if pc in TABLES else b"",
        CENTRER, TAILLE_DOUBLE, _texte("TEST IMPRIMANTE", pc), b"\n", TAILLE_NORMALE,
        _texte(imprimante.libelle, pc), b"\n",
        _texte("Accents : é è à ç ô ù", pc), b"\n",
        _texte(timezone.localtime().strftime("%d/%m/%Y %H:%M"), pc), b"\n",
        AVANCER_ET_COUPER,
    ])
    envoyer(imprimante, donnees)


def _derniere_impression(borne):
    return (
        Evenement.objects.filter(
            centre_id=borne.centre_id,
            type__in=[Evenement.Type.IMPRESSION, Evenement.Type.ECHEC_IMPRESSION],
            details__borne=borne.code,
        )
        .order_by("-date", "-id")
        .first()
    )


def etat_imprimante_borne(borne):
    """
    État de l'imprimante d'une borne, affiché sur la borne elle-même :
    « pret », « echec » (dernière impression ratée) ou « navigateur »
    (aucune imprimante suivie : impression par la tablette).
    Retourne (état, dernier événement d'impression ou None).
    """
    dernier = _derniere_impression(borne)
    # Imprimante réseau rattachée, ou imprimante Bluetooth de l'application Android.
    suivie = (borne.imprimante_id is not None and borne.imprimante.actif) or bool(
        dernier and dernier.details.get("bluetooth")
    )
    if not suivie:
        return "navigateur", dernier
    if dernier is not None and dernier.type == Evenement.Type.ECHEC_IMPRESSION:
        return "echec", dernier
    return "pret", dernier


def etat_imprimantes(centre):
    """
    Bornes du centre dont la dernière tentative d'impression a échoué
    (l'alerte disparaît dès qu'une impression réussit).
    """
    from centres.models import Borne

    alertes = []
    for borne in Borne.objects.filter(centre=centre, actif=True).select_related("imprimante"):
        dernier = _derniere_impression(borne)
        if borne.imprimante_id is None and not (dernier and dernier.details.get("bluetooth")):
            continue
        if dernier is not None and dernier.type == Evenement.Type.ECHEC_IMPRESSION:
            alertes.append({
                "borne": borne, "date": dernier.date, "ticket": dernier.ticket.numero,
                "bluetooth": bool(dernier.details.get("bluetooth")),
            })
    return alertes


def resultat_impression_android(ticket, borne, reussi, erreur=""):
    """
    Résultat signalé par l'application Android (imprimante Bluetooth) : il
    est inscrit dans l'historique comme une impression réseau, pour que
    l'accueil voie l'alerte si l'imprimante ne répond plus.
    """
    details = {"borne": borne.code, "imprimante": "Bluetooth (application Android)", "bluetooth": True}
    if not reussi:
        details["erreur"] = (erreur or "Imprimante Bluetooth injoignable")[:200]
    Evenement.objects.create(
        centre_id=ticket.centre_id,
        ticket=ticket,
        type=Evenement.Type.IMPRESSION if reussi else Evenement.Type.ECHEC_IMPRESSION,
        date=timezone.now(),
        details=details,
    )
