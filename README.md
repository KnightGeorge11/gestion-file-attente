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
- **Pharmacie : jamais automatique ni obligatoire.** Tout service de soins (infirmerie, médecin, dentiste, échographie) peut décider explicitement de l'envoyer à la pharmacie (« Transférer à la pharmacie ») ; le ticket se termine après la pharmacie. Depuis une étape intermédiaire (ex. infirmerie du parcours M), un **motif est obligatoire** et les étapes restantes ne sont pas réalisées (décision du 20/11/2026). Jamais depuis l'accueil (décision du 18/11/2026). Aucune version de parcours ne contient la pharmacie.
- **Clôture** à **23 h 59** des tickets restés ouverts et **remise à zéro des numéros** (décision du 21/11/2026, heure réglable par centre). Elle est **automatique** : dès qu'une page de la borne, d'un guichet ou de l'écran est ouverte, les tickets à clôturer sont fermés (vérification au plus une fois par minute). La commande `cloturer_tickets` reste disponible en tâche planifiée de sécurité. Après l'heure de clôture, la borne affiche « Tickets terminés pour aujourd'hui » et ne délivre plus de ticket jusqu'à minuit (audit du 20/11/2026).
- **10 secondes** minimum entre deux appels du même patient ; absence après 3 appels.
- **2 agents minimum par service** (décision du 18/11/2026) : chaque file accepte au moins 2 appels simultanés ; chaque agent ne tient qu'un patient à la fois.
- Tickets « Autre » : préfixe **A** avec leur propre compteur.
- **Deux types d'administrateur** (décision du 19/11/2026) :
  - **administrateur de la plateforme** (type de compte « Administrateur de la plateforme », ou superutilisateur ; ex. `tedy`) : tous les centres, tous les écrans de l'administration (centres, rôles, journal d'audit compris) ;
  - **administrateur de centre** (type de compte « Administrateur de centre » + affectation au centre avec le rôle « Administrateur de centre ») : l'administration de **son centre seulement** (bornes, écrans, imprimantes, services, files, motifs « Autre », parcours, quotas, comptes et affectations du centre). Il ne voit ni les autres centres, ni les centres eux-mêmes, ni le journal d'audit, et ne peut pas modifier le compte d'un administrateur de la plateforme. Seul l'administrateur de la plateforme peut créer un administrateur de centre. Il ne peut ni modifier sa propre affectation, ni ajouter d'autres permissions que les permissions métier (appeler, orienter, quotas…), ni gérer un compte rattaché à un centre qu'il n'administre pas ; il n'administre que les centres où son affectation lui en donne le droit (pas ceux où il n'est qu'agent). Les rôles sont réservés à l'administrateur de la plateforme (audit du 20/11/2026).
- **L'accueil délivre les tickets** (décision du 21/11/2026, case « L'accueil délivre les tickets » du centre, cochée par défaut) : la borne est à l'accueil, tout le monde y vient prendre son ticket pour un service, puis va **directement au premier service de son parcours** : plus de file d'attente à l'accueil pour les soins. Le ticket imprimé indique le service (« Présentez-vous au service Infirmerie »). Dentiste : la place de quota est réservée à l'émission du ticket ; quota atteint, la borne refuse le ticket (aucun numéro consommé). Les demandes « Autre » passent toujours par l'accueil, qui les oriente. Case décochée : circuit précédent (borne libre-service, puis file de l'accueil).
- **Tout passe d'abord par l'accueil** (décision du 19/11/2026) : seul l'accueil appelle et oriente les tickets qui l'attendent, **y compris les « Autre »**. Un service n'appelle que les patients que l'accueil lui a orientés. Les administrateurs (plateforme et centre), les responsables et la direction **n'appellent aucun ticket** (refus côté serveur, aucun bouton d'appel) : l'accueil leur transmet les demandes « Autre » qui les concernent (Notifications). **Appel du visiteur à l'écran** (décision du 19/11/2026) : le destinataire d'une demande « Autre » orientée par l'accueil (une personne, un agent désigné d'un service) peut, depuis ses Notifications, appeler le visiteur sur l'écran principal ; l'écran affiche le numéro (ex. A003) et la destination (le service, sinon le rôle du destinataire, ex. « Direction ») avec le bureau saisi. Possible tant que la demande n'est pas clôturée, le jour même, 10 s minimum entre deux appels. La **direction et l'administrateur de centre ne voient que les tickets « Autre »** (aucun patient). **Dérogation au quota dentiste** : l'agent d'accueil peut déroger directement en validant le ticket (case « Dérogation exceptionnelle » et motif obligatoire, décision du 20/11/2026) ; un responsable peut aussi l'autoriser à l'avance depuis la page du ticket (confirmé le 19/11/2026). Dans les deux cas, la réservation est marquée « dérogation », avec le motif et le nom de l'auteur, dans l'historique.
- Chaque service appelle ses patients ; **un écran principal unique affiche simultanément les appels de plusieurs services** ; la pharmacie peut appeler sur l'écran ou oralement.
- File commune des médecins : le patient appelé est attribué au médecin connecté qui l'appelle (verrou, pas de double prise).
- Comptes individuels pour chaque agent ; interface commune par service.
- **Chronométrage** de l'attente et de la prise en charge à chaque passage, et durée totale du ticket.
- Absence seulement après **3 appels** sans réponse ; réintégration sans effacer l'historique.
- **Aucune information sur le patient n'est collectée** (décision du 18/11/2026) : le système fonctionne uniquement avec les numéros de ticket. L'accueil appelle le ticket, le prend en charge puis le **valide** et l'oriente vers son service. Il n'y a ni fiche patient, ni identification, ni règle « un seul ticket par patient ».
- **Quotas dentiste** : 7 le matin et 4 l'après-midi du lundi au vendredi, 4 le samedi matin ; bascule à 12 h 00 ; fermé le samedi après-midi et le dimanche. Place réservée quand l'**accueil valide le ticket** (avant l'orientation vers le dentiste) ; blocage quand le quota est atteint ; **dérogation** par l'accueil (à la validation) ou par un responsable, avec motif obligatoire, historisée ; **libération** manuelle avec motif obligatoire, historisée. Patient dont la place a été libérée et qui revient quand même (décision du 20/11/2026) : à la prise en charge chez le dentiste, il **reprend une place** si le quota en a encore une ; sinon la prise en charge est refusée jusqu'à une dérogation (jamais de soin hors quota). Modification des quotas dans l'administration, historisée (auteur, date, avant/après).
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
| Heure de clôture des tickets | Centre | 23 h 59 |
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
| Droits de la direction | Statistiques et demandes « Autre » transmises par l'accueil (appel du visiteur) ; aucun appel de patient, ni absence ni réintégration |
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
| Accueil | `accueil1`, `accueil2` |
| Infirmerie | `infirmier1`, `infirmier2` |
| Médecins | `medecin1`, `medecin2` |
| Dentiste | `dentiste1`, `dentiste2` |
| Échographie | `echographie1`, `echographie2` |
| Pharmacie | `pharmacie1`, `pharmacie2` |
| Direction (statistiques, demandes « Autre » reçues) | `direction1`, `direction2` |
| Responsable (dérogations, demandes « Autre » reçues) | `responsable1`, `responsable2` |
| Administrateur de centre (administration de son centre) | `admin1`, `admin2` |

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
Serveur : **Ubuntu** (décision du 18/11/2026). Procédure complète (PostgreSQL à deux rôles, Gunicorn `gthread`, Nginx en HTTPS avec une autorité locale, systemd, cron, sauvegardes) : **[docs/DEPLOIEMENT_UBUNTU.md](docs/DEPLOIEMENT_UBUNTU.md)**, fichiers prêts dans `deploiement/`. Procédure écrite, **pas encore exécutée sur le serveur réel**. Le serveur de développement `runserver` ne doit pas être utilisé en production.

### Liste de contrôle sécurité avant la mise en service

- [ ] Mot de passe PostgreSQL fort pour le compte de l'application (l'actuel de développement est trop court).
- [ ] `DJANGO_DEBUG=False`, `DJANGO_SECRET_KEY` aléatoire, `DJANGO_ALLOWED_HOSTS` = IP / nom du serveur.
- [ ] Aucun compte de test (`accueil1`, `medecin2`, `admin1`… mot de passe 1234) sur la base de production : `creer_comptes_test` et `simuler_patients` refusent de s'exécuter si DEBUG est désactivé.
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

**Limite connue** : « Votre ticket s'imprime » signifie que l'imprimante a **reçu** le ticket, pas que le papier est sorti. Sans papier ou capot ouvert, la plupart des imprimantes acceptent quand même les données. Lire l'état de l'imprimante (commande `DLE EOT`) dépend du modèle : ce sera à ajouter après l'achat et l'essai de l'imprimante réelle. En attendant, vérifier le papier à l'ouverture et suivre les alertes de l'accueil. La ponctuation typographique (’, «, —, œ) est translittérée si la table de l'imprimante ne la contient pas, au lieu de sortir en « ? ».

**Tester sans matériel** : `python manage.py imprimante_simulee` (écoute sur 127.0.0.1:9100 et affiche les tickets reçus), puis créer l'imprimante 127.0.0.1:9100 dans l'administration. L'adresse 127.0.0.1 n'est acceptée qu'en développement (`DJANGO_DEBUG=True` ou `IMPRIMANTE_SIMULEE_AUTORISEE=True`) ; en production, seules les adresses du réseau local et les ports 9100 à 9109 le sont.

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
- **Aucun rechargement de page** sur les pages des agents (`hx-boost`) : liens du menu, formulaires (centre, demandes, notifications, actions sur un ticket) et bouton « retour » du navigateur passent par HTMX ; seule la zone de la page change. Exceptions volontaires, chargées normalement : borne, écran principal, connexion, déconnexion, administration Django et exports PDF/Excel. Si la session a expiré ou si la réponse est une page d'erreur, la page est chargée normalement.
- Pages aussi **actualisées automatiquement** : demandes « Autre » et notifications (toutes les 10 s), détail d'un ticket (10 s). L'actualisation attend que l'agent ait fini de saisir et ne referme pas un volet qu'il a ouvert.
- **Nouvelle version prise sans Ctrl+F5** : les fichiers CSS et JavaScript sont appelés avec leur version (`{% static_v %}`), le navigateur recharge donc le nouveau style après une mise à jour.
- **Aucune ressource externe** : polices du système, pas de CDN. Tout fonctionne hors Internet.

## Postes

- **Borne** : `http://<serveur>/circuit/borne/`. **Sans clé** (décision du 21/11/2026) : la première fois, un administrateur (de la plateforme ou du centre) se connecte sur l'appareil, choisit la borne dans la liste, puis il est déconnecté automatiquement. La borne reste ensuite activée sur cet appareil (cookie de 10 ans) : elle ne se déconnecte plus, même si un agent se connecte puis se déconnecte sur le même navigateur. Les bornes activées autrefois avec une clé le restent. Pour retirer une borne : la désactiver ou la supprimer dans l'administration.
- **Services de chaque borne** : l'administrateur de la plateforme choisit, pour chaque borne, les services proposés (Administration → Bornes → « Services proposés »). Aucun coché : tous les services visibles du centre.
- **Impression** : par le serveur sur l'imprimante réseau de la borne (voir « Impression des tickets »). **Testé avec une imprimante simulée ; à valider avec l'imprimante réelle.** Tablette Android : épingler l'écran ou utiliser un navigateur en mode kiosque pour que le patient ne quitte pas la page.
- **Écran de la salle** (décision du 21/11/2026) : un **vrai écran physique relié à l'ordinateur** affiche les appels, pas une fenêtre de navigateur.
  - Menu **Écran** : le système **détecte les écrans reliés à l'ordinateur** (TV ou moniteur en **HDMI**, ou écran **sans fil** connecté par Windows) et affiche les appels **en plein écran** sur celui qu'on choisit. Il s'en souvient : quand l'écran est débranché puis rebranché, les appels s'y réaffichent tout seuls (Chrome ou Edge ; autoriser « gestion des fenêtres » et les fenêtres surgissantes la première fois).
  - **Sans fil** : « Rechercher une TV (Cast) » (la TV affiche elle-même les appels ; ouvrir l'application avec l'adresse réseau de l'ordinateur, pas localhost) ou « Connecter un écran Miracast » (Windows + K). Le Bluetooth ne transporte pas l'image.
  - **Démarrage automatique** : `scripts\ecran_salle.ps1 -Adresse <adresse affichée dans la page Écran>`, à mettre dans le dossier Démarrage de Windows. Il cherche l'écran de la salle (et passe Windows en mode « Étendre » si besoin), y affiche les appels en plein écran sans fenêtre visible sur l'écran de l'ordinateur, les arrête si l'écran est débranché et les réaffiche dès qu'il revient.
  - Aucune clé, rien sur Internet (réseau local uniquement) ; l'affichage ne dépend pas de la session de l'agent. **Non vérifié avec un écran physique réel.**
- **Écran principal (TV autonome)** : `http://<serveur>/circuit/ecran/`, activé avec la clé de l'écran (ancien mode, toujours possible). Chaque appel suit l'état de son passage : il reste dans la zone « Ticket appelé » tant que le service n'a pas terminé (le plus récent en grand, les autres appels en cours en dessous), puis rejoint « Appels précédents » avec son heure et le nom du service. Un même ticket peut donc y figurer plusieurs fois (M001 Accueil, puis M001 Infirmerie) ; un rappel remplace l'appel du même passage. Cliquer une fois sur « Activer le son » (les navigateurs bloquent le son sans action de l'utilisateur) : carillon et annonce vocale si une voix française est installée sur l'appareil. **Son non vérifié sur la TV réelle.**
- **Agents** : `http://<serveur>/` → connexion individuelle → choix du centre → interface commune du service.

- **Heure** (décision du 21/11/2026) : toutes les interfaces affichent l'heure du **serveur**, à l'heure du centre (barre du haut, borne, écran), jamais l'horloge de l'appareil ; elle est recalée à chaque échange avec le serveur. Le serveur doit donc être à l'heure (Ubuntu : `timedatectl set-ntp true`, voir le déploiement).
- **Durée des passages** : la page du ticket affiche, pour chaque service, l'attente, la prise en charge et la **durée du passage** (en direct pour le passage en cours) ; le panneau « Patient en cours » rappelle la durée des passages précédents.

La borne et l'écran n'acceptent que les adresses du réseau local (`RESEAU_LOCAL_UNIQUEMENT=True`). Sur la TV, le son demandé une fois est repris après un rechargement si le navigateur l'autorise (mode kiosque) ; sinon le premier appui sur la télécommande le réactive. Plusieurs appels dans le même intervalle sont tous annoncés, dans l'ordre.

## Sauvegarde et restauration

- `scripts\sauvegarde.ps1 -Dossier D:\sauvegardes -CopieVers E:\sauvegardes` : `pg_dump` compressé, archive vérifiée, copie sur un second support, conservation des 30 dernières. À planifier chaque jour (Planificateur de tâches Windows).
- `scripts\test_restauration.ps1` : restaure la dernière sauvegarde dans une **base distincte** (`gfa_restauration_test`), compare le nombre de lignes de chaque table avec la production, puis supprime la base de test. La production n'est jamais modifiée.
- Sous Ubuntu : `scripts/sauvegarde.sh` et `scripts/test_restauration.sh` (mêmes fonctions), planifiés par `deploiement/crontab`. Restauration réelle : avec le rôle propriétaire, puis refaire le `REVOKE` (voir docs/DEPLOIEMENT_UBUNTU.md).

## Tâches planifiées

- `python manage.py cloturer_tickets` à **23 h 59** et une seconde fois la nuit, par sécurité (crontab, voir la commande). Ce n'est plus indispensable : l'application clôture aussi d'elle-même à la première page ouverte.
- Sauvegarde PostgreSQL quotidienne et test de restauration mensuel : `deploiement/crontab` (scripts `scripts/*.sh`).

## Sécurité et intégrité

- Le centre actif vient toujours de la session, jamais du navigateur ; chaque objet est relu dans le centre actif (404 sinon). Seul l'administrateur central (superutilisateur ou type « Administrateur central ») a une vue transversale.
- Droits vérifiés côté serveur avec `comptes.permissions.a_droit(utilisateur, permission, centre, service)`.
- Administration Django filtrée par centre pour les comptes non centraux ; les champs donnant des droits transversaux (superutilisateur, type de compte, groupes) ne leur sont pas modifiables.
- Triggers PostgreSQL : cohérence de `centre_id` entre tables liées ; `circuit_evenement` et `audit_journalaudit` refusent UPDATE et DELETE. En production, retirer aussi ces droits au compte de l'application :
  `REVOKE UPDATE, DELETE ON circuit_evenement, audit_journalaudit FROM <compte_app>;` (le propriétaire des tables doit être un autre rôle).
- Contraintes : numérotation unique par centre/préfixe/jour, un patient tenu par agent, un passage actif par ticket, une réservation de quota active par ticket.
- Les comptes ne sont jamais supprimés (désactivation seulement).
- Connexion : après 5 échecs pour un même identifiant **depuis le même poste**, blocage de 15 minutes (`CONNEXION_MAX_ECHECS`, `CONNEXION_BLOCAGE_MINUTES`) : quelqu'un qui se trompe ailleurs ne bloque pas le titulaire. Un poste est aussi bloqué après 20 échecs tous identifiants confondus (`CONNEXION_MAX_ECHECS_IP`). Les échecs sont comptés dans le journal d'audit et remis à zéro par une connexion réussie.
- Adresse IP réelle derrière Nginx : en-tête `X-Real-IP`, cru seulement s'il vient d'un proxy listé dans `DJANGO_PROXIES_CONFIANCE`. Elle sert à la restriction réseau local de la borne et de l'écran, au blocage des connexions et au journal d'audit.
- Journal d'audit : connexions, comptes (dont les changements de mot de passe, sans jamais le mot de passe), affectations, rôles et leurs droits, bornes, écrans, imprimantes (dont la régénération de clé), quotas, parcours ; avec l'adresse IP de l'auteur.
- Imprimantes : adresse du réseau local et port 9100 à 9109 uniquement (le serveur ne se connecte jamais ailleurs). Borne : verrou contre le double appui et plafond de tickets par heure (`BORNE_MAX_TICKETS_PAR_HEURE`).

## Tests

```bash
python manage.py test --noinput
```

Tests sur PostgreSQL (base de test créée automatiquement) : comptes et permissions, isolation entre centres, numérotation et appels simultanés, recette des quatre parcours par les interfaces avec et sans pharmacie, mode Autre, quotas, absences et réintégrations, chronométrage à horloge contrôlée, borne, écran, audit, statistiques et exports, absence de toute information patient.
