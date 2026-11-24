from django.contrib import admin
from django.contrib.admin.utils import get_fields_from_path
from django.core.exceptions import FieldDoesNotExist
from django.utils.html import format_html

from centres.access import est_administrateur_central
from centres.models import Centre


def centres_administres(utilisateur, modele):
    """
    Centres dont l'utilisateur administre ce modèle : ceux où son affectation
    active lui donne le droit de consulter le modèle (« app.view_modele »).

    Les droits de l'administration Django sont l'union des droits de toutes
    les affectations (comptes.backends) : sans ce filtre, un administrateur
    du centre A, simple agent dans le centre B, administrerait aussi B
    (audit du 20/11/2026).
    """
    if est_administrateur_central(utilisateur):
        return Centre.objects.filter(actif=True)
    from comptes.models import AffectationUtilisateur
    from comptes.permissions import permissions_affectation

    code = f"{modele._meta.app_label}.view_{modele._meta.model_name}"
    # Droit donné au compte lui-même (par l'administrateur de la plateforme) :
    # il vaut dans tous les centres où le compte est affecté.
    droit_du_compte = utilisateur.user_permissions.filter(
        content_type__app_label=modele._meta.app_label, codename=f"view_{modele._meta.model_name}"
    ).exists()
    ids = [
        affectation.centre_id
        for affectation in AffectationUtilisateur.objects.filter(
            utilisateur=utilisateur, actif=True, centre__actif=True
        )
        if droit_du_compte or code in permissions_affectation(affectation)
    ]
    return Centre.objects.filter(pk__in=ids)


def _filtrer_par_centre(queryset, centres):
    """Limite une liste de choix aux objets des centres donnés."""
    modele = queryset.model
    if modele is Centre:
        return queryset.filter(pk__in=centres)
    noms = {champ.name for champ in modele._meta.get_fields()}
    if "centre" in noms:
        return queryset.filter(centre__in=centres)
    if "parcours" in noms:
        return queryset.filter(parcours__centre__in=centres)
    if "affectations" in noms:
        # Comptes (ex. « Agent désigné » d'un service) : seulement ceux
        # affectés à ces centres (relecture du 23/11/2026).
        return queryset.filter(affectations__centre__in=centres, affectations__actif=True).distinct()
    return queryset


def pastille(texte, genre="neutre"):
    """État lisible dans les listes : « En service » en vert, « Hors service » en rouge…"""
    return format_html('<span class="pastille-etat {}">{}</span>', genre, texte)


def etat_actif(actif, oui="En service", non="Hors service"):
    return pastille(oui, "ok") if actif else pastille(non, "arret")


def oui_non(valeur):
    return pastille("Oui", "ok") if valeur else pastille("Non", "neutre")


# Titres lisibles des filtres de droite (au lieu de « Par par defaut », « Par est accueil »).
TITRES_FILTRES = {
    "actif": "État", "suspendu": "Suspendu", "est_accueil": "Accueil", "est_pharmacie": "Pharmacie",
    "par_defaut": "File principale", "ordre_strict": "Ordre strict", "visible_borne": "Proposé à la borne",
    "soumis_quota": "Soumis à quota", "fuseau_horaire": "Fuseau horaire", "is_active": "Compte actif",
    "is_staff": "Accès à l'administration", "is_superuser": "Tous les droits", "type_compte": "Type de compte",
    "role": "Niveau", "jour_semaine": "Jour", "periode": "Période", "statut": "Statut", "derogation": "Dérogation",
    "mode": "Mode", "action": "Action", "jour": "Jour",
}


def _filtre_titre(titre):
    def fabrique(champ, request, params, modele, model_admin, field_path):
        filtre = admin.FieldListFilter.create(champ, request, params, modele, model_admin, field_path)
        filtre.title = titre
        return filtre
    return fabrique


class ListeLisibleMixin:
    """
    Listes de l'administration plus lisibles (24/11/2026) : titre simple
    (« Imprimantes de tickets ») au lieu de « Sélectionnez l'objet … à
    changer », et une phrase d'aide en tête de liste (`aide_liste`).
    """

    aide_liste = ""
    change_list_template = "admin/change_list_lisible.html"

    def get_list_filter(self, request):
        return [
            (filtre, _filtre_titre(TITRES_FILTRES[filtre])) if isinstance(filtre, str) and filtre in TITRES_FILTRES
            else filtre
            for filtre in super().get_list_filter(request)
        ]

    def changelist_view(self, request, extra_context=None):
        extra_context = {
            "title": self.model._meta.verbose_name_plural.capitalize(),
            "aide_liste": self.aide_liste,
            **(extra_context or {}),
        }
        return super().changelist_view(request, extra_context)


class CentreAdminMixin(ListeLisibleMixin):
    """
    Isolation par centre dans l'administration Django : un compte qui n'est
    pas administrateur central ne voit et ne modifie que les données des
    centres qu'il administre. Toutes les listes de choix (centre, service,
    imprimante, file…) sont limitées à ces centres.
    """

    chemin_centre = "centre"

    def centres_administres(self, request):
        if not hasattr(request, "_centres_administres"):
            request._centres_administres = {}
        cle = self.model._meta.label
        if cle not in request._centres_administres:
            request._centres_administres[cle] = centres_administres(request.user, self.model)
        return request._centres_administres[cle]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if est_administrateur_central(request.user):
            return queryset
        return queryset.filter(**{f"{self.chemin_centre}__in": self.centres_administres(request)})

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        champ = super().formfield_for_foreignkey(db_field, request, **kwargs)
        if champ is not None and not est_administrateur_central(request.user):
            champ.queryset = _filtrer_par_centre(champ.queryset, self.centres_administres(request))
        return champ

    def get_list_filter(self, request):
        """
        Filtres latéraux sur une relation (centre, parcours…) : seulement les
        valeurs présentes dans les données visibles, jamais la liste de tous
        les centres ou parcours de la plateforme (relecture du 23/11/2026).
        """
        filtres = super().get_list_filter(request)
        if est_administrateur_central(request.user):
            return filtres
        resultat = []
        for filtre in filtres:
            if isinstance(filtre, str):
                try:
                    champ = get_fields_from_path(self.model, filtre)[-1]
                except FieldDoesNotExist:
                    champ = None
                if champ is not None and champ.is_relation:
                    filtre = (filtre, admin.RelatedOnlyFieldListFilter)
            resultat.append(filtre)
        return resultat

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        champ = super().formfield_for_manytomany(db_field, request, **kwargs)
        if champ is not None and not est_administrateur_central(request.user):
            champ.queryset = _filtrer_par_centre(champ.queryset, self.centres_administres(request))
        return champ
