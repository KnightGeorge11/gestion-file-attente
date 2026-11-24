
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from core.requetes import entier

from .access import (
    SESSION_CENTRE_KEY,
    centres_autorises,
)


@login_required
def selection_centre(request):
    centres = centres_autorises(request.user)

    if request.method == "POST":
        centre_id = request.POST.get("centre_id")

        centre = centres.filter(id=entier(centre_id)).first()

        if centre is None:
            messages.error(
                request,
                "Vous n'êtes pas autorisé à accéder à ce centre.",
            )
        else:
            request.session[SESSION_CENTRE_KEY] = centre.id
            messages.success(
                request,
                f"Centre actif : {centre.nom}",
            )
            return redirect("accueil")

    centre_actif = None
    centre_id = request.session.get(SESSION_CENTRE_KEY)

    if centre_id:
        centre_actif = centres.filter(id=centre_id).first()

    return render(
        request,
        "centres/selection_centre.html",
        {
            "centres": centres,
            "centre_actif": centre_actif,
        },
    )
