from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


@login_required
def accueil(request):
    """Page d'arrivée après connexion : choix du centre puis des services."""
    if getattr(request, "centre_actif", None) is None:
        return redirect("centres:selection")
    return redirect("circuit:guichets")
