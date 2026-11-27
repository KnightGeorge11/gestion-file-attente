/* ==========================================================================
   SMIE FUNTOA · Files d'attente — comportements communs (sans dépendance,
   en complément de HTMX).

   - notifications discrètes (messages Django affichés en « toasts ») ;
   - durées et chronomètres mis à jour chaque seconde ([data-depuis]) ;
   - horloges ([data-horloge]) ;
   - mise en évidence des nouveaux éléments d'une liste rafraîchie
     ([data-suivi] contenant des [data-id]) ;
   - barre de chargement et état de connexion pendant les requêtes HTMX.

   L'heure de référence est celle du serveur (attribut data-maintenant du
   <body>) : une tablette dont l'horloge est fausse affiche quand même des
   durées justes.
   ========================================================================== */
(function () {
  "use strict";

  var decalage = 0;   // heure serveur - heure de l'appareil (ms)

  function maintenant() { return Date.now() + decalage; }

  function initialiserHeure() {
    var serveur = document.body && document.body.dataset.maintenant;
    if (serveur) {
      var t = Date.parse(serveur);
      if (!isNaN(t)) { decalage = t - Date.now(); }
    }
  }

  /* ---------------- Notifications (toasts) ---------------- */

  function zoneToasts() {
    var zone = document.getElementById("toasts");
    if (!zone) {
      zone = document.createElement("div");
      zone.id = "toasts";
      zone.className = "toasts";
      zone.setAttribute("role", "status");
      zone.setAttribute("aria-live", "polite");
      document.body.appendChild(zone);
    }
    return zone;
  }

  function toast(texte, genre) {
    var element = document.createElement("div");
    element.className = "toast " + (genre || "info");
    // Les erreurs sont annoncées immédiatement par les lecteurs d'écran.
    if (genre === "error") { element.setAttribute("role", "alert"); }
    var icone = {success: "check-cercle", error: "alerte", warning: "alerte"}[genre] || "info";
    element.innerHTML = '<svg class="ic" aria-hidden="true"><use href="#i-' + icone + '"></use></svg>' +
      '<span></span><button type="button" class="toast-fermer" aria-label="Fermer">' +
      '<svg class="ic" aria-hidden="true"><use href="#i-croix"></use></svg></button>';
    element.querySelector("span").textContent = texte;
    element.querySelector("button").addEventListener("click", function () { fermer(element); });
    zoneToasts().appendChild(element);
    setTimeout(function () { fermer(element); }, genre === "error" ? 9000 : 5000);
  }

  function fermer(element) {
    if (!element.parentNode) { return; }
    element.classList.add("sortie");
    setTimeout(function () { if (element.parentNode) { element.parentNode.removeChild(element); } }, 250);
  }

  /** Les messages Django de la page deviennent des notifications. */
  function messagesEnToasts(racine) {
    // Borne : le patient lit le message en grand dans la page (24/11/2026).
    if (document.body.classList.contains("borne")) { return; }
    var listes = (racine || document).querySelectorAll("ul.messages[data-toasts]");
    listes.forEach(function (liste) {
      liste.querySelectorAll("li").forEach(function (li) {
        var genre = ["success", "error", "warning"].filter(function (g) { return li.classList.contains(g); })[0];
        toast(li.textContent.trim(), genre || "info");
        li.parentNode.removeChild(li);
      });
    });
  }
  window.notifier = toast;

  /* ---------------- Durées et horloges ---------------- */

  function formaterDuree(secondes, format) {
    secondes = Math.max(0, Math.floor(secondes));
    var h = Math.floor(secondes / 3600), m = Math.floor((secondes % 3600) / 60), s = secondes % 60;
    if (format === "chrono") {
      return (h ? h + ":" + String(m).padStart(2, "0") : String(m)) + ":" + String(s).padStart(2, "0");
    }
    if (h) { return h + " h " + String(m).padStart(2, "0"); }
    if (m) { return m + " min"; }
    return "< 1 min";
  }

  function majDurees() {
    var t = maintenant();
    document.querySelectorAll("[data-depuis]").forEach(function (el) {
      var debut = Date.parse(el.dataset.depuis);
      if (isNaN(debut)) { return; }
      var secondes = (t - debut) / 1000;
      el.textContent = formaterDuree(secondes, el.dataset.format);
      if (el.dataset.alerteMinutes) {
        el.classList.toggle("depasse", secondes >= Number(el.dataset.alerteMinutes) * 60);
      }
    });
    document.querySelectorAll("[data-horloge]").forEach(function (el) {
      var options = {timeZone: el.dataset.fuseau || undefined};
      if (el.dataset.horloge === "date") {
        options.weekday = "long"; options.day = "numeric"; options.month = "long"; options.year = "numeric";
        el.textContent = new Date(t).toLocaleDateString("fr-FR", options);
      } else {
        options.hour = "2-digit"; options.minute = "2-digit";
        if (el.dataset.horloge === "secondes") { options.second = "2-digit"; }
        el.textContent = new Date(t).toLocaleTimeString("fr-FR", options);
      }
    });
  }

  /* ---------------- Nouveaux éléments d'une liste ---------------- */

  var dejaVus = {};

  function suivreNouveaux(racine) {
    (racine || document).querySelectorAll("[data-suivi]").forEach(function (liste) {
      var cle = liste.dataset.suivi;
      var connus = dejaVus[cle];
      var actuels = {};
      liste.querySelectorAll("[data-id]").forEach(function (el) {
        actuels[el.dataset.id] = true;
        if (connus && !connus[el.dataset.id]) { el.classList.add("arrivee"); }
      });
      dejaVus[cle] = actuels;
    });
  }

  /* ---------------- HTMX : chargement, erreurs, connexion ---------------- */

  function estActionUtilisateur(detail) {
    // Les rafraîchissements périodiques ne montrent pas la barre de chargement.
    var declencheur = detail && detail.requestConfig && detail.requestConfig.triggeringEvent;
    return !!(declencheur && declencheur.type !== "every" && declencheur.type !== "load" && detail.elt &&
      (detail.elt.tagName === "FORM" || detail.elt.tagName === "BUTTON" || detail.elt.tagName === "A"));
  }

  var enCours = 0;
  function barre(actif) {
    var b = document.getElementById("barre-chargement");
    if (!b) { return; }
    enCours = Math.max(0, enCours + (actif ? 1 : -1));
    b.classList.toggle("actif", enCours > 0);
  }

  function horsLigne(etat) {
    document.body.classList.toggle("hors-ligne", etat);
  }

  document.addEventListener("htmx:beforeRequest", function (e) {
    if (estActionUtilisateur(e.detail)) {
      e.detail.elt.classList.add("en-cours");
      barre(true);
      e.detail.elt.dataset.barre = "1";
    }
  });
  /* Heure exacte : à chaque réponse du serveur, l'heure affichée est recalée
     sur la sienne (en-tête Date). Les écrans restés ouverts des jours (TV,
     borne) ne dérivent pas, même si l'horloge de l'appareil est fausse. */
  function recalerHeure(xhr) {
    var date = xhr && xhr.getResponseHeader && xhr.getResponseHeader("Date");
    var t = date ? Date.parse(date) : NaN;
    if (isNaN(t)) { return; }
    var ecart = (t + 500) - maintenant();   // l'en-tête est à la seconde près
    if (Math.abs(ecart) > 2000) { decalage += ecart; }
  }
  document.addEventListener("htmx:afterRequest", function (e) {
    recalerHeure(e.detail.xhr);
    var elt = e.detail.elt;
    if (elt && elt.dataset && elt.dataset.barre) {
      delete elt.dataset.barre;
      elt.classList.remove("en-cours");
      barre(false);
    }
    if (e.detail.successful) { horsLigne(false); erreurSignalee = false; }
  });
  document.addEventListener("htmx:sendError", function () {
    horsLigne(true);
  });
  /* Une erreur des rafraîchissements automatiques (écran TV, files…) n'est
     signalée qu'une fois, jusqu'au prochain succès : pas de messages en boucle
     toutes les 3 s quand le serveur est en panne. Les actions de l'agent sont
     toujours signalées. */
  var erreurSignalee = false;
  document.addEventListener("htmx:responseError", function (e) {
    var statut = e.detail.xhr ? e.detail.xhr.status : 0;
    if (!estActionUtilisateur(e.detail)) {
      if (erreurSignalee) { return; }
      erreurSignalee = true;
    }
    if (statut === 403) {
      toast("Action non autorisée. Votre session a peut-être expiré : rechargez la page.", "error");
    } else if (statut >= 500) {
      toast("Le serveur a rencontré une erreur. Réessayez ; si cela persiste, prévenez l'administrateur.", "error");
    }
  });
  /* Navigation sans rechargement (hx-boost) : quand la réponse est une page
     d'un autre type (connexion après expiration de la session, borne, page
     d'erreur…) ou qu'une zone rafraîchie reçoit une page complète, on charge
     la page normalement plutôt que de l'insérer au mauvais endroit. */
  function classeBody(html) {
    var m = /<body[^>]*\sclass="([^"]*)"/i.exec(html || "");
    return m ? m[1].trim() : null;
  }
  document.addEventListener("htmx:beforeSwap", function (e) {
    var xhr = e.detail.xhr;
    if (!xhr) { return; }
    var html = xhr.responseText || "";
    var pageComplete = /<body[\s>]/i.test(html);
    var adresse = xhr.responseURL || window.location.href;
    if (e.detail.boosted) {
      var classeActuelle = document.body.className.replace(/\bhors-ligne\b/, "").trim();
      var verbe = ((e.detail.requestConfig && e.detail.requestConfig.verb) || "get").toLowerCase();
      if (xhr.status >= 400 && verbe !== "get") {
        // Erreur après l'envoi d'un formulaire : l'adresse de la réponse est
        // une vue réservée à l'envoi (POST). La recharger donnerait une erreur
        // 405 et une page blanche : on recharge la page courante, avec le
        // message d'erreur.
        e.detail.shouldSwap = false;
        try {
          sessionStorage.setItem("toast-apres-rechargement", xhr.status === 403
            ? "Action non autorisée. Votre session a peut-être expiré."
            : "L'action n'a pas pu être enregistrée. Réessayez.");
        } catch (ignore) {}
        window.location.reload();
      } else if (xhr.status >= 400 || (pageComplete && classeBody(html) !== classeActuelle)) {
        e.detail.shouldSwap = false;
        window.location.href = adresse;
      }
    } else if (pageComplete && e.detail.target !== document.body) {
      // Une zone rafraîchie reçoit une page entière : normal avec hx-select
      // (seule la zone est reprise), sauf si le serveur a redirigé ailleurs
      // (ex. session expirée -> page de connexion).
      var avecSelection = e.detail.elt && e.detail.elt.closest && e.detail.elt.closest("[hx-select]");
      var demande = e.detail.requestConfig && e.detail.requestConfig.path;
      var redirige = demande && xhr.responseURL &&
        new URL(xhr.responseURL, window.location.href).pathname !== new URL(demande, window.location.href).pathname;
      if (!avecSelection || redirige) {
        e.detail.shouldSwap = false;
        window.location.reload();
      }
    }
  });

  /* Les rafraîchissements automatiques attendent que l'agent ait fini de
     saisir : utilisé dans hx-trigger="every 10s [pasDeSaisie()]". */
  function champModifie(champ) {
    if (champ.type === "hidden" || champ.type === "submit" || champ.type === "button" || champ.disabled) { return false; }
    if (champ.type === "checkbox" || champ.type === "radio") { return champ.checked !== champ.defaultChecked; }
    if (champ.tagName === "SELECT") {
      // Sans option « selected » dans la page, le navigateur choisit la première.
      var defaut = 0;
      for (var i = 0; i < champ.options.length; i++) {
        if (champ.options[i].defaultSelected) { defaut = i; }
      }
      return champ.options.length > 0 && champ.selectedIndex !== defaut;
    }
    return champ.value !== champ.defaultValue;
  }
  window.pasDeSaisie = function () {
    var actif = document.activeElement;
    if (actif && actif.matches && actif.matches("input, select, textarea")) { return false; }
    // Une saisie commencée mais pas encore envoyée (le champ n'a plus le
    // focus) n'est pas effacée par l'actualisation.
    var champs = document.querySelectorAll("main input, main select, main textarea");
    for (var i = 0; i < champs.length; i++) {
      if (champModifie(champs[i])) { return false; }
    }
    // Un volet (« Annuler le ticket »…) ouvert par l'agent reste ouvert.
    return !document.querySelector("details[open][data-ouvert-par-agent]");
  };
  document.addEventListener("click", function (e) {
    var resume = e.target.closest && e.target.closest("summary");
    if (resume && resume.parentNode.tagName === "DETAILS") {
      resume.parentNode.setAttribute("data-ouvert-par-agent", "1");
    }
  });

  document.addEventListener("htmx:afterSettle", function (e) {
    messagesEnToasts(document);
    suivreNouveaux(e.detail.elt);
    majDurees();
  });
  document.addEventListener("htmx:oobAfterSwap", function () {
    messagesEnToasts(document);
  });

  /* ---------------- Démarrage ---------------- */

  document.addEventListener("DOMContentLoaded", function () {
    initialiserHeure();
    messagesEnToasts(document);
    try {
      var enAttente = sessionStorage.getItem("toast-apres-rechargement");
      if (enAttente) {
        sessionStorage.removeItem("toast-apres-rechargement");
        toast(enAttente, "error");
      }
    } catch (ignore) {}
    suivreNouveaux(document);
    majDurees();
    setInterval(majDurees, 1000);
  });
})();

