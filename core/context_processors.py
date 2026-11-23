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
    from circuit.models import Notification
    from circuit.visibilite import services_operationnels

    contexte.update(
        {
            "peut_statistiques": a_droit(utilisateur, "circuit.voir_statistiques", centre),
            "voit_tickets": bool(services_operationnels(utilisateur, centre)),
            # Onglet « Borne » : bornes du centre ACTIF administrées (les droits
            # d'administration sont l'union de toutes les affectations).
            "administre_bornes": utilisateur.is_staff and _administre_bornes(utilisateur, centre),
            "nb_notifications": Notification.objects.filter(
                centre=centre, destinataire=utilisateur, date_lecture__isnull=True
            ).count(),
        }
    )
    return contexte


def _administre_bornes(utilisateur, centre):
    from centres.models import Borne
    from core.admin import centres_administres

    return centres_administres(utilisateur, Borne).filter(pk=centre.pk).exists()
