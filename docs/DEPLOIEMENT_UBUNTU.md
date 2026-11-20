# Déploiement sur le serveur Ubuntu

Procédure pour installer l'application sur le serveur du centre (Ubuntu 24.04 LTS), sur le réseau local, sans Internet obligatoire après l'installation.

> **Statut :** procédure écrite et relue, **pas encore exécutée sur le serveur réel**. Notez chaque écart rencontré pour corriger ce document.

Architecture : Nginx (HTTPS, port 443) → Gunicorn (127.0.0.1:8000, workers `gthread`) → Django → PostgreSQL. Les fichiers statiques sont servis par l'application (WhiteNoise).

Les fichiers prêts à copier sont dans `deploiement/` :
- `gunicorn.conf.py` ;
- `gestion-file-attente.service` (systemd) ;
- `nginx-gestion-file-attente.conf` ;
- `crontab`.

Les scripts de sauvegarde sont dans `scripts/` :
- `sauvegarde.sh` ;
- `test_restauration.sh`.

## 1. Paquets et fuseau horaire

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-dev postgresql nginx git openssl
sudo timedatectl set-timezone Indian/Antananarivo
```

## 2. Utilisateur et dossiers

```bash
sudo adduser --system --group --home /opt/gestion_file_attente gfa
sudo mkdir -p /var/log/gfa /var/backups/gfa
sudo chown gfa:gfa /var/log/gfa /var/backups/gfa
sudo chmod 700 /var/backups/gfa
```

## 3. PostgreSQL : deux rôles

Les tables appartiennent à un rôle **propriétaire**. L'application se connecte avec un autre rôle, qui ne peut ni modifier ni supprimer l'historique.

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE gfa_proprietaire LOGIN PASSWORD 'mot-de-passe-long-1';
CREATE ROLE gestion_app LOGIN PASSWORD 'mot-de-passe-long-2';
CREATE DATABASE gestion_file_attente OWNER gfa_proprietaire;
\c gestion_file_attente
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS btree_gist;
GRANT CONNECT ON DATABASE gestion_file_attente TO gestion_app;
GRANT USAGE ON SCHEMA public TO gestion_app;
SQL
```

Remplacez les mots de passe par des mots de passe longs et aléatoires.

## 4. Code et environnement Python

```bash
sudo -u gfa git clone <adresse du dépôt> /opt/gestion_file_attente
cd /opt/gestion_file_attente
sudo -u gfa python3 -m venv .venv
sudo -u gfa .venv/bin/pip install -r requirements.txt
sudo -u gfa cp .env.exemple .env
sudo chmod 600 .env
```

Si le serveur n'a pas Internet : préparez les paquets sur un poste connecté avec `pip download -r requirements.txt -d paquets`, copiez le dossier, puis installez avec `pip install --no-index --find-links paquets -r requirements.txt`.

Renseignez `.env` :

| Variable | Valeur |
|---|---|
| `DJANGO_DEBUG` | `False` |
| `DJANGO_SECRET_KEY` | `python3 -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `DJANGO_ALLOWED_HOSTS` | `192.168.1.10,serveur-files.local` (adresse et nom du serveur) |
| `DJANGO_HTTPS` | `True` (après l'étape 6) |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | `https://192.168.1.10,https://serveur-files.local` |
| `DJANGO_PROXIES_CONFIANCE` | `127.0.0.1` (Nginx) |
| `DB_NAME`, `DB_HOST`, `DB_PORT` | `gestion_file_attente`, `127.0.0.1`, `5432` |
| `DB_USER`, `DB_PASSWORD` | rôle **de l'application** (`gestion_app`) |

## 5. Base de données : migrations avec le rôle propriétaire

Les migrations créent les tables : elles s'exécutent avec le rôle propriétaire, passé seulement le temps de la commande.

```bash
cd /opt/gestion_file_attente
sudo -u gfa env DB_USER=gfa_proprietaire DB_PASSWORD='mot-de-passe-long-1' .venv/bin/python manage.py migrate
sudo -u gfa .venv/bin/python manage.py collectstatic --noinput
```

Droits de l'application : tout sauf modifier ou supprimer l'historique.

```bash
sudo -u postgres psql -d gestion_file_attente <<'SQL'
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gestion_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gestion_app;
REVOKE UPDATE, DELETE ON circuit_evenement, audit_journalaudit FROM gestion_app;
SQL
```

À refaire après chaque mise à jour qui ajoute des tables : `migrate` avec le rôle propriétaire, puis les trois lignes ci-dessus.

Ensuite :

```bash
sudo -u gfa .venv/bin/python manage.py initialiser_roles
sudo -u gfa .venv/bin/python manage.py createsuperuser
sudo -u gfa .venv/bin/python manage.py check --deploy
```

Puis la mise en service d'un centre (README, « Mise en service d'un centre »).

## 6. HTTPS avec une autorité de certification locale

Sans HTTPS, les mots de passe et les cookies circulent en clair sur le Wi-Fi. Sur un réseau local sans nom de domaine public, on crée une **autorité locale** propre au centre, puis on installe son certificat sur chaque appareil.

```bash
sudo mkdir -p /etc/ssl/gfa && cd /etc/ssl/gfa
# Autorité locale (10 ans) : garder ca.key hors du serveur après usage.
sudo openssl req -x509 -newkey rsa:4096 -nodes -days 3650 -keyout ca.key -out ca.crt -subj "/CN=Autorite locale SMIE FUNTOA"
# Certificat du serveur (2 ans), valable pour son adresse IP et son nom.
sudo openssl req -newkey rsa:2048 -nodes -keyout serveur.key -out serveur.csr -subj "/CN=serveur-files.local"
printf "subjectAltName=IP:192.168.1.10,DNS:serveur-files.local\n" | sudo tee serveur.ext
sudo openssl x509 -req -in serveur.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 825 -out serveur.crt -extfile serveur.ext
sudo chmod 600 serveur.key ca.key
```

Installer `ca.crt` sur chaque appareil :
- **postes Windows** : double-clic → Installer → « Autorités de certification racines de confiance » ;
- **tablettes Android** : Paramètres → Sécurité → Chiffrement → Installer un certificat → Certificat CA ;
- **Smart TV** : selon le modèle. Si la TV ne permet pas d'ajouter une autorité, voir « Limites » plus bas.

## 7. Gunicorn (systemd) et Nginx

```bash
sudo cp /opt/gestion_file_attente/deploiement/gestion-file-attente.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now gestion-file-attente
sudo systemctl status gestion-file-attente

sudo cp /opt/gestion_file_attente/deploiement/nginx-gestion-file-attente.conf /etc/nginx/sites-available/gestion-file-attente
# Adapter l'adresse et le nom du serveur dans le fichier, puis :
sudo ln -s /etc/nginx/sites-available/gestion-file-attente /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

Gunicorn utilise des workers `gthread` : une imprimante éteinte (4 s d'attente) ne bloque pas les autres postes.

## 8. Pare-feu

```bash
sudo ufw allow from 192.168.1.0/24 to any port 80,443 proto tcp
sudo ufw allow OpenSSH
sudo ufw enable
```

PostgreSQL n'écoute que sur `127.0.0.1` (valeur par défaut d'Ubuntu) : ne pas l'ouvrir au réseau.

## 9. Tâches planifiées et sauvegardes

```bash
cd /opt/gestion_file_attente
sudo chmod +x scripts/*.sh
sudo crontab -u gfa deploiement/crontab
```

- Clôture des tickets à 20 h 05, puis une seconde fois la nuit.
- Sauvegarde chaque soir à 22 h, vers `/var/backups/gfa`, avec copie sur un second support (adapter `/media/sauvegarde_usb`).
- Test de restauration le 1er de chaque mois dans une base distincte.

Lancez une première fois à la main et gardez la sortie comme preuve datée :

```bash
sudo -u gfa /opt/gestion_file_attente/scripts/sauvegarde.sh /var/backups/gfa
sudo -u gfa /opt/gestion_file_attente/scripts/test_restauration.sh
```

Le compte `gestion_app` doit pouvoir créer la base de test : `sudo -u postgres psql -c "ALTER ROLE gestion_app CREATEDB;"`.

### Restaurer réellement (incident)

1. Arrêter l'application : `sudo systemctl stop gestion-file-attente`.
2. Restaurer **avec le rôle propriétaire**, jamais avec `gestion_app`. Sinon les tables appartiendraient à l'application, et l'interdiction de modifier l'historique serait sans effet :
   ```bash
   sudo -u postgres dropdb gestion_file_attente
   sudo -u postgres createdb -O gfa_proprietaire gestion_file_attente
   sudo -u postgres pg_restore -d gestion_file_attente --role=gfa_proprietaire --no-owner /var/backups/gfa/<fichier>.dump
   ```
3. Refaire les `GRANT` et le `REVOKE` de l'étape 5.
4. Redémarrer : `sudo systemctl start gestion-file-attente`.

## 10. Mise à jour de l'application

```bash
cd /opt/gestion_file_attente
sudo -u gfa /opt/gestion_file_attente/scripts/sauvegarde.sh /var/backups/gfa
sudo -u gfa git pull --ff-only
sudo -u gfa .venv/bin/pip install -r requirements.txt
sudo -u gfa env DB_USER=gfa_proprietaire DB_PASSWORD='…' .venv/bin/python manage.py migrate
sudo -u gfa .venv/bin/python manage.py collectstatic --noinput
sudo -u gfa .venv/bin/python manage.py initialiser_roles
sudo systemctl restart gestion-file-attente
```

Puis refaire les droits de l'étape 5 si de nouvelles tables sont apparues.

## 11. Postes

- **Agents** : `https://serveur-files.local/`.
- **Borne** : `https://serveur-files.local/circuit/borne/` dans Chrome en mode épinglé, ou dans l'application Android (adresse en `https://`).
- **Écran TV** : `https://serveur-files.local/circuit/ecran/`.
  - Pour que le son reprenne seul après un redémarrage de la TV, lancer le navigateur en mode kiosque avec la lecture automatique autorisée. Sur un mini-PC ou une clé HDMI sous Chrome : `--kiosk --autoplay-policy=no-user-gesture-required <adresse>`.
  - Sinon, l'écran rappelle « Activer le son », et le premier appui sur la télécommande le réactive.

## Limites connues, à valider sur site

- **Smart TV sans gestion des certificats** : si la TV refuse le certificat local, utilisez un mini-PC ou une clé HDMI (Chrome) qui accepte l'autorité locale. Ne repassez pas tout le site en HTTP pour la TV seule : les mots de passe des agents circuleraient en clair.
- **HSTS** : laisser `DJANGO_HSTS_SECONDES=0` tant que tous les appareils n'ont pas été vérifiés en HTTPS. HSTS empêche ensuite tout retour au HTTP.
- Procédure **non encore exécutée** sur le serveur réel. Imprimante, TV et tablette sont à valider sur place.
