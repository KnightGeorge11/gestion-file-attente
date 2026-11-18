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

from .calculs import calculer

PERIODE_MAX_JOURS = 366


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
        centres = list(centres_autorises(utilisateur))
        libelle = "Tous les centres autorisés"
    else:
        centres = [centre]
        libelle = centre.nom

    peut_reseau = est_administrateur_central(utilisateur) or a_droit(utilisateur, "circuit.consulter_reseau", centre)
    return centres, libelle, date_debut, date_fin, reseau, peut_reseau


@centre_actif_requis
def tableau(request):
    centres, libelle, du, au, reseau, peut_reseau = _parametres(request)
    return render(
        request,
        "statistiques/tableau.html",
        {
            "stats": calculer(centres, du, au),
            "libelle_centres": libelle,
            "du": du,
            "au": au,
            "reseau": reseau,
            "peut_reseau": peut_reseau,
            "requete": request.GET.urlencode(),
        },
    )


def _lignes_export(stats):
    """Sections (titre, en-têtes, lignes) communes aux exports."""
    return [
        ("Synthèse", ["Indicateur", "Valeur"], [
            ["Tickets émis", stats["total_tickets"]],
            ["Durée totale moyenne (min)", stats["duree_totale_moy"]],
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
    for titre, entetes, lignes in _lignes_export(stats):
        feuille = classeur.create_sheet(titre[:31])
        feuille.append([f"{libelle} — du {du:%d/%m/%Y} au {au:%d/%m/%Y}"])
        feuille.append([])
        feuille.append(entetes)
        for cellule in feuille[3]:
            cellule.font = Font(bold=True)
        for ligne in lignes:
            feuille.append(ligne)
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

    centres, libelle, du, au, _, _ = _parametres(request)
    stats = calculer(centres, du, au)
    flux = io.BytesIO()
    document = SimpleDocTemplate(flux, pagesize=landscape(A4), leftMargin=1.5 * cm, rightMargin=1.5 * cm,
                                 topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                                 title="Statistiques des files d'attente")
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("SMIE FUNTOA — Statistiques des files d'attente", styles["Title"]),
        Paragraph(f"{libelle} — du {du:%d/%m/%Y} au {au:%d/%m/%Y}", styles["Normal"]),
        Spacer(1, 0.4 * cm),
    ]
    for titre, entetes, lignes in _lignes_export(stats):
        elements.append(Paragraph(titre, styles["Heading2"]))
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
