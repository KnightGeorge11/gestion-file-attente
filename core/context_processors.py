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
    from circuit.visibilite import patients_pour_le_chef, services_operationnels

    contexte.update(
        {
            "peut_statistiques": a_droit(utilisateur, "circuit.voir_statistiques", centre),
            "voit_tickets": bool(services_operationnels(utilisateur, centre)),
            # + patients envoyés au médecin en chef, pas encore reçus (décision du 22/11/2026).
            "nb_notifications": Notification.objects.filter(
                centre=centre, destinataire=utilisateur, date_lecture__isnull=True
            ).count() + patients_pour_le_chef(utilisateur, centre).count(),
        }
    )
    return contexte
