def navigation(request):
    """Éléments communs aux pages agents (centre actif, menus, notifications)."""
    utilisateur = getattr(request, "user", None)

    if utilisateur is None or not utilisateur.is_authenticated:
        return {}

    centre = getattr(request, "centre_actif", None)
    contexte = {"centre_actif": centre}

    if centre is None:
        return contexte

    from comptes.permissions import a_droit
    from circuit.services import nombre_notifications
    from circuit.visibilite import services_operationnels

    contexte.update(
        {
            "peut_statistiques": a_droit(utilisateur, "circuit.voir_statistiques", centre),
            "voit_tickets": bool(services_operationnels(utilisateur, centre)),
            # Onglet « Borne » : bornes du centre ACTIF administrées (les droits
            # d'administration sont l'union de toutes les affectations).
            "administre_bornes": utilisateur.is_staff and _administre_bornes(utilisateur, centre),
            "nb_notifications": nombre_notifications(utilisateur, centre),
            # Onglet « Demandes » : agents qui orientent les demandes « Autre » (24/11/2026).
            "peut_orienter": _peut_orienter(utilisateur, centre),
        }
    )
    return contexte


def _administre_bornes(utilisateur, centre):
    from centres.models import Borne
    from core.admin import centres_administres

    return centres_administres(utilisateur, Borne).filter(pk=centre.pk).exists()


def _peut_orienter(utilisateur, centre):
    from centres.models import Service
    from comptes.permissions import a_droit

    accueil = Service.objects.filter(centre=centre, est_accueil=True).first()
    return a_droit(utilisateur, "circuit.orienter_demande", centre, accueil)
