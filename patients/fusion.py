"""
Fusion manuelle et réversible de fiches patient (dossier partagé).

Principe retenu pour le dossier partagé entre centres :
- la fiche « source » est marquée comme fusionnée dans la fiche « cible »
  (champ fusionne_dans) ; elle disparaît des recherches et des
  identifications, qui sont redirigées vers la cible ;
- aucun ticket, passage, rattachement ni historique n'est réattribué :
  rien n'est perdu et l'annulation remet simplement la fiche en service ;
- chaque fusion et annulation est historisée (FusionPatient + audit).
"""

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from audit.journal import journaliser
from audit.models import JournalAudit

from .models import FusionPatient, Patient, SignalementDoublon


def _instantane(patient):
    return {
        "nom": patient.nom,
        "prenom": patient.prenom,
        "date_naissance": patient.date_naissance.isoformat() if patient.date_naissance else None,
        "telephone": patient.telephone,
        "rattachements": list(patient.rattachements.values_list("centre__code", "matricule")),
        "tickets": patient.tickets.count(),
    }


@transaction.atomic
def fusionner(source, cible, utilisateur, motif, centre=None):
    motif = (motif or "").strip()
    if not motif:
        raise ValidationError("Le motif de fusion est obligatoire.")
    source = Patient.objects.select_for_update().get(pk=source.pk)
    cible = Patient.objects.select_for_update().get(pk=cible.pk)
    if source.pk == cible.pk:
        raise ValidationError("Une fiche ne peut pas être fusionnée avec elle-même.")
    if source.fusionne_dans_id or cible.fusionne_dans_id:
        raise ValidationError("Une des fiches est déjà fusionnée.")

    fusion = FusionPatient.objects.create(
        patient_source=source,
        patient_cible=cible,
        effectuee_par=utilisateur,
        motif=motif,
        donnees_avant_fusion={"source": _instantane(source), "cible": _instantane(cible)},
    )
    source.fusionne_dans = cible
    source.save(update_fields=["fusionne_dans", "date_modification"])

    SignalementDoublon.objects.filter(
        patient_1_id=min(source.pk, cible.pk), patient_2_id=max(source.pk, cible.pk)
    ).update(statut=SignalementDoublon.Statut.CONFIRME, examine_par=utilisateur, date_examen=timezone.now())

    journaliser(JournalAudit.Action.FUSION, utilisateur, objet=fusion, centre=centre,
                details={"source": source.pk, "cible": cible.pk, "motif": motif})
    return fusion


@transaction.atomic
def annuler_fusion(fusion, utilisateur, centre=None):
    fusion = FusionPatient.objects.select_for_update().get(pk=fusion.pk)
    if fusion.statut != FusionPatient.Statut.ACTIVE:
        raise ValidationError("Cette fusion est déjà annulée.")
    source = Patient.objects.select_for_update().get(pk=fusion.patient_source_id)
    source.fusionne_dans = None
    source.save(update_fields=["fusionne_dans", "date_modification"])
    fusion.statut = FusionPatient.Statut.ANNULEE
    fusion.annulee_par = utilisateur
    fusion.date_annulation = timezone.now()
    fusion.save(update_fields=["statut", "annulee_par", "date_annulation"])
    journaliser(JournalAudit.Action.ANNULATION_FUSION, utilisateur, objet=fusion, centre=centre)
    return fusion


@transaction.atomic
def marquer_non_doublon(signalement, utilisateur):
    signalement.statut = SignalementDoublon.Statut.NON_DOUBLON
    signalement.examine_par = utilisateur
    signalement.date_examen = timezone.now()
    signalement.save(update_fields=["statut", "examine_par", "date_examen"])
    return signalement
