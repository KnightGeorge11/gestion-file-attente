#!/usr/bin/env bash
# Sauvegarde PostgreSQL (serveur Ubuntu) : équivalent de sauvegarde.ps1.
#
# - Format personnalisé pg_dump (-Fc), compressé, restaurable avec pg_restore.
# - Archive vérifiée (pg_restore --list).
# - Copie sur un second support si un second dossier est indiqué : seulement
#   s'il est monté (jamais sur le disque du serveur à la place de la clé).
#   En cas d'échec de la copie, la rotation locale est faite quand même et le
#   script se termine en erreur (visible dans le journal de cron).
# - Conserve les N dernières sauvegardes (CONSERVER, 30 par défaut).
# - Paramètres de connexion lus dans le fichier .env du projet.
#
# Usage : scripts/sauvegarde.sh [dossier] [copie_vers]
#   scripts/sauvegarde.sh /var/backups/gfa /media/sauvegarde_usb
set -euo pipefail

RACINE="$(cd "$(dirname "$0")/.." && pwd)"
DOSSIER="${1:-/var/backups/gfa}"
COPIE_VERS="${2:-}"
CONSERVER="${CONSERVER:-30}"

lire_env() {
    # Valeur d'une variable du .env (sans exécuter le fichier). Clé absente :
    # valeur vide (la valeur par défaut s'applique). Guillemets retirés, comme
    # le fait python-dotenv.
    local valeur
    valeur="$( { grep -E "^$1=" "$RACINE/.env" || true; } | tail -n 1 | cut -d= -f2- | tr -d '\r')"
    valeur="${valeur%\"}"; valeur="${valeur#\"}"
    valeur="${valeur%\'}"; valeur="${valeur#\'}"
    printf '%s' "$valeur"
}

DB_NAME="$(lire_env DB_NAME)"
DB_USER="$(lire_env DB_USER)"
DB_HOST="$(lire_env DB_HOST)"; DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="$(lire_env DB_PORT)"; DB_PORT="${DB_PORT:-5432}"
export PGPASSWORD="$(lire_env DB_PASSWORD)"
trap 'unset PGPASSWORD' EXIT

mkdir -p "$DOSSIER"
chmod 700 "$DOSSIER"
FICHIER="$DOSSIER/${DB_NAME}_$(date +%Y%m%d_%H%M%S).dump"

pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -Fc -f "$FICHIER" "$DB_NAME"
# Vérifie que l'archive est lisible.
pg_restore --list "$FICHIER" > /dev/null
chmod 600 "$FICHIER"
echo "$(date '+%F %T') Sauvegarde créée : $FICHIER ($(du -h "$FICHIER" | cut -f1))"

# Rotation : on garde les $CONSERVER plus récentes (avant la copie, pour
# qu'un problème de second support ne remplisse jamais le disque du serveur).
ls -1t "$DOSSIER"/*.dump 2>/dev/null | tail -n +"$((CONSERVER + 1))" | xargs -r rm -f

if [ -n "$COPIE_VERS" ]; then
    if ! mountpoint -q "$COPIE_VERS"; then
        echo "$(date '+%F %T') ERREUR : le second support n'est pas monté sur $COPIE_VERS : copie NON faite." >&2
        exit 2
    fi
    if ! cp "$FICHIER" "$COPIE_VERS/"; then
        echo "$(date '+%F %T') ERREUR : copie impossible sur $COPIE_VERS (droits d'écriture de gfa ?)." >&2
        exit 3
    fi
    echo "$(date '+%F %T') Copie sur le second support : $COPIE_VERS"
    # Même rotation sur le second support (sinon il finit par se remplir).
    ls -1t "$COPIE_VERS"/*.dump 2>/dev/null | tail -n +"$((CONSERVER + 1))" | xargs -r rm -f
fi
