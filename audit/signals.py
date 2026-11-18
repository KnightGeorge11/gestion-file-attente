"""
Enregistrement automatique des opérations sensibles dans le journal d'audit.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import (
    user_logged_in,
    user_logged_out,
    user_login_failed,
)
from django.db.models.signals import m2m_changed, post_save, pre_save
from django.dispatch import receiver

from comptes.models import AffectationService, AffectationUtilisateur
from parcours.models import ModificationQuota, ParcoursVersion

from .journal import journaliser
from .models import JournalAudit
from .threadlocal import utilisateur_courant

Utilisateur = get_user_model()


@receiver(user_logged_in)
def connexion(sender, request, user, **kwargs):
    journaliser(JournalAudit.Action.CONNEXION, user, objet=user, request=request)


@receiver(user_logged_out)
def deconnexion(sender, request, user, **kwargs):
    if user is not None:
        journaliser(JournalAudit.Action.DECONNEXION, user, objet=user, request=request)


@receiver(user_login_failed)
def echec_connexion(sender, credentials, request=None, **kwargs):
    journaliser(
        JournalAudit.Action.ECHEC_CONNEXION,
        identifiant_saisi=str(credentials.get("username", "")),
        request=request,
    )


@receiver(pre_save, sender=Utilisateur)
def memoriser_etat_utilisateur(sender, instance, **kwargs):
    instance._etat_avant = None

    if instance.pk:
        instance._etat_avant = (
            Utilisateur.objects.filter(pk=instance.pk)
            .values("is_active", "is_superuser", "is_staff", "type_compte")
            .first()
        )


@receiver(post_save, sender=Utilisateur)
def audit_utilisateur(sender, instance, created, **kwargs):
    auteur = utilisateur_courant()

    if created:
        journaliser(JournalAudit.Action.CREATION, auteur, objet=instance)
        return

    avant = getattr(instance, "_etat_avant", None)

    if not avant:
        return

    if avant["is_active"] and not instance.is_active:
        journaliser(JournalAudit.Action.DESACTIVATION, auteur, objet=instance)
    elif not avant["is_active"] and instance.is_active:
        journaliser(JournalAudit.Action.REACTIVATION, auteur, objet=instance)

    changements = {
        champ: [avant[champ], getattr(instance, champ)]
        for champ in ("is_superuser", "is_staff", "type_compte")
        if avant[champ] != getattr(instance, champ)
    }

    if changements:
        journaliser(JournalAudit.Action.ROLE, auteur, objet=instance, details=changements)


@receiver(pre_save, sender=AffectationUtilisateur)
def memoriser_affectation(sender, instance, **kwargs):
    instance._etat_avant = None

    if instance.pk:
        instance._etat_avant = (
            AffectationUtilisateur.objects.filter(pk=instance.pk)
            .values("groupe_id", "role", "actif")
            .first()
        )


@receiver(post_save, sender=AffectationUtilisateur)
def audit_affectation(sender, instance, created, **kwargs):
    apres = {
        "utilisateur": instance.utilisateur_id,
        "groupe_id": instance.groupe_id,
        "role": instance.role,
        "actif": instance.actif,
    }
    journaliser(
        JournalAudit.Action.CREATION if created else JournalAudit.Action.ROLE,
        utilisateur_courant(),
        objet=instance,
        details={"avant": getattr(instance, "_etat_avant", None), "apres": apres},
    )


@receiver(m2m_changed, sender=AffectationUtilisateur.permissions.through)
def audit_permissions_affectation(sender, instance, action, pk_set, **kwargs):
    if action in ("post_add", "post_remove", "post_clear"):
        journaliser(
            JournalAudit.Action.ROLE,
            utilisateur_courant(),
            objet=instance,
            details={"operation": action, "permissions": sorted(pk_set or [])},
        )


@receiver(m2m_changed, sender=Utilisateur.groups.through)
def audit_groupes_utilisateur(sender, instance, action, pk_set, **kwargs):
    if action in ("post_add", "post_remove", "post_clear"):
        journaliser(
            JournalAudit.Action.ROLE,
            utilisateur_courant(),
            objet=instance,
            details={"operation": action, "groupes": sorted(pk_set or [])},
        )


@receiver(post_save, sender=AffectationService)
def audit_affectation_service(sender, instance, created, **kwargs):
    journaliser(
        JournalAudit.Action.ROLE,
        utilisateur_courant(),
        objet=instance.affectation,
        details={"service_ajoute": instance.service_id},
    )


@receiver(post_save, sender=ModificationQuota)
def audit_quota(sender, instance, created, **kwargs):
    if created:
        journaliser(
            JournalAudit.Action.QUOTA,
            instance.auteur,
            objet=instance.parcours,
            details={
                "operation": instance.action,
                "avant": instance.valeurs_avant,
                "apres": instance.valeurs_apres,
            },
        )


@receiver(pre_save, sender=ParcoursVersion)
def memoriser_version(sender, instance, **kwargs):
    instance._statut_avant = None
    if instance.pk:
        instance._statut_avant = (
            ParcoursVersion.objects.filter(pk=instance.pk)
            .values_list("statut", flat=True)
            .first()
        )


@receiver(post_save, sender=ParcoursVersion)
def audit_version(sender, instance, created, **kwargs):
    if (
        instance.statut == ParcoursVersion.Statut.PUBLIEE
        and getattr(instance, "_statut_avant", None) != ParcoursVersion.Statut.PUBLIEE
    ):
        journaliser(
            JournalAudit.Action.PUBLICATION,
            instance.publiee_par or utilisateur_courant(),
            objet=instance.parcours,
            details={"version": instance.numero},
        )
