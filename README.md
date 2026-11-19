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
| `patients` | **Inactif** (décision du 18/11/2026 : aucune information patient). Tables conservées, plus aucune page ni permission |
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
- **Progression du patient** (décision du 18/11/2026) : appelé par un service, le patient s'y rend ; quand le service appuie sur « Terminer », le patient peut être appelé par le service suivant de son parcours (celui choisi au départ). Pas de saut d'étape.
- **Pharmacie : jamais automatique ni obligatoire.** Le service de soins qui termine le parcours (médecin, dentiste, échographie, infirmerie pour le parcours I) peut décider explicitement de l'envoyer à la pharmacie (« Transférer à la pharmacie ») ; le ticket se termine après la pharmacie. Pas de pharmacie depuis une étape intermédiaire (infirmerie du parcours M) ni depuis l'accueil (décisions du 18/11/2026). Aucune version de parcours ne contient la pharmacie.
- **Clôture** à **20 h 00** des tickets restés ouverts (heure réglable par centre). Elle est **automatique** : dès qu'une page de la borne, d'un guichet ou de l'écran est ouverte, les tickets à clôturer sont fermés (vérification au plus une fois par minute). La commande `cloturer_tickets` reste disponible en tâche planifiée de sécurité.
- **10 secondes** minimum entre deux appels du même patient ; absence après 3 appels.
- **2 agents minimum par service** (décision du 18/11/2026) : chaque file accepte au moins 2 appels simultanés ; chaque agent ne tient qu'un patient à la fois.
- Tickets « Autre » : préfixe **A** avec leur propre compteur.
- **Qui appelle quoi** (décisions du 19/11/2026) : l'administrateur, les responsables et la direction **n'appellent que les tickets « Autre »** (A…), jamais les patients (M, D, I, E), qui ne relèvent pas de leur département. Ils appellent la demande « Autre » suivante de la file d'accueil, même si des patients sont devant ; l'appel d'un patient, y compris l'appel oral à la pharmacie, leur est refusé côté serveur. La **direction ne voit que les tickets « Autre »** (aucun patient). **Dérogation au quota dentiste** (confirmée le 19/11/2026) : le responsable l'autorise depuis la page du ticket, avec un motif obligatoire ; l'agent d'accueil valide ensuite le ticket au-delà du quota (la réservation est marquée « dérogation », avec le motif et le nom du responsable).
- Chaque service appelle ses patients ; **un écran principal unique affiche simultanément les appels de plusieurs services** ; la pharmacie peut appeler sur l'écran ou oralement.
- File commune des médecins : le patient appelé est attribué au médecin connecté qui l'appelle (verrou, pas de double prise).
- Comptes individuels pour chaque agent ; interface commune par service.
- **Chronométrage** de l'attente et de la prise en charge à chaque passage, et durée totale du ticket.
- Absence seulement après **3 appels** sans réponse ; réintégration sans effacer l'historique.
- **Aucune information sur le patient n'est collectée** (décision du 18/11/2026) : le système fonctionne uniquement avec les numéros de ticket. L'accueil appelle le ticket, le prend en charge puis le **valide** et l'oriente vers son service. Il n'y a ni fiche patient, ni identification, ni règle « un seul ticket par patient ».
- **Quotas dentiste** : 7 le matin et 4 l'après-midi du lundi au vendredi, 4 le samedi matin ; bascule à 12 h 00 ; fermé le samedi après-midi et le dimanche. Place réservée quand l'**accueil valide le ticket** (avant l'orientation vers le dentiste) ; blocage quand le quota est atteint ; **dérogation** possible uniquement par un responsable autorisé, avec motif obligatoire, historisée ; **libération** manuelle avec motif obligatoire, historisée. Modification des quotas dans l'administration, historisée (auteur, date, avant/après).
- **Mode Autre** (rendez-vous, visite d'embauche, entretien, cotisations, rencontre d'une personne…) : notification à l'accueil ; l'accueil oriente vers une **personne précise** ou un **service** (dans ce cas, un **agent désigné** du service est notifié, pas tous les agents) ; le destinataire répond par un **statut prédéfini** (demande reçue, prise en compte, en cours de traitement, patient attendu, traitée, non réalisable) **et un message libre facultatif**. Le ticket est clôturé après l'orientation ; la demande reste suivie indépendamment.
- Tickets, passages et activités isolés par centre.
- Le concept de « visite » n'existe pas.
- **Confidentialité par service** (décision du 18/11/2026) : un agent ne voit que les patients qui viennent dans son service. Un ticket est visible pour un service **à l'avance**, dès que son parcours prévoit ce service (ex. un patient M à l'accueil ou à l'infirmerie est visible des médecins, dans la section « À venir » de leur interface), puis pendant et après son passage. Un patient transféré à la pharmacie depuis une étape intermédiaire n'est plus « à venir » pour les étapes suivantes. L'accueil voit tous les tickets (tous les patients y passent) ; la pharmacie ne voit que les patients qui lui sont transférés ; la direction ne voit aucun patient : seulement les tickets « Autre », qu'elle peut appeler (décision du 19/11/2026). Contrôle côté serveur : liste des tickets du jour et accès direct par adresse (404 sinon).

### Paramètres configurables (administration)

| Paramètre | Où | Valeur actuelle |
|---|---|---|
| Délai entre deux appels | File d'attente | 10 s |
| Appels avant absence | File d'attente | 3 |
| Appels simultanés | File d'attente | **2 minimum** (2 agents par service) ; file des médecins = nombre de médecins |
| Ordre strict | File d'attente | oui |
| Réintégrations maximum | Centre | 1 |
| Heure de bascule matin / après-midi | Centre | 12 h 00 |
| Heure de clôture des tickets | Centre | 20 h 00 |
| Durée de mise en évidence d'un appel à l'écran | Centre | 30 s |
| Quotas (jour, période, dates de validité) | Quotas de parcours | voir ci-dessus |
| Motifs du mode Autre | Motifs de demande | Rendez-vous, Visite d'embauche, Entretien, Cotisations, Rencontrer une personne du centre |
| Agent désigné des demandes « Autre » | Service | à renseigner par service |
| Appel oral autorisé | Service | pharmacie |

### Encore à confirmer (comportement provisoire actuellement codé)

| Point (carte « Répondre aux points ouverts ») | Comportement provisoire |
|---|---|
| Transfert vers un autre service que la pharmacie | Non proposé |
| Position après réintégration | En fin de file |
| Pharmacie, appel oral | Bouton « Appel oral » = prise en charge directe, puis « Terminer » (horaires enregistrés) |
| File des médecins : ordre strict ou libre | Ordre strict |
| Droits de la direction | Statistiques uniquement |
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

## Comptes de test et simulation

Pour une démonstration ou un test manuel pas à pas, voir le **[guide de test](docs/GUIDE_TEST.md)**.

**Uniquement sur une base de développement ou de test** (les deux commandes refusent de s'exécuter si `DEBUG` est désactivé, sauf `--force`).

```bash
python manage.py creer_comptes_test              # 2 comptes par service, tous les centres actifs
python manage.py simuler_patients 001            # 15 tickets, 10 étapes
python manage.py simuler_patients 001 --continu --intervalle 15 --duree 30   # démonstration en direct
```

Comptes créés, mot de passe par défaut **1234** (modifiable avec `--mot-de-passe`), affectés à chaque centre :

| Service / rôle | Identifiants |
|---|---|
| Accueil | `accueiltest1`, `accueiltest2` |
| Infirmerie | `infirmiertest1`, `infirmiertest2` |
| Médecins | `medecintest1`, `medecintest2` |
| Dentiste | `dentistetest1`, `dentistetest2` |
| Échographie | `echographietest1`, `echographietest2` |
| Pharmacie | `pharmacietest1`, `pharmacietest2` |
| Direction (statistiques, appel des « Autre ») | `directiontest1`, `directiontest2` |
| Responsable (dérogations, appel des « Autre ») | `responsabletest1`, `responsabletest2` |

La simulation passe par le moteur réel (toutes les règles s'appliquent). Elle émet ses tickets par une borne dédiée « SIMULATION », n'agit que sur ses propres tickets et uniquement au nom des comptes de test ; elle n'appelle jamais une file dont le premier patient est un vrai patient. Aucune information patient n'est créée. Les tickets simulés restent dans l'historique et les statistiques (rien n'est supprimé).

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
Serveur : **Ubuntu** (décision du 18/11/2026). Serveur d'application conseillé : Gunicorn (éventuellement derrière Nginx). Le serveur de développement `runserver` ne doit pas être utilisé en production. Le déploiement n'est pas pris en charge dans ce dépôt pour l'instant.

### Liste de contrôle sécurité avant la mise en service

- [ ] Mot de passe PostgreSQL fort pour le compte de l'application (l'actuel de développement est trop court).
- [ ] `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY` aléatoire, `DJANGO_ALLOWED_HOSTS` = IP / nom du serveur.
- [ ] Aucun compte de test (`*test1`, `*test2`, mot de passe 1234) sur la base de production : `creer_comptes_test` et `simuler_patients` refusent de s'exécuter si DEBUG est désactivé.
- [ ] `REVOKE UPDATE, DELETE ON circuit_evenement, audit_journalaudit FROM <compte_app>;` (tables possédées par un autre rôle).
- [ ] Dépôt GitHub passé en **privé**.

Sur un réseau local en HTTP simple, `check --deploy` signale 4 avertissements liés à HTTPS (HSTS, redirection SSL, cookies sécurisés) : ils sont attendus et disparaissent avec `DJANGO_HTTPS=True` derrière un certificat.

## Impression des tickets (option A : imprimante réseau)

La tablette Android affiche seulement les choix. Quand le patient prend son ticket, **le serveur l'envoie directement à l'imprimante thermique** de la borne, par le réseau local : aucune fenêtre d'impression sur la tablette, quel que soit son modèle.

**Imprimante à acheter** (critères) :
- thermique **80 mm** avec **coupe automatique** ;
- connexion **Ethernet** (recommandé) ou **Wi-Fi** sur le réseau local. Une imprimante Bluetooth n'est possible qu'avec l'application Android de dernier recours (voir ci-dessous) ;
- compatible **ESC/POS** avec « impression brute » sur le **port TCP 9100** (cas de la plupart des imprimantes de caisse) ;
- page de codes PC858 ou PC850 pour les accents (sinon choisir « Sans accents ») ;
- **adresse IP fixe** (réglée sur l'imprimante ou réservée sur la box/le routeur).

**Mise en service** :
1. Administration → *Imprimantes de tickets* → ajouter (adresse IP, port 9100, 48 caractères pour du 80 mm).
2. Action « Imprimer un ticket de test ».
3. Administration → *Bornes* → choisir cette imprimante pour la borne.

Si l'imprimante ne répond pas, le ticket reste valable : la tablette affiche le numéro en grand (« Notez votre numéro ») avec un bouton « Réessayer l'impression », et l'accueil voit une alerte jusqu'à la prochaine impression réussie. Une borne sans imprimante réseau utilise l'impression du navigateur.

**Tester sans matériel** : `python manage.py imprimante_simulee` (écoute sur 127.0.0.1:9100 et affiche les tickets reçus), puis créer l'imprimante 127.0.0.1:9100 dans l'administration.

**Dernier recours, imprimante Bluetooth** : une petite application Android ([`borne_android/`](borne_android/README.md)) affiche la même page de borne et envoie le ticket, préparé par le serveur, à une imprimante Bluetooth appairée. L'APK est compilé automatiquement par GitHub Actions. **Non testé sur du matériel réel.**

## Interface (système de design)

- **Une seule feuille de style** : `static/css/app.css`. Les couleurs (bleu profond, turquoise, gris clairs), espacements, rayons et ombres y sont des variables : changer une valeur suffit pour toute l'application. L'administration Django reprend les mêmes couleurs avec `static/css/admin.css`.
- **Icônes locales** dans `templates/_icones.html`, utilisées avec `{% load interface %}{% icone "nom" %}`. Le filtre `|icone_service` choisit l'icône d'un service ou d'un parcours.
- **Script commun** `static/js/app.js`, sans dépendance, en complément de HTMX :
  - messages affichés en notifications ;
  - durées et chronomètres mis à jour chaque seconde, calés sur l'heure du serveur ;
  - horloges ;
  - nouveaux tickets surlignés ;
  - barre de chargement pendant les actions ;
  - bandeau « connexion perdue ».
- **Actualisation automatique** :
  - file d'un service toutes les 5 s, chiffres du service toutes les 10 s et juste après chaque action ;
  - liste des services toutes les 10 s ;
  - tickets du jour toutes les 15 s ;
  - statistiques toutes les minutes ;
  - écran principal toutes les 3 s.
- **Aucune ressource externe** : polices du système, pas de CDN. Tout fonctionne hors Internet.

## Postes

- **Borne** : `http://<serveur>/circuit/borne/`. Activer une fois (code centre + code borne + clé). Ne pas se connecter comme agent sur le navigateur de la borne : la déconnexion d'un agent désactiverait aussi la borne.
- **Impression** : par le serveur sur l'imprimante réseau de la borne (voir « Impression des tickets »). **Testé avec une imprimante simulée ; à valider avec l'imprimante réelle.** Tablette Android : épingler l'écran ou utiliser un navigateur en mode kiosque pour que le patient ne quitte pas la page.
- **Écran principal (TV)** : `http://<serveur>/circuit/ecran/`, activé avec la clé de l'écran. Pour un même ticket, seul le dernier appel est affiché. Cliquer une fois sur « Activer le son » (les navigateurs bloquent le son sans action de l'utilisateur) : carillon et annonce vocale si une voix française est installée sur l'appareil. **Son non vérifié sur la TV réelle.**
- **Agents** : `http://<serveur>/` → connexion individuelle → choix du centre → interface commune du service.

La borne et l'écran n'acceptent que les adresses du réseau local (`RESEAU_LOCAL_UNIQUEMENT=True`).

## Sauvegarde et restauration

- `scripts\sauvegarde.ps1 -Dossier D:\sauvegardes -CopieVers E:\sauvegardes` : `pg_dump` compressé, archive vérifiée, copie sur un second support, conservation des 30 dernières. À planifier chaque jour (Planificateur de tâches Windows).
- `scripts\test_restauration.ps1` : restaure la dernière sauvegarde dans une **base distincte** (`gfa_restauration_test`), compare le nombre de lignes de chaque table avec la production, puis supprime la base de test. La production n'est jamais modifiée.
- Sous Linux : mêmes commandes `pg_dump -Fc` / `createdb` / `pg_restore --no-owner` via cron.

## Tâches planifiées

- `python manage.py cloturer_tickets` à **20 h 05** (clôture à 20 h 00) et une seconde fois la nuit, par sécurité (crontab, voir la commande). Ce n'est plus indispensable : l'application clôture aussi d'elle-même à la première page ouverte.
- Sauvegarde PostgreSQL quotidienne : sur Ubuntu, `pg_dump -Fc` via cron et copie sur un second support ; test de restauration au moins une fois par mois (équivalents des scripts `scripts/*.ps1` écrits pour Windows).

## Sécurité et intégrité

- Le centre actif vient toujours de la session, jamais du navigateur ; chaque objet est relu dans le centre actif (404 sinon). Seul l'administrateur central (superutilisateur ou type « Administrateur central ») a une vue transversale.
- Droits vérifiés côté serveur avec `comptes.permissions.a_droit(utilisateur, permission, centre, service)`.
- Administration Django filtrée par centre pour les comptes non centraux ; les champs donnant des droits transversaux (superutilisateur, type de compte, groupes) ne leur sont pas modifiables.
- Triggers PostgreSQL : cohérence de `centre_id` entre tables liées ; `circuit_evenement` et `audit_journalaudit` refusent UPDATE et DELETE. En production, retirer aussi ces droits au compte de l'application :
  `REVOKE UPDATE, DELETE ON circuit_evenement, audit_journalaudit FROM <compte_app>;` (le propriétaire des tables doit être un autre rôle).
- Contraintes : numérotation unique par centre/préfixe/jour, un patient tenu par agent, un passage actif par ticket, une réservation de quota active par ticket.
- Les comptes ne sont jamais supprimés (désactivation seulement).
- Connexion : après 5 échecs pour un même identifiant, blocage de 15 minutes (`CONNEXION_MAX_ECHECS`, `CONNEXION_BLOCAGE_MINUTES`) ; les échecs sont comptés dans le journal d'audit et remis à zéro par une connexion réussie.

## Tests

```bash
python manage.py test --noinput
```

Tests sur PostgreSQL (base de test créée automatiquement) : comptes et permissions, isolation entre centres, numérotation et appels simultanés, recette des quatre parcours par les interfaces avec et sans pharmacie, mode Autre, quotas, absences et réintégrations, chronométrage à horloge contrôlée, borne, écran, audit, statistiques et exports, absence de toute information patient.
