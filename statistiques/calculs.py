"""
Indicateurs calculés à partir des horodatages (jamais stockés séparément).
Aucune donnée nominative de patient n'apparaît dans les statistiques.
"""

from datetime import timedelta

from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Max, Q

from circuit.models import EntreeFile, Passage, ReservationQuota, Ticket, Transfert

ATTENTE = ExpressionWrapper(F("date_debut") - F("date_arrivee"), output_field=DurationField())
PRISE_EN_CHARGE = ExpressionWrapper(F("date_fin") - F("date_debut"), output_field=DurationField())
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
            pec_moy=Avg(PRISE_EN_CHARGE),
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
        .annotate(n=Count("id"), pec=Avg(PRISE_EN_CHARGE))
        .order_by("nom_service", "-n")
    ]

    transferts = [
        {"service_origine": ligne["passage_origine__nom_service"], "transferts": ligne["n"]}
        for ligne in Transfert.objects.filter(ticket__in=tickets)
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
        duree=Avg(DUREE_TICKET, filter=Q(statut=Ticket.Statut.TERMINE)),
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
