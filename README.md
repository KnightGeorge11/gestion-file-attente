# Gestion des files d'attente médicales — SMIE FUNTOA

Monolithe modulaire Django 5.2 + PostgreSQL + Django Templates / HTMX.
Fonctionne entièrement sur le réseau local : HTMX et le CSS sont servis par l'application, aucune ressource Internet n'est chargée.

La référence métier est le tableau Trello « Gestion des files d'attente médicales ».
Ce document distingue les **règles confirmées** dans Trello, les **paramètres configurables** et les **points encore à confirmer**.

## Applications

| Application | Rôle |
|---|---|
| `core` | Isolation des données par centre (manager, mixin d'administration, accès), fuseaux horaires, filtre d'affichage des durées |
| `centres` | Centres, services, horaires, files, bornes, écrans d'affichage |
| `comptes` | Utilisateurs, affectations (centre + rôle + services + permissions individuelles), backend d'autorisation |
| `patients` | Fiche centrale partagée, rattachement par centre (matricule `CODE-000001`), recherche, doublons, fusion réversible |
| `parcours` | Parcours versionnés (M, D, I, E), quotas du dentiste |
| `circuit` | Tickets, passages, files, appels, transferts, quotas réservés, mode Autre, événements ; interfaces agents, borne, écran |
| `audit` | Journal d'audit (connexions, rôles, quotas, publications, désactivations, fusions, exports) |
| `statistiques` | Tableau de bord de la direction, exports PDF et Excel |

Le module `salles` n'a pas été créé : il reste optionnel et non validé (Trello).

## Règles métier

### Confirmées dans Trello (implémentées)

- La borne libre-service est près de la sécurité ; **la sécurité n'a aucun rôle ni compte** dans le logiciel.
- Avant le ticket, le patient choisit un mode : **Interne, Externe ou Autre**. Interne et Externe suivent les mêmes parcours et files (catégorie conservée pour information).
- Tickets **M, D, I, E** à compteurs indépendants, format lettre + 3 chiffres (M001), **remise à zéro à minuit** (heure du centre) ; un numéro annulé n'est jamais réutilisé.
- **Tous les patients passent par l'accueil** après le ticket.
- Parcours : **M** accueil → infirmerie → file commune des médecins → consultation ; **D** accueil → dentiste ; **E** accueil → échographie ; **I** accueil → infirmerie → fin. Marche avant.
- **Pharmacie : jamais automatique ni obligatoire.** Le service de soins qui prend en charge le patient (médecin, dentiste, échographie, infirmerie) peut décider explicitement, à la fin de sa prise en charge, de l'envoyer à la pharmacie (case « Transférer à la pharmacie »). Aucune version de parcours ne contient la pharmacie.
- Chaque service appelle ses patients ; **un écran principal unique affiche simultanément les appels de plusieurs services** ; la pharmacie peut appeler sur l'écran ou oralement.
- File commune des médecins : le patient appelé est attribué au médecin connecté qui l'appelle (verrou, pas de double prise).
- Comptes individuels pour chaque agent ; interface commune par service.
- **Chronométrage** de l'attente et de la prise en charge à chaque passage, et durée totale du ticket.
- Absence seulement après **3 appels** sans réponse ; réintégration sans effacer l'historique.
- **Un patient n'a qu'un seul ticket ouvert à la fois** (tous centres confondus) ; un ticket supplémentaire est annulé à l'accueil.
- **Quotas dentiste** : 7 le matin et 4 l'après-midi du lundi au vendredi, 4 le samedi matin ; bascule à 12 h 00 ; fermé le samedi après-midi et le dimanche. Place réservée à l'**identification à l'accueil** ; blocage quand le quota est atteint ; **dérogation** possible uniquement par un responsable autorisé, avec motif obligatoire, historisée ; **libération** manuelle avec motif obligatoire, historisée. Modification des quotas dans l'administration, historisée (auteur, date, avant/après).
- **Mode Autre** (rendez-vous, cotisations, rencontre d'une personne…) : notification à l'accueil ; l'accueil oriente vers une **personne précise** ou un **service** (dans ce cas, un **agent désigné** du service est notifié, pas tous les agents) ; le destinataire répond par un **statut prédéfini** (demande reçue, prise en compte, en cours de traitement, patient attendu, traitée, non réalisable) **et un message libre facultatif**. Le ticket est clôturé après l'orientation ; la demande reste suivie indépendamment.
- **Dossier patient unique partagé** entre centres ; matricule propre à chaque centre `CODECENTRE-000001` ; tickets, passages et activités isolés par centre.
- Le concept de « visite » n'existe pas.

### Paramètres configurables (administration)

| Paramètre | Où | Valeur actuelle |
|---|---|---|
| Délai entre deux appels | File d'attente | 60 s (provisoire) |
| Appels avant absence | File d'attente | 3 |
| Appels simultanés | File d'attente | 1 ; file des médecins = nombre de médecins |
| Ordre strict | File d'attente | oui |
| Réintégrations maximum | Centre | 1 |
| Tickets ouverts par patient | Centre | 1 |
| Heure de bascule matin / après-midi | Centre | 12 h 00 |
| Durée de mise en évidence d'un appel à l'écran | Centre | 30 s |
| Quotas (jour, période, dates de validité) | Quotas de parcours | voir ci-dessus |
| Motifs du mode Autre | Motifs de demande | Rendez-vous, Cotisations, Rencontrer une personne du centre |
| Agent désigné des demandes « Autre » | Service | à renseigner par service |
| Appel oral autorisé | Service | pharmacie |

### Encore à confirmer (comportement provisoire actuellement codé)

| Point (carte « Répondre aux points ouverts ») | Comportement provisoire |
|---|---|
| Identification obligatoire avant le premier service | Exigée pour les tickets de soins |
| Heure de clôture des tickets restés ouverts | Commande nocturne : tickets des jours précédents clôturés, passages en cours annulés |
| Transfert pharmacie depuis l'infirmerie du parcours M (étape intermédiaire) | Autorisé avec motif obligatoire ; la consultation n'est pas réalisée ; le ticket se termine après la pharmacie |
| Transfert depuis l'**accueil**, ou vers un autre service que la pharmacie | Non proposé. **Décision à prendre** : Trello indique que « chaque service » peut envoyer à la pharmacie, mais la carte des points ouverts liste encore le transfert depuis l'accueil comme non tranché |
| Fin du ticket après un transfert | Le ticket se termine après la pharmacie |
| Délai entre appels, position après réintégration | 60 s ; réintégration en fin de file |
| Pharmacie, appel oral | Bouton « Appel oral » = prise en charge directe, puis « Terminer » (horaires enregistrés) |
| File des médecins : ordre strict ou libre ; guichets d'accueil | Ordre strict ; 1 guichet d'accueil (paramétrable) |
| Préfixe des tickets « Autre » | `A` avec son propre compteur |
| Droits de la direction | Statistiques sans noms de patients |
| Priorités, durée de conservation des historiques, module salles | Non implémentés |

## Installation (développement)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.exemple .env      # puis renseigner, avec DJANGO_DEBUG=True en développement
python manage.py migrate
python manage.py initialiser_roles
python manage.py createsuperuser
```

Le compte PostgreSQL doit pouvoir créer les extensions `pg_trgm` et `btree_gist` (première migration) et la base de test (`CREATEDB`).

## Mise en service d'un centre

1. Créer le centre dans l'administration (`/admin/`).
2. `python manage.py initialiser_parcours <CODE> --medecins 2` : services, parcours publiés, quotas dentiste.
3. Créer les comptes et leurs affectations (centre + rôle + services).
4. Créer une borne et un écran dans l'administration : la clé n'est affichée qu'une fois.
5. Renseigner les motifs du mode Autre et, pour chaque service, l'agent désigné.

Pour une recette : `python manage.py preparer_demo <CODE> --mot-de-passe "..."` (un compte par rôle `<role>_<code>`, une borne `BORNE1`, un écran `ECRAN1`).

## Production sur le réseau local

Paramètres (voir `.env.exemple`) :

- `DJANGO_DEBUG=False` (valeur par défaut) ; `DJANGO_SECRET_KEY` aléatoire d'au moins 50 caractères (l'application refuse de démarrer sinon) ;
- `DJANGO_ALLOWED_HOSTS` = adresse IP et nom du serveur sur le réseau local ;
- `DJANGO_HTTPS=True` seulement si le site est servi en HTTPS (sinon les cookies sécurisés ne seraient jamais envoyés) ;
- sessions agents : 12 h (`SESSION_DUREE_SECONDES`) ; borne et écran : 1 an, renouvelée à l'usage.

À chaque déploiement :

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
```

Les fichiers statiques sont servis par l'application (WhiteNoise), avec des noms versionnés pour que la TV, la borne et les tablettes rechargent le CSS après une mise à jour.
Serveur d'application : à choisir avec le serveur matériel (carte Trello « Sauvegarde PostgreSQL et déploiement ») — par exemple Waitress sous Windows, Gunicorn (+ Nginx) sous Linux. Le serveur de développement `runserver` ne doit pas être utilisé en production.

Sur un réseau local en HTTP simple, `check --deploy` signale 4 avertissements liés à HTTPS (HSTS, redirection SSL, cookies sécurisés) : ils sont attendus et disparaissent avec `DJANGO_HTTPS=True` derrière un certificat.

## Postes

- **Borne** : `http://<serveur>/circuit/borne/`. Activer une fois (code centre + code borne + clé). Ne pas se connecter comme agent sur le navigateur de la borne : la déconnexion d'un agent désactiverait aussi la borne.
- **Impression** : la page du ticket appelle l'impression du navigateur (format 80 mm). Pour imprimer sans boîte de dialogue sur une imprimante thermique, lancer Chrome en mode kiosque : `chrome --kiosk --kiosk-printing http://<serveur>/circuit/borne/` avec l'imprimante thermique définie par défaut. **Non testé sur imprimante réelle** : à valider sur site.
- **Écran principal (TV)** : `http://<serveur>/circuit/ecran/`, activé avec la clé de l'écran. Pour un même ticket, seul le dernier appel est affiché. Cliquer une fois sur « Activer le son » (les navigateurs bloquent le son sans action de l'utilisateur) : carillon et annonce vocale si une voix française est installée sur l'appareil. **Son non vérifié sur la TV réelle.**
- **Agents** : `http://<serveur>/` → connexion individuelle → choix du centre → interface commune du service.

La borne et l'écran n'acceptent que les adresses du réseau local (`RESEAU_LOCAL_UNIQUEMENT=True`).

## Sauvegarde et restauration

- `scripts\sauvegarde.ps1 -Dossier D:\sauvegardes -CopieVers E:\sauvegardes` : `pg_dump` compressé, archive vérifiée, copie sur un second support, conservation des 30 dernières. À planifier chaque jour (Planificateur de tâches Windows).
- `scripts\test_restauration.ps1` : restaure la dernière sauvegarde dans une **base distincte** (`gfa_restauration_test`), compare le nombre de lignes de chaque table avec la production, puis supprime la base de test. La production n'est jamais modifiée.
- Sous Linux : mêmes commandes `pg_dump -Fc` / `createdb` / `pg_restore --no-owner` via cron.

## Tâches planifiées

- `python manage.py cloturer_tickets` chaque nuit (ex. 00 h 30).
- `scripts\sauvegarde.ps1` chaque jour, et `scripts\test_restauration.ps1` au moins une fois par mois.

## Sécurité et intégrité

- Le centre actif vient toujours de la session, jamais du navigateur ; chaque objet est relu dans le centre actif (404 sinon). Seul l'administrateur central (superutilisateur ou type « Administrateur central ») a une vue transversale.
- Droits vérifiés côté serveur avec `comptes.permissions.a_droit(utilisateur, permission, centre, service)`.
- Administration Django filtrée par centre pour les comptes non centraux ; les champs donnant des droits transversaux (superutilisateur, type de compte, groupes) ne leur sont pas modifiables.
- Triggers PostgreSQL : cohérence de `centre_id` entre tables liées ; `circuit_evenement` et `audit_journalaudit` refusent UPDATE et DELETE. En production, retirer aussi ces droits au compte de l'application :
  `REVOKE UPDATE, DELETE ON circuit_evenement, audit_journalaudit FROM <compte_app>;` (le propriétaire des tables doit être un autre rôle).
- Contraintes : numérotation unique par centre/préfixe/jour, un patient tenu par agent, un passage actif par ticket, une réservation de quota active par ticket.
- Les comptes ne sont jamais supprimés (désactivation seulement).

## Tests

```bash
python manage.py test --noinput
```

Tests sur PostgreSQL (base de test créée automatiquement) : comptes et permissions, isolation entre centres, numérotation et appels simultanés, recette des quatre parcours par les interfaces avec et sans pharmacie, mode Autre, quotas, absences et réintégrations, chronométrage à horloge contrôlée, borne, écran, audit, statistiques et exports, fusion de patients.
