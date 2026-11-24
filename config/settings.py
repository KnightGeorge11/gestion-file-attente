import os
import sys
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


# Suite de tests (« manage.py test ») : seulement si « test » est la commande,
# pas un argument (ex. « changepassword test » en production, relecture du 23/11/2026).
EN_TEST = len(sys.argv) > 1 and sys.argv[1] == "test"


def env_bool(nom, defaut="False"):
    return os.getenv(nom, defaut).strip().lower() in ("1", "true", "oui", "yes")


# Production par défaut : DEBUG doit être activé explicitement (.env de développement).
DEBUG = env_bool("DJANGO_DEBUG", "False")

SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG and not EN_TEST:
        raise ImproperlyConfigured("DJANGO_SECRET_KEY est obligatoire en production (DEBUG désactivé).")
    SECRET_KEY = "django-insecure-local-development-only"
if not DEBUG and not EN_TEST and (
    SECRET_KEY.startswith("django-insecure") or len(SECRET_KEY) < 50
):
    raise ImproperlyConfigured("DJANGO_SECRET_KEY doit être une clé aléatoire d'au moins 50 caractères.")

ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]


def _adresses_de_cet_ordinateur():
    """Adresses IP de l'ordinateur sur le réseau local (Wi-Fi, câble)."""
    import socket

    adresses = set()
    try:
        adresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    try:
        # Adresse utilisée pour sortir sur le réseau (aucun paquet n'est envoyé).
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as prise:
            prise.connect(("192.168.255.255", 1))
            adresses.add(prise.getsockname()[0])
    except OSError:
        pass
    return sorted(a for a in adresses if not a.startswith(("127.", "169.254.")))


# Développement (DEBUG) : l'écran de la salle, la borne ou une TV du même Wi-Fi
# joignent le serveur par l'adresse réseau de cet ordinateur (ex.
# http://192.168.3.212:8000). Sans elle dans ALLOWED_HOSTS, Django répond
# « 400 » et la TV n'affiche rien. En production, DJANGO_ALLOWED_HOSTS reste
# la seule liste (adresse et nom du serveur).
if DEBUG and env_bool("DJANGO_AUTORISER_ADRESSES_LOCALES", "True"):
    ALLOWED_HOSTS += [a for a in _adresses_de_cet_ordinateur() if a not in ALLOWED_HOSTS]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "core",
    "comptes",
    "centres",
    "patients",
    "parcours",
    "circuit",
    "audit",
    "statistiques",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Fichiers statiques (CSS, HTMX) servis par l'application : fonctionne
    # hors Internet et quel que soit le serveur web retenu.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "centres.middleware.CentreActifMiddleware",
    # Comptes connectés (24/11/2026) : heure de la dernière page ouverte.
    "comptes.presence.PresenceMiddleware",
    "audit.threadlocal.UtilisateurCourantMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "core.context_processors.navigation",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("DB_NAME", "gestion_file_attente"),
        "USER": os.getenv("DB_USER", "gestion_app"),
        "PASSWORD": os.getenv("DB_PASSWORD"),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Indian/Antananarivo"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Production : noms de fichiers versionnés (app.4f3c….css) et compressés,
# pour que la TV, la borne et les tablettes rechargent le CSS / JS après
# une mise à jour au lieu de garder l'ancienne version en cache.
# Nécessite « python manage.py collectstatic » à chaque déploiement.
if not DEBUG and not EN_TEST:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
    }

# Cache partagé par tous les processus Gunicorn (décision du 24/11/2026) : la
# mémoire de chaque processus est séparée, si bien que les comptes connectés
# (relais de l'accueil par l'infirmerie) et les verrous (clôture automatique,
# une recherche d'écrans toutes les 10 s) ne valaient que pour un processus.
# Cache dans des fichiers : rien à installer. Dossier réglable par
# DJANGO_CACHE_DOSSIER ; par défaut un dossier temporaire du service
# (PrivateTmp de systemd : commun aux processus du service, vidé au redémarrage).
# Développement (runserver, un seul processus) et tests : cache en mémoire.
if not DEBUG and not EN_TEST:
    import tempfile

    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": os.getenv(
                "DJANGO_CACHE_DOSSIER", os.path.join(tempfile.gettempdir(), "gestion_file_attente_cache")
            ),
            "OPTIONS": {"MAX_ENTRIES": 2000},
        }
    }

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = 'comptes.Utilisateur'

AUTHENTICATION_BACKENDS = ["comptes.backends.AffectationBackend"]

# Blocage temporaire d'un identifiant après plusieurs échecs de connexion.
CONNEXION_MAX_ECHECS = int(os.getenv("CONNEXION_MAX_ECHECS", "5"))
CONNEXION_BLOCAGE_MINUTES = int(os.getenv("CONNEXION_BLOCAGE_MINUTES", "15"))
# Limite par adresse IP (tous identifiants confondus) sur la même fenêtre.
CONNEXION_MAX_ECHECS_IP = int(os.getenv("CONNEXION_MAX_ECHECS_IP", "20"))

# Proxys de confiance (Nginx) dont l'en-tête X-Real-IP donne l'adresse du client.
PROXIES_DE_CONFIANCE = tuple(
    ip.strip() for ip in os.getenv("DJANGO_PROXIES_CONFIANCE", "").split(",") if ip.strip()
)

LOGIN_URL = "connexion"
LOGIN_REDIRECT_URL = "accueil"
LOGOUT_REDIRECT_URL = "connexion"

# Borne et écran : accès limité au réseau local (adresses privées).
RESEAU_LOCAL_UNIQUEMENT = env_bool("RESEAU_LOCAL_UNIQUEMENT", "True")
# Délai minimal entre deux tickets émis par une même borne (limitation de débit).
BORNE_DELAI_MIN_SECONDES = float(os.getenv("BORNE_DELAI_MIN_SECONDES", "2"))
# Imprimante simulée sur ce poste (python manage.py imprimante_simulee) : développement seulement.
IMPRIMANTE_SIMULEE_AUTORISEE = env_bool("IMPRIMANTE_SIMULEE_AUTORISEE", str(DEBUG))
# Annonce vocale des appels fabriquée par le serveur (voix française installée sur la machine).
ANNONCE_VOCALE_SERVEUR = env_bool("ANNONCE_VOCALE_SERVEUR", "True")
# Plafond de tickets par borne sur une heure glissante (protection contre l'inondation).
BORNE_MAX_TICKETS_PAR_HEURE = int(os.getenv("BORNE_MAX_TICKETS_PAR_HEURE", "150"))

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
# Session des agents : déconnexion automatique (postes et tablettes partagés).
SESSION_COOKIE_AGE = int(os.getenv("SESSION_DUREE_SECONDES", str(12 * 3600)))
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

# HTTPS : à activer seulement si le serveur est servi en HTTPS
# (certificat local ou proxy Nginx). Sur un réseau local en HTTP simple,
# laisser DJANGO_HTTPS=False, sinon les cookies ne seraient jamais envoyés.
HTTPS = env_bool("DJANGO_HTTPS", "False")
SESSION_COOKIE_SECURE = HTTPS
CSRF_COOKIE_SECURE = HTTPS
if HTTPS:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SSL_REDIRECT", "True")
    SECURE_HSTS_SECONDS = int(os.getenv("DJANGO_HSTS_SECONDES", "0"))

CSRF_TRUSTED_ORIGINS = [
    origine.strip()
    for origine in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origine.strip()
]

X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "same-origin"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": os.getenv("DJANGO_LOG_LEVEL", "WARNING")},
}

if EN_TEST:
    PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
