# Application Android de la borne (dernier recours)

> **À n'utiliser qu'en dernier recours.** La solution retenue reste l'option A :
> la tablette ouvre la page web de la borne dans Chrome, et c'est le serveur qui
> imprime sur une imprimante thermique **réseau** (Ethernet ou Wi-Fi). Cette
> application ne sert que si l'on doit utiliser une imprimante **Bluetooth**.

## Ce que fait l'application

- Elle affiche en plein écran la page de la borne du serveur (`…/circuit/borne/`).
  Toute la logique (services, tickets, quotas) reste sur le serveur : **aucune mise à
  jour de l'application n'est nécessaire quand le logiciel évolue**.
- Elle imprime le ticket sur une imprimante thermique **Bluetooth** déjà appairée
  (ESC/POS, profil série SPP). Le serveur prépare le ticket ; l'application ne fait
  que l'envoyer à l'imprimante.
- Elle verrouille la borne : pas de bouton Retour, plein écran, épinglage de l'écran
  en option.
- Si le serveur ne répond pas, elle affiche « Borne momentanément indisponible »
  et réessaie toute seule toutes les 10 secondes.

C'est une application légère : 4 fichiers Java, aucune bibliothèque externe, Android 7.0 minimum.

## Obtenir l'APK (sans rien installer sur le PC)

L'APK est compilé automatiquement par GitHub à chaque modification du dossier
`borne_android/` :

1. Ouvrir le dépôt GitHub, onglet **Actions**, puis **APK borne Android**.
2. Choisir la dernière exécution réussie (coche verte).
3. En bas, dans **Artifacts**, télécharger **borne-funtoa-apk** (un .zip qui contient `app-release.apk` si la clé stable est configurée, sinon `app-debug.apk`).

Pour relancer la compilation à la main : **Actions → APK borne Android → Run workflow**.

Avec Android Studio, on peut aussi ouvrir le dossier `borne_android/`, puis lancer
*Build → Build APK(s)*.

## Installer sur la tablette

1. Copier `app-debug.apk` sur la tablette (câble USB, clé USB ou Bluetooth).
   **Serveur en HTTPS (production)** : installer d'abord le certificat de l'autorité locale
   du centre (`ca.crt`, docs/DEPLOIEMENT_UBUNTU.md, étape 6) : Paramètres → Sécurité →
   Chiffrement → Installer un certificat → **Certificat CA**. L'application l'accepte
   (`res/xml/network_security_config.xml`) ; sans lui, la borne reste « momentanément indisponible ».
2. L'ouvrir et accepter « Installer des applications de sources inconnues ».
3. **Appairer l'imprimante** : Paramètres Android → Bluetooth → choisir l'imprimante
   (code habituel : `0000` ou `1234`).
4. Ouvrir **Borne FUNTOA**. L'écran des réglages s'affiche au premier lancement :
   - **Adresse du serveur** : en production `https://192.168.1.10/circuit/borne/`
     (HTTPS, sans port) ; en développement `http://192.168.1.10:8000/circuit/borne/` ;
   - **Imprimante** : la choisir dans la liste, régler le papier (80 ou 58 mm),
     puis toucher **Imprimer un test** ;
   - **Accents** : si les accents du test sortent mal, choisir « Sans accents » ;
   - **Code des réglages** : 4 chiffres ou plus, à garder pour le personnel ;
   - **Épingler l'écran** : conseillé en service.
5. **Enregistrer et ouvrir la borne**, puis l'activer : un administrateur se connecte
   sur la tablette, choisit la borne et l'active (sans clé). Elle reste activée.

**Côté serveur** : ne rattacher **aucune imprimante réseau** à cette borne
(Administration → Bornes). Sinon c'est le serveur qui imprime, et l'application ne fait rien.

## Mises à jour sans désinstaller (clé de signature stable)

Sans clé stable, chaque APK compilé par GitHub est signé avec une clé différente : Android refuse alors la mise à jour, et il faut désinstaller. Les réglages et l'activation de la borne sont alors perdus.

Une seule fois, créer la clé sur un poste avec Java installé :

```bash
keytool -genkeypair -v -keystore borne.jks -alias borne -keyalg RSA -keysize 2048 -validity 10000
```

Puis, dans GitHub (Settings → Secrets and variables → Actions), créer quatre secrets :

| Secret | Valeur |
|---|---|
| `BORNE_KEYSTORE_BASE64` | contenu de `borne.jks` en base64 (`base64 -w0 borne.jks`, ou `certutil -encode` sous Windows) |
| `BORNE_KEYSTORE_MOT_DE_PASSE` | mot de passe du fichier |
| `BORNE_CLE_ALIAS` | `borne` |
| `BORNE_CLE_MOT_DE_PASSE` | mot de passe de la clé |

Gardez `borne.jks` et ses mots de passe en lieu sûr, hors du dépôt : sans eux, plus aucune mise à jour ne pourra s'installer par-dessus.

Une fois les secrets créés, l'APK s'appelle `app-release.apk`. Le numéro de version augmente à chaque compilation. Le premier passage de la version de debug à la version signée demande une dernière désinstallation.

## Redémarrage de la tablette

- Après une coupure de courant, l'application se relance seule sur Android 9 et moins.
- Sur Android 10 et plus, le système limite ce lancement. Choisissez **Borne FUNTOA** comme **écran d'accueil par défaut** : Paramètres → Applications → Applications par défaut → Application d'accueil. La borne s'ouvre alors à chaque démarrage, et le bouton Accueil y ramène.

## Code des réglages

Il n'est conservé sur la tablette que sous forme d'empreinte, jamais en clair. Dans les réglages, laissez le champ vide pour garder le code actuel. Un code oublié se règle en effaçant les données de l'application (Paramètres Android → Applications → Borne FUNTOA → Stockage).

## Au quotidien

- **Revenir aux réglages** : toucher 5 fois de suite le coin haut-gauche de l'écran, puis saisir le code.
- **Sortir de la borne** (maintenance) : dans les réglages, **Quitter la borne**.
  Si l'écran est épinglé, Android peut aussi demander d'appuyer longuement sur Retour et Aperçu.
- **Imprimante éteinte ou hors de portée** : la borne affiche « Notez votre numéro »
  et propose « Réessayer l'impression ». Le ticket est bien enregistré. L'accueil voit
  une alerte, qui disparaît à la prochaine impression réussie.

## Mise à jour de l'application

L'APK de GitHub est signé avec une clé de « debug » qui change d'une compilation à l'autre.
Pour installer une nouvelle version, il faut donc **désinstaller l'ancienne**, puis
refaire les réglages et l'activation de la borne. Les mises à jour seront rares,
puisque tout le fonctionnement est sur le serveur. Si elles deviennent fréquentes,
créer une clé de signature et la déclarer dans les secrets GitHub.

## Fichiers

| Fichier | Rôle |
|---|---|
| `app/src/main/java/.../MainActivity.java` | Affichage de la borne, verrouillage, lien avec la page ticket |
| `app/src/main/java/.../ImprimanteBluetooth.java` | Envoi du ticket à l'imprimante Bluetooth |
| `app/src/main/java/.../ReglagesActivity.java` | Écran des réglages |
| `app/src/main/java/.../Reglages.java` | Enregistrement des réglages sur la tablette |
| `app/src/main/res/` | Écran des réglages, couleurs (celles de l'application web), icône |
| `../.github/workflows/apk-borne.yml` | Compilation automatique de l'APK |

**Côté serveur**, deux éléments complètent l'application :

- la vue `circuit.views_postes.borne_ticket_escpos` (`/circuit/borne/ticket/<id>/escpos/`). Elle
  fournit le ticket au format ESC/POS, seulement à la borne qui l'a émis, pendant
  3 minutes, et seulement si aucune imprimante réseau n'est rattachée à cette borne ;
- la vue `borne_impression_android` (`/circuit/borne/ticket/<id>/impression-android/`). Elle inscrit
  le résultat de l'impression dans l'historique du ticket, pour l'alerte de l'accueil ;
- le script de `circuit/templates/circuit/borne_ticket.html`. Quand la page s'ouvre
  dans l'application, il imprime par Bluetooth au lieu d'ouvrir la fenêtre d'impression du navigateur.

## État

Le code est écrit. La compilation est vérifiée par GitHub Actions. **Il n'a pas encore été testé
sur une vraie tablette ni avec une vraie imprimante Bluetooth : à valider sur site.**
