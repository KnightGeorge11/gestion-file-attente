import datetime
import io

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from django.shortcuts import render

from audit.journal import journaliser
from audit.models import JournalAudit
from centres.access import centres_autorises, est_administrateur_central
from centres.decorators import centre_actif_requis
from comptes.permissions import a_droit
from core.timeutils import maintenant_centre

from . import calculs
from .calculs import calculer

PERIODE_MAX_JOURS = 366


def cellule_sure(valeur):
    """
    Texte inoffensif dans un tableur : une valeur commençant par =, +, -, @
    (nom d'agent, de service, de centre…) serait exécutée comme une formule
    à l'ouverture du fichier. On la préfixe d'une apostrophe.
    """
    if isinstance(valeur, str) and valeur[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + valeur
    return valeur


def _parametres(request):
    """Centres et période autorisés (contrôle côté serveur)."""
    utilisateur = request.user
    centre = request.centre_actif
    if not a_droit(utilisateur, "circuit.voir_statistiques", centre):
        raise PermissionDenied

    aujourd_hui = maintenant_centre(centre).date()

    def lire_date(nom, defaut):
        try:
            return datetime.date.fromisoformat(request.GET.get(nom, ""))
        except ValueError:
            return defaut

    date_debut = lire_date("du", aujourd_hui)
    date_fin = lire_date("au", aujourd_hui)
    if date_fin < date_debut:
        date_debut, date_fin = date_fin, date_debut
    if (date_fin - date_debut).days > PERIODE_MAX_JOURS:
        date_debut = date_fin - datetime.timedelta(days=PERIODE_MAX_JOURS)

    reseau = request.GET.get("reseau") == "1" and (
        est_administrateur_central(utilisateur) or a_droit(utilisateur, "circuit.consulter_reseau", centre)
    )
    if reseau:
        # Seulement les centres où il a le droit de voir les statistiques
        # (pas ceux où il n'est qu'agent, relecture du 23/11/2026).
        centres = [
            c for c in centres_autorises(utilisateur)
            if est_administrateur_central(utilisateur) or a_droit(utilisateur, "circuit.voir_statistiques", c)
        ]
        libelle = "Tous les centres autorisés"
    else:
        centres = [centre]
        libelle = centre.nom

    peut_reseau = est_administrateur_central(utilisateur) or a_droit(utilisateur, "circuit.consulter_reseau", centre)
    return centres, libelle, date_debut, date_fin, reseau, peut_reseau


@centre_actif_requis
def tableau(request):
    centres, libelle, du, au, reseau, peut_reseau = _parametres(request)
    aujourd_hui = maintenant_centre(request.centre_actif).date()
    fuseau = request.centre_actif.fuseau_horaire
    stats = calculer(centres, du, au)
    calculs_service = stats["par_service"]
    return render(
        request,
        "statistiques/tableau.html",
        {
            "stats": stats,
            # Pages du 24/11/2026 : affluence, évolution, demandes, quotas.
            "affluence": calculs.affluence(centres, du, au, fuseau),
            "evolution": calculs.evolution(centres, du, au),
            "max_temps_service": max(
                [l[c] or 0 for l in calculs_service for c in ("attente_moy", "prise_en_charge_moy")] or [0]
            ),
            "max_agent": max([l["prises_en_charge"] for l in stats["par_agent"]] or [0]),
            "demandes": calculs.demandes(centres, du, au),
            "quotas_periodes": calculs.quotas_par_periode(centres, du, au),
            "periodes_rapides": [
                ("Aujourd'hui", aujourd_hui, aujourd_hui),
                ("7 jours", aujourd_hui - datetime.timedelta(days=6), aujourd_hui),
                ("30 jours", aujourd_hui - datetime.timedelta(days=29), aujourd_hui),
                ("90 jours", aujourd_hui - datetime.timedelta(days=89), aujourd_hui),
            ],
            "libelle_centres": libelle,
            "du": du,
            "au": au,
            "reseau": reseau,
            "peut_reseau": peut_reseau,
            "requete": request.GET.urlencode(),
        },
    )


@centre_actif_requis
def direct(request):
    """Tableau du jour en direct (24/11/2026) : centre actif, rafraîchi toutes les 15 s."""
    if not a_droit(request.user, "circuit.voir_statistiques", request.centre_actif):
        raise PermissionDenied
    return render(request, "statistiques/direct.html", {"direct": calculs.direct(request.centre_actif)})


def _lignes_export(stats, demandes=None, quotas_periodes=None):
    """Sections (titre, en-têtes, lignes) communes aux exports."""
    return [
        ("Synthèse", ["Indicateur", "Valeur"], [
            ["Tickets émis", stats["total_tickets"]],
            ["Durée totale moyenne, tickets de soins (min)", stats["duree_totale_moy"]],
            *[[f"Tickets - {k}", v] for k, v in stats["par_statut"].items()],
            *[[f"Mode - {k}", v] for k, v in stats["par_mode"].items()],
            ["Places de quota réservées", stats["quotas"]["reservees"]],
            ["Dérogations au quota", stats["quotas"]["derogations"]],
            ["Places libérées", stats["quotas"]["liberees"]],
        ]),
        ("Par parcours", ["Parcours", "Tickets", "Terminés", "Durée totale moy. (min)"],
         [[l["parcours"], l["tickets"], l["termines"], l["duree_totale_moy"]] for l in stats["par_parcours"]]),
        ("Par service", ["Service", "Passages", "Terminés", "Absences", "Transferts reçus",
                         "Attente moy. (min)", "Attente max (min)", "Prise en charge moy. (min)"],
         [[l["service"], l["passages"], l["termines"], l["absences"], l["transferts_recus"],
           l["attente_moy"], l["attente_max"], l["prise_en_charge_moy"]] for l in stats["par_service"]]),
        ("Transferts pharmacie", ["Service d'origine", "Transferts"],
         [[l["service_origine"], l["transferts"]] for l in stats["transferts"]]),
        ("Par agent", ["Agent", "Service", "Prises en charge", "Durée moy. (min)"],
         [[l["agent"], l["service"], l["prises_en_charge"], l["prise_en_charge_moy"]] for l in stats["par_agent"]]),
        *([("Demandes et médecin en chef",
            ["Destinataire", "Reçues", "Sans réponse", "Délai moy. de réponse (min)"],
            [[l["destinataire"], l["recues"], l["sans_reponse"], l["delai_moy"]] for l in demandes["par_destinataire"]])]
          if demandes is not None else []),
        *([("Quota dentiste par période",
            ["Période", "Jours avec réservations", "Jours quota atteint", "Places occupées", "Places prévues", "Remplissage (%)"],
            [[l["periode"], l["creneaux"], l["atteints"], l["occupees"], l["places"], l["taux"]] for l in quotas_periodes])]
          if quotas_periodes is not None else []),
    ]


def _journaliser_export(request, format_, libelle, du, au):
    journaliser(
        JournalAudit.Action.EXPORT, request.user, centre=request.centre_actif,
        details={"format": format_, "centres": libelle, "du": str(du), "au": str(au)}, request=request,
    )


@centre_actif_requis
def export_excel(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font

    centres, libelle, du, au, _, _ = _parametres(request)
    stats = calculer(centres, du, au)
    classeur = Workbook()
    classeur.remove(classeur.active)
    for titre, entetes, lignes in _lignes_export(stats, calculs.demandes(centres, du, au), calculs.quotas_par_periode(centres, du, au)):
        feuille = classeur.create_sheet(titre[:31])
        feuille.append([cellule_sure(f"{libelle} — du {du:%d/%m/%Y} au {au:%d/%m/%Y}")])
        feuille.append([])
        feuille.append(entetes)
        for cellule in feuille[3]:
            cellule.font = Font(bold=True)
        for ligne in lignes:
            feuille.append([cellule_sure(v) for v in ligne])
        for colonne in feuille.columns:
            largeur = max(len(str(c.value or "")) for c in colonne[2:]) if feuille.max_row > 2 else 10
            feuille.column_dimensions[colonne[0].column_letter].width = min(max(largeur + 2, 12), 45)
    flux = io.BytesIO()
    classeur.save(flux)
    _journaliser_export(request, "xlsx", libelle, du, au)
    reponse = HttpResponse(
        flux.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    reponse["Content-Disposition"] = f'attachment; filename="statistiques_{du}_{au}.xlsx"'
    return reponse


@centre_actif_requis
def export_pdf(request):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    centres, libelle, du, au, _, _ = _parametres(request)
    stats = calculer(centres, du, au)
    flux = io.BytesIO()
    document = SimpleDocTemplate(flux, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                                 topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                                 title="Statistiques des files d'attente")
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("SMIE FUNTOA — Statistiques des files d'attente", styles["Title"]),
        # Paragraph interprète le texte comme du balisage : « & » ou « < »
        # dans un nom de centre casserait le PDF.
        Paragraph(escape(f"{libelle} — du {du:%d/%m/%Y} au {au:%d/%m/%Y}"), styles["Normal"]),
        Spacer(1, 0.4 * cm),
    ]
    for titre, entetes, lignes in _lignes_export(stats, calculs.demandes(centres, du, au), calculs.quotas_par_periode(centres, du, au)):
        elements.append(Paragraph(escape(titre), styles["Heading2"]))
        donnees = [entetes] + [["" if v is None else str(v) for v in ligne] for ligne in lignes]
        if not lignes:
            donnees.append(["Aucune donnée"] + [""] * (len(entetes) - 1))
        table = Table(donnees, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b6e4f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f7")]),
        ]))
        elements += [table, Spacer(1, 0.4 * cm)]
    document.build(elements)
    _journaliser_export(request, "pdf", libelle, du, au)
    reponse = HttpResponse(flux.getvalue(), content_type="application/pdf")
    reponse["Content-Disposition"] = f'attachment; filename="statistiques_{du}_{au}.pdf"'
    return reponse
