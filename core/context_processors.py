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
    from core.memo import memo

    # Les mêmes droits sont vérifiés plusieurs fois : calculés une seule fois ici.
    with memo():
        contexte.update(_menus(utilisateur, centre, a_droit, nombre_notifications, services_operationnels))
    return contexte


def _menus(utilisateur, centre, a_droit, nombre_notifications, services_operationnels):
    return (
        {
            "peut_statistiques": a_droit(utilisateur, "circuit.voir_statistiques", centre),
            "voit_tickets": bool(services_operationnels(utilisateur, centre)),
            # Onglet « Borne » : bornes du centre ACTIF administrées (les droits
            # d'administration sont l'union de toutes les affectations).
            "administre_bornes": _administre_bornes(utilisateur, centre),
            "nb_notifications": nombre_notifications(utilisateur, centre),
            # Onglet « Demandes » : agents qui orientent les demandes « Autre » (24/11/2026).
            "peut_orienter": _peut_orienter(utilisateur, centre),
            # Infirmerie en relais de l'accueil (24/11/2026) : bandeau d'information.
            "relais_accueil_actif": _relais_accueil(utilisateur, centre),
            "est_admin_plateforme": _est_admin_plateforme(utilisateur),
        }
    )


def _relais_accueil(utilisateur, centre):
    from comptes.permissions import affectation_active
    from comptes.presence import relais_accueil

    return relais_accueil(affectation_active(utilisateur, centre), centre)


def _administre_bornes(utilisateur, centre):
    from centres.models import Borne
    from core.admin import centres_administres

    return centres_administres(utilisateur, Borne).filter(pk=centre.pk).exists()


def _peut_orienter(utilisateur, centre):
    from centres.models import Service
    from comptes.permissions import a_droit

    accueil = Service.objects.filter(centre=centre, est_accueil=True).first()
    return a_droit(utilisateur, "circuit.orienter_demande", centre, accueil)


def _est_admin_plateforme(utilisateur):
    from centres.access import est_administrateur_central

    return est_administrateur_central(utilisateur)
