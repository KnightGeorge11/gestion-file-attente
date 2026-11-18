from django.utils import timezone

from core.timeutils import fuseau_horaire_centre

from .access import (
    SESSION_CENTRE_KEY,
    centres_autorises,
    est_administrateur_central,
)


class CentreActifMiddleware:
    """
    Identifie le centre actif de l'utilisateur à chaque requête.
    Vérifie que l'utilisateur est autorisé à y accéder.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.centre_actif = None

        user = getattr(request, "user", None)

        if user is not None and user.is_authenticated:
            centre_id = request.session.get(SESSION_CENTRE_KEY)
            selection_invalide = False

            # Vérifier le centre enregistré dans la session.
            if centre_id:
                centre = centres_autorises(user).filter(
                    id=centre_id
                ).first()

                if centre:
                    request.centre_actif = centre
                else:
                    # Le centre n'est pas autorisé.
                    request.session.pop(SESSION_CENTRE_KEY, None)
                    selection_invalide = True

            # Sélection automatique uniquement si aucune sélection
            # invalide n'a été détectée pendant cette requête.
            if (
                request.centre_actif is None
                and not selection_invalide
                and not est_administrateur_central(user)
            ):
                centres = list(
                    centres_autorises(user)[:2]
                )

                if len(centres) == 1:
                    request.centre_actif = centres[0]
                    request.session[SESSION_CENTRE_KEY] = centres[0].id

        # Les dates affichées suivent le fuseau horaire du centre actif.
        if request.centre_actif is not None:
            try:
                timezone.activate(fuseau_horaire_centre(request.centre_actif))
            except ValueError:
                timezone.deactivate()
        else:
            timezone.deactivate()

        return self.get_response(request)
