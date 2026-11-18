from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from .access import (
    SESSION_CENTRE_KEY,
    utilisateur_peut_acceder_centre,
)


def centre_actif_requis(view_func):
    """
    Protège une vue métier :
    - utilisateur obligatoirement connecté ;
    - centre actif obligatoirement sélectionné ;
    - accès au centre obligatoirement autorisé.
    """

    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        centre = getattr(request, "centre_actif", None)

        if centre is None:
            messages.warning(
                request,
                "Veuillez sélectionner un centre avant de continuer.",
            )
            return redirect("centres:selection")

        if not utilisateur_peut_acceder_centre(
            request.user,
            centre,
        ):
            request.session.pop(SESSION_CENTRE_KEY, None)

            messages.error(
                request,
                "Vous n'êtes pas autorisé à accéder à ce centre.",
            )
            return redirect("centres:selection")

        return view_func(request, *args, **kwargs)

    return login_required(wrapper)