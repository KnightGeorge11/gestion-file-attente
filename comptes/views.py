"""
Comptes connectés et profils (décision du 24/11/2026), réservés à
l'administrateur de la plateforme. Lecture seule : les modifications se font
dans l'administration.
"""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from django.utils import timezone

from centres.access import est_administrateur_central
from centres.models import Centre

from .models import AffectationUtilisateur, Utilisateur
from .presence import DUREE_PRESENCE, utilisateurs_connectes


def _exiger_admin_plateforme(request):
    if not est_administrateur_central(request.user):
        raise PermissionDenied


def appareil(navigateur):
    """« Chrome · Windows » à partir de l'en-tête User-Agent (approximatif)."""
    n = navigateur or ""
    nom = next((libelle for cle, libelle in (
        ("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari"),
    ) if cle in n), "")
    systeme = next((libelle for cle, libelle in (
        ("Android", "Android"), ("iPhone", "iPhone"), ("iPad", "iPad"), ("Windows", "Windows"),
        ("Mac OS", "Mac"), ("Linux", "Linux"),
    ) if cle in n), "")
    return " · ".join(filter(None, [nom, systeme])) or "—"


def _roles(utilisateur):
    return [
        f"{a.groupe.name if a.groupe_id else a.get_role_display()} ({a.centre.code})"
        for a in utilisateur.affectations.all() if a.actif
    ]


@login_required
def comptes(request):
    _exiger_admin_plateforme(request)
    connectes = utilisateurs_connectes()
    vue = request.GET.get("vue", "connectes")
    recherche = request.GET.get("q", "").strip()
    utilisateurs = Utilisateur.objects.prefetch_related("affectations__groupe", "affectations__centre").order_by(
        "last_name", "first_name", "username"
    )
    if vue == "connectes":
        utilisateurs = utilisateurs.filter(pk__in=connectes.keys())
    if recherche:
        utilisateurs = utilisateurs.filter(
            Q(username__icontains=recherche) | Q(first_name__icontains=recherche) | Q(last_name__icontains=recherche)
        )
    centres = {c.pk: c for c in Centre.objects.all()}
    maintenant = timezone.now()
    lignes = []
    for u in utilisateurs[:500]:
        info = connectes.get(u.pk)
        lignes.append({
            "utilisateur": u,
            "roles": _roles(u),
            "connecte": info is not None,
            "vu_il_y_a": int((maintenant - info["vu_a"]).total_seconds() // 60) if info else None,
            "centre": centres.get(info["centre_id"]) if info else None,
            "ip": info["ip"] if info else "",
            "appareil": appareil(info["navigateur"]) if info else "",
            "sessions": info["sessions"] if info else 0,
        })
    return render(request, "comptes/comptes.html", {
        "page": "comptes",
        "lignes": lignes,
        "vue": vue,
        "recherche": recherche,
        "nb_connectes": len(connectes),
        "nb_comptes": Utilisateur.objects.count(),
        "minutes_presence": int(DUREE_PRESENCE.total_seconds() // 60),
    })


@login_required
def profil(request, utilisateur_id):
    _exiger_admin_plateforme(request)
    from audit.models import JournalAudit
    from circuit.models import Appel, Passage

    u = get_object_or_404(Utilisateur, pk=utilisateur_id)
    info = utilisateurs_connectes().get(u.pk)
    affectations = (
        AffectationUtilisateur.objects.filter(utilisateur=u)
        .select_related("centre", "groupe").prefetch_related("services_autorises__service", "permissions")
        .order_by("-actif", "centre__code")
    )
    debut_jour = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return render(request, "comptes/profil.html", {
        "page": "comptes",
        "u": u,
        "info": info,
        "centre_ouvert": Centre.objects.filter(pk=info["centre_id"]).first() if info and info["centre_id"] else None,
        "appareil": appareil(info["navigateur"]) if info else "",
        "affectations": affectations,
        "prises_en_charge_jour": Passage.objects.filter(pris_en_charge_par=u, date_debut__gte=debut_jour).count(),
        "appels_jour": Appel.objects.filter(agent=u, date__gte=debut_jour).count(),
        "journal": JournalAudit.objects.filter(utilisateur=u).select_related("centre").order_by("-date")[:20],
    })
