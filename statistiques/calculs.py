"""
Indicateurs calculés à partir des horodatages (jamais stockés séparément).
Aucune donnée nominative de patient n'apparaît dans les statistiques.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Max, Min, Q
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay
from django.utils import timezone

from circuit.models import DemandeAutre, EntreeFile, Notification, Passage, ReservationQuota, Ticket, Transfert

ATTENTE = ExpressionWrapper(F("date_debut") - F("date_arrivee"), output_field=DurationField())
PRISE_EN_CHARGE = ExpressionWrapper(F("date_fin") - F("date_debut"), output_field=DurationField())
# Durée de prise en charge : seulement les passages terminés par l'agent. Un
# passage annulé, ou clôturé automatiquement à la fin de la journée, a une
# date de fin qui ne mesure pas la prise en charge (relecture du 23/11/2026).
PASSAGE_TERMINE = Q(statut=Passage.Statut.TERMINE)
DUREE_TICKET = ExpressionWrapper(F("date_fin") - F("date_emission"), output_field=DurationField())


def minutes(duree):
    if duree is None:
        return None
    return round(duree / timedelta(minutes=1), 1)


def calculer(centres, date_debut, date_fin):
    tickets = Ticket.objects.filter(centre__in=centres, jour__gte=date_debut, jour__lte=date_fin)
    passages = Passage.objects.filter(ticket__in=tickets)

    par_statut = dict(tickets.values_list("statut").annotate(n=Count("id")))
    par_mode = dict(tickets.values_list("mode").annotate(n=Count("id")))

    par_parcours = [
        {
            "parcours": ligne["parcours__libelle_borne"] or "Autre (hors soins)",
            "tickets": ligne["n"],
            "termines": ligne["termines"],
            "duree_totale_moy": minutes(ligne["duree"]),
        }
        for ligne in tickets.values("parcours__libelle_borne")
        .annotate(
            n=Count("id"),
            termines=Count("id", filter=Q(statut=Ticket.Statut.TERMINE)),
            duree=Avg(DUREE_TICKET, filter=Q(statut=Ticket.Statut.TERMINE)),
        )
        .order_by("parcours__libelle_borne")
    ]

    # Absences comptées à part : une jointure sur les entrées de file
    # dupliquerait les passages réintégrés dans les moyennes et les totaux.
    absences = dict(
        EntreeFile.objects.filter(passage__in=passages, statut=EntreeFile.Statut.ABSENT)
        .values_list("passage__nom_service")
        .annotate(n=Count("id"))
    )

    par_service = [
        {
            "service": ligne["nom_service"],
            "passages": ligne["n"],
            "termines": ligne["termines"],
            "absences": absences.get(ligne["nom_service"], 0),
            "transferts_recus": ligne["transferts"],
            "attente_moy": minutes(ligne["attente_moy"]),
            "attente_max": minutes(ligne["attente_max"]),
            "prise_en_charge_moy": minutes(ligne["pec_moy"]),
        }
        for ligne in passages.values("nom_service")
        .annotate(
            n=Count("id"),
            termines=Count("id", filter=Q(statut=Passage.Statut.TERMINE)),
            transferts=Count("id", filter=Q(origine=Passage.Origine.TRANSFERT)),
            attente_moy=Avg(ATTENTE),
            attente_max=Max(ATTENTE),
            pec_moy=Avg(PRISE_EN_CHARGE, filter=PASSAGE_TERMINE),
        )
        .order_by("nom_service")
    ]

    par_agent = [
        {
            "agent": " ".join(filter(None, [ligne["pris_en_charge_par__first_name"],
                                            ligne["pris_en_charge_par__last_name"]]))
            or ligne["pris_en_charge_par__username"],
            "service": ligne["nom_service"],
            "prises_en_charge": ligne["n"],
            "prise_en_charge_moy": minutes(ligne["pec"]),
        }
        for ligne in passages.filter(pris_en_charge_par__isnull=False)
        .values("pris_en_charge_par__username", "pris_en_charge_par__first_name",
                "pris_en_charge_par__last_name", "nom_service")
        .annotate(n=Count("id"), pec=Avg(PRISE_EN_CHARGE, filter=PASSAGE_TERMINE))
        .order_by("nom_service", "-n")
    ]

    transferts = [
        {"service_origine": ligne["passage_origine__nom_service"], "transferts": ligne["n"]}
        # Vers la pharmacie seulement : les envois au médecin en chef sont
        # aussi des transferts, mais pas vers la pharmacie.
        for ligne in Transfert.objects.filter(ticket__in=tickets, service_destination__est_pharmacie=True)
        .values("passage_origine__nom_service")
        .annotate(n=Count("id"))
        .order_by("passage_origine__nom_service")
    ]

    reservations = ReservationQuota.objects.filter(ticket__in=tickets)
    quotas = reservations.aggregate(
        reservees=Count("id"),
        derogations=Count("id", filter=Q(derogation=True)),
        liberees=Count("id", filter=Q(statut=ReservationQuota.Statut.LIBEREE)),
        consommees=Count("id", filter=Q(statut=ReservationQuota.Statut.CONSOMMEE)),
    )

    global_ = tickets.aggregate(
        total=Count("id"),
        # Tickets de soins seulement : un ticket « Autre » se termine dès
        # l'orientation par l'accueil et fausserait la moyenne.
        duree=Avg(DUREE_TICKET, filter=Q(statut=Ticket.Statut.TERMINE) & ~Q(mode=Ticket.Mode.AUTRE)),
    )

    return {
        "total_tickets": global_["total"],
        "duree_totale_moy": minutes(global_["duree"]),
        "par_statut": {libelle: par_statut.get(valeur, 0) for valeur, libelle in Ticket.Statut.choices},
        "par_mode": {libelle: par_mode.get(valeur, 0) for valeur, libelle in Ticket.Mode.choices},
        "par_parcours": par_parcours,
        "par_service": par_service,
        "par_agent": par_agent,
        "transferts": transferts,
        "quotas": quotas,
    }


# ---------------------------------------------------------------------------
# Pages de statistiques du 24/11/2026 : affluence, évolution, demandes,
# quotas par période et tableau du jour en direct.
# ---------------------------------------------------------------------------

JOURS_COURTS = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"]


def _tickets(centres, date_debut, date_fin):
    return Ticket.objects.filter(centre__in=centres, jour__gte=date_debut, jour__lte=date_fin)


def _fuseau(nom):
    try:
        return ZoneInfo(nom)
    except Exception:  # noqa: BLE001 - fuseau invalide : celui du serveur
        return timezone.get_current_timezone()


def affluence(centres, date_debut, date_fin, fuseau):
    """Tickets émis par jour de la semaine et par heure (heure du centre)."""
    tz = _fuseau(fuseau)
    compte = {
        (l["js"], l["h"]): l["n"]
        for l in _tickets(centres, date_debut, date_fin)
        .annotate(js=ExtractIsoWeekDay("date_emission", tzinfo=tz), h=ExtractHour("date_emission", tzinfo=tz))
        .values("js", "h").annotate(n=Count("id"))
    }
    if not compte:
        return None
    heures = range(min(7, min(h for _, h in compte)), max(17, max(h for _, h in compte)) + 1)
    jours = [j for j in range(1, 8) if j <= 6 or any(js == 7 for js, _ in compte)]
    maximum = max(compte.values())
    lignes = []
    for j in jours:
        cellules = []
        for h in heures:
            n = compte.get((j, h), 0)
            # Niveau 0 à 4, proportionnel au créneau le plus chargé.
            cellules.append({"n": n, "heure": h, "niveau": (4 * n + maximum - 1) // maximum if n else 0})
        lignes.append({"jour": JOURS_COURTS[j - 1], "cellules": cellules, "total": sum(c["n"] for c in cellules)})
    pointe = max(compte, key=compte.get)
    return {
        "heures": list(heures),
        "lignes": lignes,
        "pointe": {"jour": JOURS_COURTS[pointe[0] - 1], "heure": pointe[1], "n": compte[pointe]},
    }


def evolution(centres, date_debut, date_fin):
    """Attente moyenne et tickets par jour (courbe), si la période a au moins 2 jours."""
    if date_debut == date_fin:
        return None
    attentes = dict(
        Passage.objects.filter(ticket__in=_tickets(centres, date_debut, date_fin), date_debut__isnull=False)
        .values_list("ticket__jour").annotate(a=Avg(ATTENTE))
    )
    volumes = dict(_tickets(centres, date_debut, date_fin).values_list("jour").annotate(n=Count("id")))
    presents = sorted(set(attentes) | set(volumes))
    if not presents:
        return None
    # Tous les jours entre le premier et le dernier jour avec des tickets :
    # un jour sans activité garde sa place sur l'axe du temps.
    jours = [presents[0] + timedelta(days=i) for i in range((presents[-1] - presents[0]).days + 1)]
    if len(jours) < 2:
        return None
    largeur, hauteur, marge = 600, 150, 10
    points = [{"jour": j, "attente": minutes(attentes.get(j)), "tickets": volumes.get(j, 0)} for j in jours]
    max_attente = max((p["attente"] or 0) for p in points) or 1
    max_tickets = max(p["tickets"] for p in points) or 1
    pas = (largeur - 2 * marge) / (len(points) - 1)
    largeur_barre = max(2, min(18, pas * 0.6))
    coords = []
    for i, p in enumerate(points):
        p["x"] = round(marge + i * pas, 1)
        hauteur_barre = p["tickets"] / max_tickets * (hauteur - 2 * marge) * 0.45
        p["barre"] = {"x": round(p["x"] - largeur_barre / 2, 1), "y": round(hauteur - marge - hauteur_barre, 1),
                      "l": round(largeur_barre, 1), "h": round(hauteur_barre, 1)}
        if p["attente"] is not None:
            p["y"] = round(hauteur - marge - p["attente"] / max_attente * (hauteur - 2 * marge), 1)
            coords.append(f"{p['x']},{p['y']}")
    valeurs = [p["attente"] for p in points if p["attente"] is not None]
    return {
        "points": points,
        "ligne": " ".join(coords),
        "aire": (f"{coords[0].split(',')[0]},{hauteur - marge} " + " ".join(coords)
                 + f" {coords[-1].split(',')[0]},{hauteur - marge}") if coords else "",
        "largeur": largeur,
        "hauteur": hauteur,
        "base": hauteur - marge,
        "max_attente": max_attente,
        "max_tickets": max_tickets,
        "premiere": valeurs[0] if valeurs else None,
        "derniere": valeurs[-1] if valeurs else None,
        "moyenne": round(sum(valeurs) / len(valeurs), 1) if valeurs else None,
    }


def demandes(centres, date_debut, date_fin):
    """
    Demandes « Autre » et envois au médecin en chef : délai de la première
    réponse de chaque destinataire, et notifications restées sans réponse.
    """
    notifications = list(
        Notification.objects.filter(
            centre__in=centres, demande__ticket__jour__gte=date_debut, demande__ticket__jour__lte=date_fin
        ).select_related("destinataire", "demande__ticket").prefetch_related("reponses")
    )
    par_destinataire, delais, sans_reponse, vers_chef = {}, [], 0, 0
    for n in notifications:
        if n.demande.ticket.mode != Ticket.Mode.AUTRE:
            vers_chef += 1
        reponses = sorted(r.date for r in n.reponses.all())
        ligne = par_destinataire.setdefault(
            n.destinataire_id, {"destinataire": str(n.destinataire), "recues": 0, "sans_reponse": 0, "delais": []}
        )
        ligne["recues"] += 1
        if reponses:
            delai = max(reponses[0] - n.date, timedelta())
            ligne["delais"].append(delai)
            delais.append(delai)
        elif n.demande.statut != DemandeAutre.Statut.CLOTUREE:
            # Demande clôturée : plus rien à répondre (même calcul que la page « Direct »).
            ligne["sans_reponse"] += 1
            sans_reponse += 1
    lignes = []
    for ligne in par_destinataire.values():
        d = ligne.pop("delais")
        ligne["delai_moy"] = minutes(sum(d, timedelta()) / len(d)) if d else None
        lignes.append(ligne)
    lignes.sort(key=lambda l: (-l["recues"], l["destinataire"]))
    return {
        "demandes_autre": DemandeAutre.objects.filter(
            ticket__in=_tickets(centres, date_debut, date_fin), ticket__mode=Ticket.Mode.AUTRE
        ).count(),
        "transmises": len(notifications),
        "vers_chef": vers_chef,
        "sans_reponse": sans_reponse,
        "delai_moy": minutes(sum(delais, timedelta()) / len(delais)) if delais else None,
        "max_delai": max((l["delai_moy"] or 0) for l in lignes) if lignes else 0,
        "par_destinataire": lignes,
    }


def quotas_par_periode(centres, date_debut, date_fin):
    """Remplissage du quota dentiste par période (jours où il y a eu au moins une réservation)."""
    from parcours.models import QuotaParcours

    periodes = {}
    for l in (
        ReservationQuota.objects.filter(
            centre__in=centres, jour__gte=date_debut, jour__lte=date_fin,
            statut__in=ReservationQuota.STATUTS_OCCUPANT,
        ).values("jour", "periode", "quota_id", "quota__nombre_places").annotate(n=Count("id"))
    ):
        p = periodes.setdefault(l["periode"], {"creneaux": 0, "atteints": 0, "places": 0, "occupees": 0})
        places = l["quota__nombre_places"] or 0
        p["creneaux"] += 1
        p["places"] += places
        p["occupees"] += l["n"]
        if places and l["n"] >= places:
            p["atteints"] += 1
    return [
        dict(periodes[code], periode=libelle,
             taux=round(100 * periodes[code]["occupees"] / periodes[code]["places"]) if periodes[code]["places"] else None)
        for code, libelle in QuotaParcours.Periode.choices if code in periodes
    ]


def direct(centre):
    """Tableau du jour : l'état des files maintenant (patients en attente, attente la plus longue)."""
    from centres.models import Service
    from core.timeutils import maintenant_centre

    maintenant = timezone.now()
    jour = maintenant_centre(centre).date()
    tickets = Ticket.objects.filter(centre=centre, jour=jour)
    totaux = tickets.aggregate(
        total=Count("id"),
        ouverts=Count("id", filter=Q(statut__in=Ticket.STATUTS_OUVERTS)),
        termines=Count("id", filter=Q(statut=Ticket.Statut.TERMINE)),
    )
    entrees = (
        EntreeFile.objects.filter(centre=centre, passage__ticket__jour=jour,
                                  statut__in=[EntreeFile.Statut.EN_ATTENTE, *EntreeFile.STATUTS_TENUS])
        .values("file__service_id", "statut")
        .annotate(n=Count("id"), plus_ancienne=Min("date_entree"))
    )
    etat = {}
    for l in entrees:
        e = etat.setdefault(l["file__service_id"], {"attente": 0, "en_cours": 0, "plus_longue": None})
        if l["statut"] == EntreeFile.Statut.EN_ATTENTE:
            e["attente"] += l["n"]
            e["plus_longue"] = minutes(maintenant - l["plus_ancienne"])
        else:
            e["en_cours"] += l["n"]
    attentes_du_jour = Passage.objects.filter(ticket__in=tickets, date_debut__isnull=False).aggregate(
        moy=Avg(ATTENTE), pec=Avg(PRISE_EN_CHARGE, filter=PASSAGE_TERMINE)
    )
    services = []
    for service in Service.objects.filter(centre=centre, actif=True).order_by("ordre", "nom"):
        e = etat.get(service.pk, {"attente": 0, "en_cours": 0, "plus_longue": None})
        services.append({"service": service, **e})
    max_attente = max((s["attente"] for s in services), default=0)
    plus_longues = [s["plus_longue"] for s in services if s["plus_longue"] is not None]
    return {
        "jour": jour,
        "tickets": totaux["total"],
        "ouverts": totaux["ouverts"],
        "termines": totaux["termines"],
        "en_attente": sum(s["attente"] for s in services),
        "en_cours": sum(s["en_cours"] for s in services),
        "attente_moy": minutes(attentes_du_jour["moy"]),
        "prise_en_charge_moy": minutes(attentes_du_jour["pec"]),
        "plus_longue": max(plus_longues) if plus_longues else None,
        "services": services,
        "max_attente": max_attente,
        "sans_reponse": Notification.objects.filter(
            centre=centre, demande__ticket__jour=jour, reponses__isnull=True
        ).exclude(demande__statut=DemandeAutre.Statut.CLOTUREE).count(),
    }
