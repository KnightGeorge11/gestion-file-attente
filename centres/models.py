
import datetime
import secrets

from django.contrib.auth.hashers import check_password, make_password
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction

from core.models import ModeleCentre


class Centre(models.Model):
    """
    Centre SMIE FUNTOA.
    """

    code = models.CharField(
        max_length=20,
        unique=True,
    )

    nom = models.CharField(
        max_length=150,
    )

    adresse = models.TextField(
        blank=True,
    )

    fuseau_horaire = models.CharField(
        max_length=50,
        default="Indian/Antananarivo",
    )

    # Prochain numéro de matricule patient à attribuer dans ce centre.
    prochain_numero_patient = models.PositiveBigIntegerField(
        default=1,
    )

    # Paramètres de fonctionnement du centre.
    heure_coupure_matin = models.TimeField(
        default=datetime.time(12, 0),
        help_text="Fin du matin et début de l'après-midi (quotas).",
    )

    heure_cloture_tickets = models.TimeField(
        null=True,
        blank=True,
        default=datetime.time(23, 59),
        help_text=(
            "Heure de clôture des tickets restés ouverts et de remise à zéro des numéros "
            "(23 h 59, décision du 21/11/2026)."
        ),
    )

    # Décision du 21/11/2026 : la borne est à l'accueil et c'est l'accueil qui
    # délivre les tickets. Le patient va directement au premier service de
    # son parcours : il n'y a plus de file d'attente à l'accueil pour les
    # soins (les demandes « Autre » y restent orientées).
    accueil_delivre_tickets = models.BooleanField(
        default=True,
        db_default=True,
        verbose_name="L'accueil délivre les tickets",
        help_text=(
            "Coché : la borne est tenue par l'accueil, le patient va directement au service "
            "choisi. Décoché : borne en libre-service, puis passage par la file de l'accueil."
        ),
    )

    max_reintegrations = models.PositiveSmallIntegerField(
        default=1,
        help_text="Nombre maximal de réintégrations après une absence.",
    )

    max_tickets_ouverts_par_patient = models.PositiveSmallIntegerField(
        default=1,
        validators=[MinValueValidator(1)],
    )

    duree_affichage_appel_secondes = models.PositiveSmallIntegerField(
        default=30,
        validators=[MinValueValidator(1)],
        help_text="Durée d'affichage d'un appel sur l'écran principal.",
    )

    actif = models.BooleanField(
        default=True,
    )

    date_creation = models.DateTimeField(
        auto_now_add=True,
    )

    date_modification = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["nom"]
        verbose_name = "Centre"
        verbose_name_plural = "Centres"

    def clean(self):
        # Un fuseau inconnu (« UTC+3 », espace en trop) mettrait toutes les
        # pages du centre en erreur (relecture du 23/11/2026).
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        self.fuseau_horaire = (self.fuseau_horaire or "").strip()
        try:
            ZoneInfo(self.fuseau_horaire)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValidationError({"fuseau_horaire": (
                "Fuseau horaire inconnu. Utilisez un nom comme « Indian/Antananarivo »."
            )})

    def __str__(self):
        return f"{self.code} - {self.nom}"


class Service(models.Model):
    """
    Service appartenant à un centre.
    """

    centre = models.ForeignKey(
        Centre,
        on_delete=models.PROTECT,
        related_name="services",
    )

    code = models.CharField(
        max_length=20,
    )

    nom = models.CharField(
        max_length=100,
    )

    ordre = models.PositiveSmallIntegerField(
        default=0,
    )

    est_accueil = models.BooleanField(
        default=False,
    )

    est_pharmacie = models.BooleanField(
        default=False,
    )

    accepte_transferts = models.BooleanField(
        default=True,
    )

    appel_oral_autorise = models.BooleanField(
        default=False,
    )

    # Médecin en chef (décision du 23/11/2026) : ce n'est plus un service mais
    # une personne (rôle « Médecin en chef »). « est_medecin_chef » n'est plus
    # utilisé (colonne gardée) ; « envoie_medecin_chef » : les agents de ce
    # service (les médecins) peuvent lui envoyer un patient en fin de parcours.
    est_medecin_chef = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name="Médecin en chef",
        help_text="Reçoit les patients envoyés par les services autorisés ci-dessous.",
    )

    envoie_medecin_chef = models.BooleanField(
        default=False,
        db_default=False,
        verbose_name="Peut envoyer au médecin en chef",
        help_text="Les agents de ce service (les médecins) peuvent envoyer un patient au médecin en chef.",
    )

    afficher_ecran_principal = models.BooleanField(
        default=True,
    )

    actif = models.BooleanField(
        default=True,
    )

    # Suspension temporaire : le service reste configuré
    # mais ne reçoit plus de patients.
    suspendu = models.BooleanField(
        default=False,
    )

    motif_suspension = models.CharField(
        max_length=255,
        blank=True,
    )

    # Mode Autre : lorsqu'un service est choisi, la notification est
    # adressée à cet agent désigné, et non à tous les agents du service.
    destinataire_demandes = models.ForeignKey(
        "comptes.Utilisateur",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="services_destinataire_demandes",
        verbose_name="Agent désigné (demandes « Autre »)",
    )

    date_creation = models.DateTimeField(
        auto_now_add=True,
    )

    date_modification = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["centre", "ordre", "nom"]
        verbose_name = "Service"
        verbose_name_plural = "Services"
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "code"],
                name="unique_code_service_par_centre",
            ),
            # Le moteur suppose un seul accueil (premier passage de tout
            # ticket) et une seule pharmacie (transferts) par centre.
            models.UniqueConstraint(
                fields=["centre"],
                condition=models.Q(est_accueil=True),
                name="un_seul_accueil_par_centre",
                violation_error_message="Ce centre a déjà un service d'accueil.",
            ),
            models.UniqueConstraint(
                fields=["centre"],
                condition=models.Q(est_pharmacie=True),
                name="une_seule_pharmacie_par_centre",
                violation_error_message="Ce centre a déjà un service de pharmacie.",
            ),
        ]

    def __str__(self):
        return f"{self.nom} ({self.centre.code})"

    @property
    def disponible(self):
        return self.actif and not self.suspendu

    def clean(self):
        super().clean()

        if self.suspendu and not self.motif_suspension.strip():
            raise ValidationError(
                {"motif_suspension": "Un motif est obligatoire pour suspendre un service."}
            )

        if self.est_accueil and self.est_pharmacie:
            raise ValidationError("Un service ne peut pas être à la fois l'accueil et la pharmacie.")

    def save(self, *args, **kwargs):
        creation = self._state.adding

        with transaction.atomic():
            super().save(*args, **kwargs)

            # Chaque service possède une file par défaut.
            if creation:
                FileAttente.objects.create(
                    centre_id=self.centre_id,
                    service=self,
                    code=self.code,
                    nom=self.nom,
                    par_defaut=True,
                )

    def file_par_defaut(self):
        return self.files.filter(par_defaut=True).first()


class HoraireService(models.Model):
    """Plage d'ouverture d'un service. Plusieurs plages possibles par jour."""

    class JourSemaine(models.IntegerChoices):
        LUNDI = 0, "Lundi"
        MARDI = 1, "Mardi"
        MERCREDI = 2, "Mercredi"
        JEUDI = 3, "Jeudi"
        VENDREDI = 4, "Vendredi"
        SAMEDI = 5, "Samedi"
        DIMANCHE = 6, "Dimanche"

    service = models.ForeignKey(
        Service,
        on_delete=models.CASCADE,
        related_name="horaires",
    )

    jour_semaine = models.PositiveSmallIntegerField(
        choices=JourSemaine.choices,
    )

    heure_debut = models.TimeField()
    heure_fin = models.TimeField()

    class Meta:
        ordering = ["service", "jour_semaine", "heure_debut"]
        verbose_name = "Horaire de service"
        verbose_name_plural = "Horaires de service"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(heure_fin__gt=models.F("heure_debut")),
                name="horaire_fin_apres_debut",
            ),
        ]

    def clean(self):
        super().clean()

        if (
            self.heure_debut
            and self.heure_fin
            and self.heure_fin <= self.heure_debut
        ):
            raise ValidationError(
                "L'heure de fin doit être postérieure à l'heure de début."
            )

        if self.service_id and self.heure_debut and self.heure_fin:
            chevauchement = (
                HoraireService.objects
                .filter(
                    service_id=self.service_id,
                    jour_semaine=self.jour_semaine,
                    heure_debut__lt=self.heure_fin,
                    heure_fin__gt=self.heure_debut,
                )
                .exclude(pk=self.pk)
                .exists()
            )

            if chevauchement:
                raise ValidationError(
                    "Cette plage chevauche une autre plage du même jour."
                )

    def __str__(self):
        return (
            f"{self.service} - {self.get_jour_semaine_display()} "
            f"{self.heure_debut:%H:%M}-{self.heure_fin:%H:%M}"
        )


class FileAttente(ModeleCentre):
    """
    File d'attente d'un service. Une file par défaut est créée
    automatiquement avec chaque service.
    """

    service = models.ForeignKey(
        Service,
        on_delete=models.PROTECT,
        related_name="files",
    )

    code = models.CharField(max_length=20)
    nom = models.CharField(max_length=100)

    par_defaut = models.BooleanField(default=False)

    nb_appels_avant_absence = models.PositiveSmallIntegerField(
        default=3,
        validators=[MinValueValidator(1)],
    )

    delai_entre_appels_secondes = models.PositiveSmallIntegerField(
        default=10,
        help_text="Délai minimal entre deux appels du même patient (10 s, décision du 18/11/2026).",
    )

    # Décision du 18/11/2026 : toujours au moins 2 agents par service ;
    # chaque agent ne tient qu'un patient à la fois.
    nb_appels_simultanes = models.PositiveSmallIntegerField(
        default=2,
        validators=[MinValueValidator(2)],
        help_text="Nombre d'agents pouvant appeler en même temps dans cette file (2 minimum).",
    )

    ordre_strict = models.BooleanField(
        default=True,
        help_text="Si coché, seul le premier patient de la file peut être appelé.",
    )

    actif = models.BooleanField(default=True)

    date_creation = models.DateTimeField(auto_now_add=True)
    date_modification = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["centre", "service__ordre", "nom"]
        verbose_name = "File d'attente"
        verbose_name_plural = "Files d'attente"
        constraints = [
            models.UniqueConstraint(
                fields=["service", "code"],
                name="unique_code_file_par_service",
            ),
            models.UniqueConstraint(
                fields=["service"],
                condition=models.Q(par_defaut=True),
                name="unique_file_par_defaut_par_service",
            ),
        ]

    def clean(self):
        super().clean()

        if (
            self.service_id
            and self.centre_id
            and self.service.centre_id != self.centre_id
        ):
            raise ValidationError(
                "La file doit appartenir au même centre que son service."
            )

    def __str__(self):
        return f"{self.nom} ({self.service})"


PORTS_IMPRIMANTE = range(9100, 9110)   # impression brute (JetDirect) : 9100 à 9109


def _imprimante_simulee(adresse):
    """Imprimante simulée sur ce poste (développement : imprimante_simulee)."""
    import ipaddress

    from django.conf import settings

    try:
        boucle = ipaddress.ip_address(str(adresse)).is_loopback
    except ValueError:
        return False
    return boucle and getattr(settings, "IMPRIMANTE_SIMULEE_AUTORISEE", False)


def adresse_imprimante_autorisee(adresse, port):
    """
    Le serveur ne se connecte qu'à une imprimante du réseau local, sur un port
    d'impression brute (protection contre l'utilisation du serveur pour
    joindre d'autres machines). Seule exception : l'imprimante simulée en
    développement (boucle locale, DEBUG).
    """
    from core.reseau import est_adresse_privee

    if _imprimante_simulee(adresse):
        return True
    return est_adresse_privee(adresse) and port in PORTS_IMPRIMANTE


def valider_adresse_imprimante(valeur):
    """Imprimante du réseau local uniquement : le serveur ne se connecte jamais ailleurs."""
    from core.reseau import est_adresse_privee

    if not (est_adresse_privee(valeur) or _imprimante_simulee(valeur)):
        raise ValidationError(
            "Adresse du réseau local attendue (ex. 192.168.1.50) : "
            "ni adresse Internet, ni boucle locale."
        )


class ImprimanteTicket(ModeleCentre):
    """
    Imprimante thermique réseau (Ethernet ou Wi-Fi) d'une borne.
    Le serveur lui envoie directement le ticket (ESC/POS, port 9100) :
    aucune boîte d'impression sur la tablette.
    """

    class PageCodes(models.TextChoices):
        CP858 = "cp858", "PC858 (Europe de l'Ouest, avec €)"
        CP850 = "cp850", "PC850 (Europe de l'Ouest)"
        ASCII = "ascii", "Sans accents (compatibilité maximale)"

    libelle = models.CharField(max_length=100)
    adresse_ip = models.GenericIPAddressField(
        validators=[valider_adresse_imprimante],
        help_text="Adresse IP fixe de l'imprimante sur le réseau local.",
    )
    port = models.PositiveIntegerField(
        default=9100,
        validators=[MinValueValidator(PORTS_IMPRIMANTE[0]), MaxValueValidator(PORTS_IMPRIMANTE[-1])],
        help_text="Port d'impression brute : 9100 (9100 à 9109).",
    )
    largeur_caracteres = models.PositiveSmallIntegerField(
        default=48,
        validators=[MinValueValidator(16)],
        help_text="Caractères par ligne : 48 pour du papier 80 mm, 32 pour du 58 mm.",
    )
    page_codes = models.CharField(max_length=10, choices=PageCodes.choices, default=PageCodes.CP858)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["centre", "libelle"]
        verbose_name = "Imprimante de tickets"
        verbose_name_plural = "Imprimantes de tickets"

    def __str__(self):
        return f"{self.libelle} ({self.adresse_ip}:{self.port})"


class Borne(ModeleCentre):
    """
    Poste de libre-service émettant les tickets.
    Identifié par un code et une clé secrète ; aucun compte utilisateur.
    """

    code = models.CharField(max_length=30)
    libelle = models.CharField(max_length=100)

    # Plus utilisée pour les bornes (décision du 21/11/2026 : activation par
    # un administrateur, sans clé). Conservée en base pour ne pas casser la
    # copie du projet qui partage la même base.
    cle_hash = models.CharField(
        max_length=128,
        editable=False,
        default="!",
    )

    imprimante = models.ForeignKey(
        ImprimanteTicket,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="bornes",
        help_text="Imprimante réseau de cette borne. Vide : impression par le navigateur.",
    )

    # Services proposés par cette borne (décision du 21/11/2026), choisis par
    # l'administrateur du centre depuis la page « Borne » ou l'administration
    # (décision du 24/11/2026). Aucun coché : tous les services visibles du centre.
    parcours = models.ManyToManyField(
        "parcours.Parcours",
        blank=True,
        related_name="bornes",
        verbose_name="Services proposés",
        help_text="Services affichés sur cette borne. Aucun : tous les services visibles du centre.",
    )

    actif = models.BooleanField(default=True)
    derniere_activite = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["centre", "code"]
        verbose_name = "Borne"
        verbose_name_plural = "Bornes"
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "code"],
                name="unique_code_borne_par_centre",
            ),
        ]

    def generer_cle(self):
        """
        Génère une nouvelle clé, en conserve uniquement l'empreinte
        et retourne la clé en clair (à afficher une seule fois).
        """
        cle = secrets.token_urlsafe(32)
        self.cle_hash = make_password(cle)
        return cle

    def verifier_cle(self, cle):
        if not cle or not self.cle_hash:
            return False

        return check_password(cle, self.cle_hash)

    def clean(self):
        super().clean()
        if self.imprimante_id and self.centre_id and self.imprimante.centre_id != self.centre_id:
            raise ValidationError({"imprimante": "L'imprimante doit appartenir au même centre que la borne."})

    def parcours_possibles(self):
        """Services que cette borne peut proposer (ceux du centre, actifs et visibles à la borne)."""
        from parcours.models import Parcours

        tous = Parcours.objects.filter(centre_id=self.centre_id, actif=True, visible_borne=True)
        if not self.centre.accueil_delivre_tickets:
            # Borne en libre-service : jamais le médecin en chef (décision du 22/11/2026).
            tous = tous.filter(reserve_accueil=False)
        return tous

    def parcours_proposes(self):
        """Parcours affichés sur cette borne (tous ceux du centre si aucun n'est choisi)."""
        tous = self.parcours_possibles()
        if self.pk and self.parcours.exists():
            return tous.filter(pk__in=self.parcours.values("pk"))
        return tous

    def __str__(self):
        return f"{self.libelle} ({self.centre.code})"


class EcranAffichage(ModeleCentre):
    """
    Écran principal (Smart TV) affichant les appels du centre.
    Identifié par un code et une clé secrète ; aucun compte utilisateur.
    """

    code = models.CharField(max_length=30)
    libelle = models.CharField(max_length=100)
    # Ancien mode à clé ; « ! » : réglage d'affichage créé depuis la page Écran, sans clé.
    cle_hash = models.CharField(max_length=128, editable=False, default="!")
    # Ce que l'écran affiche (décision du 24/11/2026) : l'administrateur choisit
    # les services dont les appels s'affichent sur chaque écran.
    services = models.ManyToManyField(
        "centres.Service",
        blank=True,
        related_name="ecrans_affichage",
        verbose_name="Services affichés",
        help_text="Appels affichés sur cet écran. Aucun : tous les services affichés à l'écran principal.",
    )
    nb_appels_affiches = models.PositiveSmallIntegerField(
        default=8,
        validators=[MinValueValidator(1)],
        help_text="Nombre maximal d'appels affichés simultanément.",
    )
    annonce_vocale = models.BooleanField(
        default=True,
        help_text="Annoncer les appels par synthèse vocale (si disponible sur l'appareil).",
    )
    actif = models.BooleanField(default=True)
    derniere_activite = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["centre", "code"]
        verbose_name = "Écran d'affichage"
        verbose_name_plural = "Écrans d'affichage"
        constraints = [
            models.UniqueConstraint(
                fields=["centre", "code"],
                name="unique_code_ecran_par_centre",
            ),
        ]

    generer_cle = Borne.generer_cle
    verifier_cle = Borne.verifier_cle

    def __str__(self):
        return f"{self.libelle} ({self.centre.code})"


class EcranConnecte(ModeleCentre):
    """
    Appareil du réseau local qui affiche l'écran des appels (TV, mini-PC,
    tablette…), repéré automatiquement par son adresse IP quand il interroge
    le serveur (décision du 21/11/2026). Sert à voir quels écrans sont en
    ligne ; aucune information personnelle.
    """

    adresse_ip = models.GenericIPAddressField()
    navigateur = models.CharField(max_length=255, blank=True)
    nom = models.CharField(max_length=100, blank=True, help_text="Nom donné par l'administrateur (ex. TV salle d'attente).")
    premiere_activite = models.DateTimeField()
    derniere_activite = models.DateTimeField()

    class Meta:
        ordering = ["centre", "-derniere_activite"]
        verbose_name = "Écran connecté"
        verbose_name_plural = "Écrans connectés"
        constraints = [
            models.UniqueConstraint(fields=["centre", "adresse_ip"], name="unique_ecran_connecte_par_centre"),
        ]

    def __str__(self):
        return self.nom or f"Écran {self.adresse_ip}"

    @property
    def en_ligne(self):
        from django.utils import timezone

        return timezone.now() - self.derniere_activite < datetime.timedelta(minutes=2)


class AppareilEcran(models.Model):
    """
    Écran Wi-Fi (TV connectée, boîtier, mini-PC) qui a ouvert une fois
    l'adresse courte …/tv/ (décision du 21/11/2026). Il attend alors, avec un
    code à l'écran, qu'on le choisisse dans la page Écran de la plateforme ;
    il affiche ensuite les appels du centre choisi, jusqu'à ce qu'on l'arrête.
    Reconnu par un jeton gardé sur l'appareil (cookie), sans compte ni clé.
    """

    jeton = models.CharField(max_length=64, unique=True)
    code = models.CharField(max_length=4)
    nom = models.CharField(max_length=100, blank=True, help_text="Nom donné à l'écran (ex. TV salle d'attente).")
    adresse_ip = models.GenericIPAddressField(null=True, blank=True)
    navigateur = models.CharField(max_length=255, blank=True)
    centre = models.ForeignKey(
        Centre, on_delete=models.SET_NULL, null=True, blank=True, related_name="appareils_ecran",
        help_text="Centre dont l'écran affiche les appels. Vide : écran libre, en attente.",
    )
    # Réglage d'affichage choisi par l'administrateur (services, nombre d'appels, voix).
    affichage = models.ForeignKey(
        EcranAffichage, on_delete=models.SET_NULL, null=True, blank=True, related_name="appareils",
        help_text="Ce que cet écran affiche. Vide : tous les appels du centre.",
    )
    date_creation = models.DateTimeField(auto_now_add=True)
    derniere_activite = models.DateTimeField()

    class Meta:
        ordering = ["-derniere_activite"]
        verbose_name = "Écran Wi-Fi"
        verbose_name_plural = "Écrans Wi-Fi"

    def __str__(self):
        return self.nom or f"Écran {self.code}"

    def clean(self):
        super().clean()
        # Affichage d'un autre centre : il serait ignoré sans message (audit du 24/11/2026).
        if self.affichage_id and self.affichage.centre_id != self.centre_id:
            raise ValidationError({"affichage": "Choisissez un affichage du centre de cet écran."})

    @property
    def en_ligne(self):
        from django.utils import timezone

        return timezone.now() - self.derniere_activite < datetime.timedelta(seconds=90)

