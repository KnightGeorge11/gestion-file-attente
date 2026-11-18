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

    contexte.update(
        {
            "peut_statistiques": a_droit(utilisateur, "circuit.voir_statistiques", centre),
            "peut_fusionner": a_droit(utilisateur, "patients.add_fusionpatient", centre),
            "peut_voir_patients": a_droit(utilisateur, "patients.view_patient", centre),
            "nb_notifications": Notification.objects.filter(
                centre=centre, destinataire=utilisateur, date_lecture__isnull=True
            ).count(),
        }
    )
    return contexte
