#!/usr/bin/env bash
# Test de restauration (serveur Ubuntu) : équivalent de test_restauration.ps1.
#
# Restaure une sauvegarde dans une base DISTINCTE (gfa_restauration_test par
# défaut), compare le nombre de lignes de chaque table avec la production,
# vérifie les triggers « ajout seulement », puis supprime la base de test.
# La base de production n'est jamais modifiée.
#
# Usage : scripts/test_restauration.sh [fichier.dump]
#   (sans argument : la plus récente de /var/backups/gfa)
# Variables : DOSSIER, BASE_TEST, CONSERVER=1 (garder la base de test).
set -euo pipefail

RACINE="$(cd "$(dirname "$0")/.." && pwd)"
DOSSIER="${DOSSIER:-/var/backups/gfa}"
BASE_TEST="${BASE_TEST:-gfa_restauration_test}"

lire_env() {
    # Même lecture que sauvegarde.sh : clé absente = vide, guillemets retirés.
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

if [ "$BASE_TEST" = "$DB_NAME" ]; then
    echo "La base de test doit être distincte de la base de production." >&2
    exit 1
fi

SAUVEGARDE="${1:-$(ls -1t "$DOSSIER"/*.dump 2>/dev/null | head -n 1 || true)}"
if [ -z "$SAUVEGARDE" ] || [ ! -f "$SAUVEGARDE" ]; then
    echo "Aucune sauvegarde trouvée." >&2
    exit 1
fi

CONNEXION=(-h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER")
dropdb "${CONNEXION[@]}" --if-exists "$BASE_TEST"
createdb "${CONNEXION[@]}" "$BASE_TEST"
pg_restore "${CONNEXION[@]}" -d "$BASE_TEST" --no-owner --exit-on-error "$SAUVEGARDE"

REQUETE="select string_agg(relname || '=' || (xpath('/row/c/text()', query_to_xml('select count(*) c from ' || quote_ident(relname), false, true, '')))[1]::text, ' ' order by relname) from pg_stat_user_tables where schemaname = 'public'"
SOURCE="$(psql "${CONNEXION[@]}" -d "$DB_NAME" -tAc "$REQUETE")"
RESTAUREE="$(psql "${CONNEXION[@]}" -d "$BASE_TEST" -tAc "$REQUETE")"
TRIGGERS="$(psql "${CONNEXION[@]}" -d "$BASE_TEST" -tAc "select count(*) from pg_trigger where not tgisinternal")"

echo "$(date '+%F %T') Sauvegarde : $SAUVEGARDE"
echo "Tables restaurées : $(echo "$RESTAUREE" | wc -w) ; triggers métier : $TRIGGERS"
if [ "$SOURCE" = "$RESTAUREE" ]; then
    echo "RESULTAT : restauration conforme (nombre de lignes identique pour toutes les tables)."
else
    echo "RESULTAT : différences détectées (normal si la base a changé depuis la sauvegarde) :"
    diff <(tr ' ' '\n' <<< "$SOURCE") <(tr ' ' '\n' <<< "$RESTAUREE") || true
fi

if [ "${CONSERVER:-0}" != "1" ]; then
    dropdb "${CONNEXION[@]}" "$BASE_TEST"
    echo "Base $BASE_TEST supprimée."
fi

# Une table absente de la sauvegarde est une vraie erreur (les écarts de
# nombre de lignes, eux, sont normaux si la base a changé depuis) : code de
# sortie non nul, visible dans le journal de cron.
TABLES_SOURCE="$(tr ' ' '\n' <<< "$SOURCE" | cut -d= -f1)"
TABLES_RESTAUREES="$(tr ' ' '\n' <<< "$RESTAUREE" | cut -d= -f1)"
if [ "$TABLES_SOURCE" != "$TABLES_RESTAUREES" ]; then
    echo "ERREUR : la sauvegarde ne contient pas les mêmes tables que la production." >&2
    exit 4
fi
