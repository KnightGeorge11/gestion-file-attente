# Guide de test — démonstration publique

Aide-mémoire pour faire tourner et présenter l'application sur le PC de développement (`D:\gestion_file_attente`), sans Internet.

---

## 1. La veille

- [ ] PC chargé, chargeur dans le sac.
- [ ] Le serveur démarre (voir section 2) et la page http://localhost:8000 s'ouvre.
- [ ] Vous connaissez le mot de passe du compte administrateur (`tedy`). Il sert à régénérer la clé de la borne.
- [ ] Si la tablette ou des téléphones sont utilisés : prévoir le Wi-Fi sur place (voir section 6). **Internet n'est pas nécessaire**, seulement un réseau local commun.
- [ ] Ne **pas** lancer la simulation de patients pendant la démonstration : elle utilise les mêmes comptes de test et peut les occuper.

---

## 2. Le matin du test : démarrage

Dans PowerShell :

```
cd D:\gestion_file_attente
.venv\Scripts\activate
python manage.py cloturer_tickets
python manage.py runserver 0.0.0.0:8000
```

- `cloturer_tickets` est désormais **facultatif** : les tickets de la veille se ferment tout seuls dès qu'on ouvre la borne, un guichet ou l'écran. La commande ne fait pas de mal, et elle affiche combien de tickets ont été fermés.
- Si `activate` est bloqué par PowerShell, utiliser directement `.venv\Scripts\python.exe manage.py ...`.
- Si le port 8000 est déjà utilisé, le serveur tourne déjà : ouvrir simplement http://localhost:8000.
- Laisser cette fenêtre ouverte pendant toute la démonstration (la fermer arrête l'application).

**Facultatif, impression sans imprimante** : dans un deuxième PowerShell (refaire `cd` et `activate`) :

```
python manage.py imprimante_simulee
```

Puis Administration → *Bornes* → « Tablette entrée » → choisir l'imprimante « Thermique entrée (simulée) ». Les tickets s'affichent dans cette fenêtre. Penser à retirer l'imprimante de la borne après le test, sinon chaque ticket affichera « Notez votre numéro ».

---

## 3. Préparer les postes

Utiliser le **centre 002** (le plus propre pour une démonstration).

| Poste | Où l'ouvrir | Connexion |
|---|---|---|
| **Borne** (le patient) | Tablette, ou un **autre navigateur** que celui des agents (ex. Edge) | http://localhost:8000/circuit/borne/ — centre `002`, poste `TABLETTE`, clé |
| **Accueil** | Navigateur principal | `accueil1` / `1234`, centre 002 |
| **Service** (infirmerie, médecin…) | Autre onglet ou autre appareil | ex. `medecin1` / `1234` |
| **Écran TV** | Onglet plein écran (F11) | http://localhost:8000/circuit/ecran/ (connecté avec un compte du centre 002) |
| **Statistiques** | En fin de démonstration | `direction1` / `1234` |

**Clé de la borne** : elle n'est affichée qu'une fois. Administration (http://localhost:8000/admin/) → *Bornes* → cocher « Tablette entrée » → action **« Régénérer la clé »** → copier la clé du message vert.

**Activer / désactiver la borne** : Administration → *Bornes* → bouton **Désactiver** (ou **Activer**) sur la ligne, en un clic. Une borne désactivée affiche « Borne momentanément fermée » et reprend toute seule, en 20 secondes au plus, dès qu'on la réactive (pas besoin de ressaisir la clé).

**Important** : un navigateur ne garde qu'une session. Si vous vous connectez comme agent dans la fenêtre de la borne, la borne se désactive. Gardez la borne dans un navigateur à part (ou sur la tablette). Une fenêtre de navigation privée perd la borne quand on la ferme.

Tous les comptes de test ont le mot de passe **1234** :
`accueil1/2`, `infirmier1/2`, `medecin1/2`, `dentiste1/2`, `echographie1/2`, `pharmacie1/2`, `direction1/2` (statistiques), `responsable1/2` (dérogations). `admin1/2` (administration du centre). **Tout passe d'abord par l'accueil** : seul l'accueil appelle et oriente les tickets (y compris les « Autre ») ; responsables, direction et administrateurs n'appellent aucun ticket.

---

## 4. Scénario de démonstration (environ 15 minutes)

**A. Parcours médecin, avec pharmacie**
1. Borne : *Interne* → *Médecin* → écran de confirmation → **Prendre mon ticket** → ticket **M00x**. En bas de la borne, l'état de l'imprimante est affiché.
2. Accueil : le ticket apparaît en quelques secondes (surligné), les chiffres du jour se mettent à jour → **Appeler le suivant** (montrer l'écran TV) → **Prendre en charge** → **Valider le ticket et orienter vers Infirmerie**.
3. Montrer, avec `medecin1`, que le médecin voit déjà ce ticket dans **« À venir »**.
4. Infirmerie (`infirmier1`) : appeler → prendre en charge → **Terminer et orienter vers Médecins**. (L'infirmerie peut aussi envoyer à la pharmacie, avec un motif obligatoire : le patient ne verra alors pas le médecin.)
5. Médecin (`medecin1`) : appeler → prendre en charge → ouvrir **« Transférer à la pharmacie »** → terminer.
6. Pharmacie (`pharmacie1`) : **Appel oral** (prise en charge directe) → Terminer. Le ticket est fini.
7. Ouvrir l'**historique** du ticket (lien « historique ») : attente et durée de chaque étape.

**B. Parcours direct** : Borne → *Externe* → *Échographie* → **Prendre mon ticket** → accueil valide → échographie (`echographie1`) appelle.

**C. Dentiste et quota** : Borne → *Dentiste*. Avant de valider, l'accueil voit le quota (7 places le matin, 4 l'après-midi ; ex. « 2 réservée(s) sur 7 · 5 restante(s) »). Quota atteint : l'agent d'accueil coche **Dérogation exceptionnelle**, saisit le motif (obligatoire) et valide le ticket. Un responsable (`responsable1`) peut aussi l'autoriser à l'avance depuis la page du ticket (Tickets → numéro → **Autoriser une dérogation au quota**).

**D. Mode Autre** : Borne → *Autre* → *Visite d'embauche* → **Prendre mon ticket** → ticket **A00x**. Accueil : appeler, prendre en charge → **Orienter la demande** → choisir une personne ou un service (au centre 002, chaque service a un agent désigné : `…1`) → revenir à l'accueil → **Terminer**. Le destinataire peut **appeler le visiteur à l'écran** depuis **Notifications** (numéro A00x + « Direction » ou le service sur l'écran TV), et répond (statut + message), et l'accueil voit la réponse dans « Demandes ».

**E. Deux agents en même temps** : `accueil1` et `accueil2` appellent chacun un ticket différent (2 agents minimum par service).

**F. Statistiques** : `direction1` → *Statistiques* → export PDF ou Excel. Aucune donnée patient.

---

## 5. Si quelque chose bloque

| Ce qui s'affiche | Cause | Solution |
|---|---|---|
| « Vous avez déjà un ticket en cours… » | L'agent tient déjà un ticket (un seul à la fois, tous centres) | Terminer ce ticket, ou utiliser l'autre compte (`…2`) |
| On ne peut appeler que le premier | Ordre strict de la file | Normal : appeler dans l'ordre d'arrivée |
| « Veuillez patienter … s avant de rappeler » | 10 s minimum entre deux appels | Attendre quelques secondes |
| « Marquer absent » n'apparaît pas | Il faut 3 appels sans réponse | Rappeler 2 fois (10 s d'écart) |
| Validation refusée pour un ticket dentiste | Quota atteint ou service fermé (samedi après-midi, dimanche) | Dérogation avec motif (accueil ou responsable), ou annuler le ticket |
| « Orientez d'abord la demande » | Ticket « Autre » pas encore orienté | Cliquer **Orienter la demande** |
| Borne : « Code ou clé incorrect » | Clé régénérée ou espace en trop | Régénérer la clé (section 3) |
| Borne : « Accès refusé » | Adresse hors réseau local | Utiliser localhost ou l'IP locale du PC |
| Écran TV muet | Les navigateurs bloquent le son | Cliquer une fois « Activer le son » |
| Une fenêtre « Imprimer » s'ouvre | Pas d'imprimante réseau sur la borne | Annuler la fenêtre, ou utiliser l'imprimante simulée |
| Tickets d'hier dans les files | Page ouverte depuis moins d'une minute (vérification une fois par minute) | Recharger la page une minute plus tard, ou `python manage.py cloturer_tickets` |
| Page qui ne répond plus | Serveur arrêté | Relancer `python manage.py runserver 0.0.0.0:8000` |

Tickets fermés automatiquement à **20 h 00** (clôture).

---

## 6. Avec une tablette ou des téléphones

1. PC et appareils sur le **même Wi-Fi**.
2. Trouver l'adresse IP du PC sur place (elle change d'un réseau à l'autre) : `ipconfig` → « Adresse IPv4 » (ex. 192.168.1.25).
3. Dans le fichier `.env`, l'ajouter : `DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,192.168.1.25`, puis relancer le serveur.
4. Si Windows demande l'autorisation du pare-feu pour Python : accepter pour le réseau privé.
5. Sur la tablette : `http://192.168.1.25:8000/circuit/borne/` ; épingler l'écran (mode kiosque Android) pour que le public ne quitte pas la page.

---

## 7. À dire au public (ce qui n'est pas encore là)

- **Aucune information patient** n'est collectée : c'est voulu, tout fonctionne avec le numéro de ticket.
- L'imprimante thermique réelle n'est pas encore achetée : impression testée avec une imprimante simulée.
- Le serveur Ubuntu du centre n'est pas encore installé : la démonstration tourne sur ce PC.

---

## 8. Après le test

- [ ] Noter les remarques du public et du personnel → carte Trello « [P0] Tests automatisés et recette ».
- [ ] `python manage.py cloturer_tickets` pour fermer les tickets restés ouverts.
- [ ] Retirer l'imprimante simulée de la borne si elle a été utilisée.
- [ ] Arrêter le serveur (Ctrl+C dans la fenêtre PowerShell).
