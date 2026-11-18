# Gestion des files d'attente médicales — SMIE FUNTOA

Monolithe modulaire Django 5.2 + PostgreSQL + Django Templates / HTMX.
Fonctionne entièrement sur le réseau local (HTMX est servi localement, aucune ressource Internet).

## Applications

| Application | Rôle |
|---|---|
| `core` | Isolation des données par centre (manager, mixin d'administration, accès), fuseaux horaires |
| `centres` | Centres, services, horaires, files, bornes, écrans d'affichage |
| `comptes` | Utilisateurs, affectations (centre + rôle + services + permissions individuelles), backend d'autorisation |
| `patients` | Fiche centrale partagée, rattachement par centre (matricule `CODE-000001`), recherche, doublons, fusion réversible |
| `parcours` | Parcours versionnés (M, D, I, E), quotas du dentiste |
| `circuit` | Tickets, passages, files, appels, transferts, quotas réservés, mode Autre, événements ; interfaces agents, borne, écran |
| `audit` | Journal d'audit (connexions, rôles, quotas, publications, désactivations, fusions, exports) |
| `statistiques` | Tableau de bord de la direction, exports PDF et Excel |

Le module `salles` n'a pas été créé : il reste optionnel et non validé (voir Trello).

## Installation

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
# .env : DJANGO_SECRET_KEY, DJANGO_DEBUG, DJANGO_ALLOWED_HOSTS, DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT
python manage.py migrate
python manage.py initialiser_roles
python manage.py createsuperuser
```

Le compte PostgreSQL doit pouvoir créer les extensions `pg_trgm` et `btree_gist` (première migration), et la base de test (`CREATEDB`) pour lancer les tests.

## Mise en service d'un centre

1. Créer le centre dans l'administration (`/admin/`).
2. `python manage.py initialiser_parcours <CODE> --medecins 2` : services, parcours publiés, quotas dentiste (7/4 en semaine, 4 le samedi matin).
3. Créer les comptes et leurs affectations (centre + rôle + services).
4. Créer une borne et un écran dans l'administration : la clé n'est affichée qu'une fois.
5. Ajouter les motifs du mode Autre et, pour chaque service, l'« agent désigné » qui reçoit les demandes.

Pour une recette : `python manage.py preparer_demo <CODE> --mot-de-passe "..."` fait tout cela d'un coup
(un compte par rôle `<role>_<code>`, une borne `BORNE1`, un écran `ECRAN1`).

## Postes

- **Borne** : `http://<serveur>/circuit/borne/`. Activer une fois avec code centre + code borne + clé. Le ticket s'imprime via la boîte d'impression du navigateur ; pour une impression directe sur imprimante thermique 80 mm, lancer Chrome en mode kiosque : `chrome --kiosk --kiosk-printing http://<serveur>/circuit/borne/`.
- **Écran principal (TV)** : `http://<serveur>/circuit/ecran/`, activé avec la clé de l'écran. Cliquer une fois sur « Activer le son » (carillon + annonce vocale si l'appareil le permet).
- **Agents** : `http://<serveur>/` → connexion individuelle → choix du centre → interface commune du service.

La borne et l'écran n'acceptent que des adresses du réseau local (`RESEAU_LOCAL_UNIQUEMENT=True`).

## Tâches planifiées

- `python manage.py cloturer_tickets` chaque nuit (ex. 00 h 30) : clôture des tickets restés ouverts la veille.
- Sauvegarde : `pg_dump` quotidien + copie sur un second support (carte Trello « Sauvegarde PostgreSQL et déploiement »).

## Sécurité et intégrité

- Le centre actif vient toujours de la session, jamais du navigateur ; chaque objet est relu dans le centre actif (404 sinon).
- Les droits sont vérifiés côté serveur avec `comptes.permissions.a_droit(utilisateur, permission, centre, service)`.
- Triggers PostgreSQL : cohérence de `centre_id` entre tables liées ; `circuit_evenement` et `audit_journalaudit` refusent UPDATE et DELETE.
  En production, retirer en plus les droits UPDATE/DELETE de ces deux tables au compte de l'application :
  `REVOKE UPDATE, DELETE ON circuit_evenement, audit_journalaudit FROM <compte_app>;` (le propriétaire des tables doit être un autre rôle).
- Contraintes : numérotation unique par centre/préfixe/jour, un patient tenu par agent, un passage actif par ticket, une réservation de quota active par ticket.
- Les comptes ne sont jamais supprimés (désactivation seulement).

## Tests

```bash
python manage.py test
```

Les tests couvrent notamment la numérotation simultanée, les appels simultanés, les quatre parcours avec et sans pharmacie, les quotas (blocage, dérogation, libération, fermetures), l'absence après 3 appels et la réintégration, l'isolation entre centres, la borne, l'écran, l'audit, les exports et la fusion.

## Règles provisoires (à confirmer — carte « Répondre aux points ouverts »)

- Transfert pharmacie depuis une étape intermédiaire (infirmerie du parcours M) : autorisé avec motif obligatoire ; les étapes restantes ne sont pas réalisées et le ticket se termine après la pharmacie.
- Après un transfert, le ticket se termine après le service destinataire. Pas de transfert depuis l'accueil ni vers un autre service que la pharmacie.
- Délai entre appels 60 s, 3 appels avant absence, 1 réintégration en fin de file (paramétrables par file / centre).
- Clôture : tickets des jours précédents clôturés par la commande nocturne.
- Mode Autre : préfixe `A` avec son propre compteur ; les tickets « Autre » passent par la file de l'accueil.
- Direction : statistiques sans données nominatives des patients.
- Un patient ne peut avoir qu'un ticket ouvert dans un même centre.
