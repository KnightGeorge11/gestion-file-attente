"""
Comptes du personnel.

- Comptes connectés et profils (décision du 24/11/2026).
- Création et modification simples (décision du 27/11/2026) : un compte en
  une page, ou plusieurs d'un coup à partir d'une liste de noms, au lieu de
  l'administration (compte, puis affectation, puis services).

L'administrateur de la plateforme voit et gère tous les comptes ;
l'administrateur de centre, ceux de son centre (centre actif), sans pouvoir
créer d'administrateurs ni modifier son propre compte.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from centres.access import est_administrateur_central
from centres.decorators import centre_actif_requis
from centres.models import Centre

from .formulaires import CompteForm, ListeComptesForm, ModifierCompteForm
from .gestion import (
    comptes_modifiables, creer_compte, decouper_nom, mot_de_passe_provisoire, affecter, peut_gerer_comptes,
)
from .models import AffectationUtilisateur, Utilisateur
from .presence import DUREE_PRESENCE, utilisateurs_connectes


def _centre(request):
    return getattr(request, "centre_actif", None)


def _exiger_gestion(request):
    """Plateforme, ou administrateur du centre actif."""
    if not (est_administrateur_central(request.user) or peut_gerer_comptes(request.user, _centre(request))):
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
    _exiger_gestion(request)
    plateforme = est_administrateur_central(request.user)
    connectes = utilisateurs_connectes()
    vue = request.GET.get("vue", "connectes")
    recherche = request.GET.get("q", "").strip()
    utilisateurs = Utilisateur.objects.prefetch_related("affectations__groupe", "affectations__centre").order_by(
        "last_name", "first_name", "username"
    )
    if not plateforme:
        # Administrateur de centre : le personnel de son centre seulement.
        utilisateurs = utilisateurs.filter(affectations__centre=_centre(request)).distinct()
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
        "nb_connectes": len(connectes) if plateforme else utilisateurs.model.objects.filter(
            pk__in=connectes.keys(), affectations__centre=_centre(request)).distinct().count(),
        "nb_comptes": Utilisateur.objects.count() if plateforme else Utilisateur.objects.filter(
            affectations__centre=_centre(request)).distinct().count(),
        "plateforme": plateforme,
        "peut_creer": _centre(request) is not None,
        "minutes_presence": int(DUREE_PRESENCE.total_seconds() // 60),
    })


@login_required
def profil(request, utilisateur_id):
    _exiger_gestion(request)
    from audit.models import JournalAudit
    from circuit.models import Appel, Passage

    plateforme = est_administrateur_central(request.user)
    visibles = Utilisateur.objects.all() if plateforme else Utilisateur.objects.filter(
        affectations__centre=_centre(request)).distinct()
    u = get_object_or_404(visibles, pk=utilisateur_id)
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
        "plateforme": plateforme,
        "modifiable": _centre(request) is not None
        and comptes_modifiables(request.user, _centre(request)).filter(pk=u.pk).exists(),
    })


def _contexte_formulaire(form, **extra):
    return {"page": "comptes", "form": form, "roles_services": form.roles_et_services(),
            "roles_salle": form.roles_avec_salle(), **extra}


@login_required
@centre_actif_requis
def compte_nouveau(request):
    """Un compte en une page : nom, rôle, service, salle (identifiant et mot de passe proposés)."""
    _exiger_gestion(request)
    centre = request.centre_actif
    form = CompteForm(request.POST or None, utilisateur=request.user, centre=centre)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        compte, mot_de_passe = creer_compte(
            centre, d["prenom"], d["nom"], d["groupe"], d["services_choisis"], d["salle"],
            d["identifiant"], d["mot_de_passe"],
        )
        return render(request, "comptes/comptes_crees.html", {
            "page": "comptes", "crees": [{"compte": compte, "mot_de_passe": mot_de_passe,
                                          "affiche": not d["mot_de_passe"]}],
        })
    return render(request, "comptes/compte_form.html", _contexte_formulaire(
        form, titre="Nouveau compte", liste=False))


@login_required
@centre_actif_requis
def comptes_liste(request):
    """Plusieurs comptes d'un coup : une personne par ligne, même rôle et même service."""
    _exiger_gestion(request)
    centre = request.centre_actif
    form = ListeComptesForm(request.POST or None, utilisateur=request.user, centre=centre)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        crees = []
        for ligne in d["noms"]:
            prenom, nom = decouper_nom(ligne)
            compte, mot_de_passe = creer_compte(centre, prenom, nom, d["groupe"], d["services_choisis"], d["salle"])
            crees.append({"compte": compte, "mot_de_passe": mot_de_passe, "affiche": True})
        return render(request, "comptes/comptes_crees.html", {"page": "comptes", "crees": crees})
    return render(request, "comptes/compte_form.html", _contexte_formulaire(
        form, titre="Plusieurs comptes", liste=True))


@login_required
@centre_actif_requis
def compte_modifier(request, utilisateur_id):
    """Nom, rôle, service et salle d'un compte dans le centre actif."""
    _exiger_gestion(request)
    centre = request.centre_actif
    compte = get_object_or_404(comptes_modifiables(request.user, centre), pk=utilisateur_id)
    affectation = AffectationUtilisateur.objects.filter(utilisateur=compte, centre=centre).first()
    initial = {"prenom": compte.first_name, "nom": compte.last_name}
    if affectation is not None:
        initial.update({
            "role": affectation.groupe_id, "salle": affectation.salle,
            "services": list(affectation.services_autorises.values_list("service_id", flat=True)),
        })
    form = ModifierCompteForm(request.POST or None, initial=initial, utilisateur=request.user, centre=centre)
    if request.method == "POST" and form.is_valid():
        d = form.cleaned_data
        compte.first_name, compte.last_name = d["prenom"].strip(), d["nom"].strip()
        compte.save(update_fields=["first_name", "last_name"])
        affecter(compte, centre, d["groupe"], d["services_choisis"], d["salle"])
        messages.success(request, f"Compte de {compte} enregistré.")
        return redirect("comptes:profil", compte.pk)
    return render(request, "comptes/compte_form.html", _contexte_formulaire(
        form, titre=f"Modifier {compte}", liste=False, compte=compte, affectation=affectation))


@require_POST
@login_required
@centre_actif_requis
def compte_action(request, utilisateur_id, action):
    """Nouveau mot de passe, désactivation ou réactivation (jamais de suppression)."""
    _exiger_gestion(request)
    compte = get_object_or_404(comptes_modifiables(request.user, request.centre_actif), pk=utilisateur_id)
    if action == "mot_de_passe":
        mot_de_passe = mot_de_passe_provisoire()
        compte.set_password(mot_de_passe)
        compte.save(update_fields=["password"])
        return render(request, "comptes/comptes_crees.html", {
            "page": "comptes", "nouveau_mot_de_passe": True,
            "crees": [{"compte": compte, "mot_de_passe": mot_de_passe, "affiche": True}],
        })
    if action in ("desactiver", "reactiver"):
        compte.is_active = action == "reactiver"
        compte.save(update_fields=["is_active"])
        if not compte.is_active:
            from .presence import deconnecter_partout

            deconnecter_partout(compte)
        messages.success(request, f"Compte de {compte} {'réactivé' if compte.is_active else 'désactivé'}.")
        return redirect("comptes:profil", compte.pk)
    raise PermissionDenied
