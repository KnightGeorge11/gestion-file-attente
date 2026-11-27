from django.shortcuts import get_object_or_404


def objet_du_centre(request, modele, **filtres):
    """
    Récupère un objet métier uniquement dans le centre actif.
    Un identifiant appartenant à un autre centre donne une erreur 404.
    """
    return get_object_or_404(
        modele.objects.pour_utilisateur(request.user, request.centre_actif),
        **filtres,
    )
