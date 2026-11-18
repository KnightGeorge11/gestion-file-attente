package mg.smiefuntoa.borne;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.ActivityManager;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.util.Base64;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.view.WindowManager;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;

import org.json.JSONObject;

import java.io.IOException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * La borne : affiche en plein écran la page web du serveur
 * (…/circuit/borne/). Toute la logique reste sur le serveur ; l'application
 * n'ajoute que l'impression Bluetooth (dernier recours) et le verrouillage.
 *
 * Accès aux réglages : toucher 5 fois de suite le coin haut-gauche de
 * l'écran, puis saisir le code.
 */
public class MainActivity extends Activity {

    private static final int DEMANDE_BLUETOOTH = 1;
    private static final int NOMBRE_TOUCHERS_REGLAGES = 5;
    private static final long DELAI_TOUCHERS_MS = 4000;
    private static final long NOUVEL_ESSAI_MS = 10000;

    private WebView web;
    private Reglages reglages;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService impression = Executors.newSingleThreadExecutor();
    private int touchers;
    private long premierToucher;
    private String urlChargee = "";

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle etat) {
        super.onCreate(etat);
        reglages = new Reglages(this);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        web = new WebView(this);
        web.setBackgroundColor(0xFF0F766E);
        setContentView(web);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setSupportZoom(false);
        s.setBuiltInZoomControls(false);
        s.setMediaPlaybackRequiresUserGesture(false);
        CookieManager.getInstance().setAcceptCookie(true);   // la borne reste activée (session du serveur)
        web.addJavascriptInterface(new Pont(), "BorneAndroid");
        web.setWebViewClient(new Client());
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (!reglages.estConfiguree()) {
            startActivity(new Intent(this, ReglagesActivity.class));
            return;
        }
        pleinEcran();
        demanderBluetoothSiNecessaire();
        if (reglages.epingler() && !estEpinglee()) {
            try {
                startLockTask();   // Android demande une confirmation la première fois
            } catch (RuntimeException ignore) {
                // épinglage refusé : la borne fonctionne quand même
            }
        }
        if (!reglages.url().equals(urlChargee)) {
            charger();
        }
    }

    @Override
    protected void onPause() {
        super.onPause();
        CookieManager.getInstance().flush();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        impression.shutdown();
        web.destroy();
        super.onDestroy();
    }

    @Override
    public void onWindowFocusChanged(boolean focus) {
        super.onWindowFocusChanged(focus);
        if (focus) {
            pleinEcran();
        }
    }

    /** Le patient ne peut pas quitter la borne avec le bouton Retour. */
    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        // ignoré volontairement
    }

    // ------------------------------------------------------------------
    // Accès caché aux réglages : 5 touchers dans le coin haut-gauche
    // ------------------------------------------------------------------

    @Override
    public boolean dispatchTouchEvent(MotionEvent ev) {
        if (ev.getActionMasked() == MotionEvent.ACTION_DOWN) {
            float zone = 90 * getResources().getDisplayMetrics().density;
            if (ev.getX() < zone && ev.getY() < zone) {
                long maintenant = System.currentTimeMillis();
                if (maintenant - premierToucher > DELAI_TOUCHERS_MS) {
                    premierToucher = maintenant;
                    touchers = 0;
                }
                if (++touchers >= NOMBRE_TOUCHERS_REGLAGES) {
                    touchers = 0;
                    demanderCode();
                }
            } else {
                touchers = 0;
            }
        }
        return super.dispatchTouchEvent(ev);
    }

    private void demanderCode() {
        EditText saisie = new EditText(this);
        saisie.setInputType(InputType.TYPE_CLASS_NUMBER | InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        saisie.setHint("Code");
        new AlertDialog.Builder(this)
                .setTitle("Réglages de la borne")
                .setView(saisie)
                .setNegativeButton("Annuler", null)
                .setPositiveButton("Ouvrir", (d, w) -> {
                    if (saisie.getText().toString().equals(reglages.code())) {
                        startActivity(new Intent(this, ReglagesActivity.class));
                    } else {
                        Toast.makeText(this, "Code incorrect", Toast.LENGTH_SHORT).show();
                    }
                })
                .show();
    }

    // ------------------------------------------------------------------
    // Page web
    // ------------------------------------------------------------------

    private void charger() {
        urlChargee = reglages.url();
        web.loadUrl(urlChargee);
    }

    /** Seules les pages du serveur configuré s'ouvrent dans la borne. */
    private boolean memeServeur(Uri uri) {
        Uri base = Uri.parse(reglages.url());
        return uri.getHost() != null
                && uri.getHost().equalsIgnoreCase(base.getHost())
                && uri.getPort() == base.getPort()
                && String.valueOf(uri.getScheme()).equalsIgnoreCase(base.getScheme());
    }

    private class Client extends WebViewClient {
        @Override
        public boolean shouldOverrideUrlLoading(WebView vue, WebResourceRequest requete) {
            return !memeServeur(requete.getUrl());   // true = bloqué
        }

        @Override
        public void onReceivedError(WebView vue, WebResourceRequest requete, WebResourceError erreur) {
            if (requete.isForMainFrame()) {
                afficherHorsLigne();
            }
        }
    }

    /** Serveur injoignable : message clair et nouvel essai automatique. */
    private void afficherHorsLigne() {
        String page = "<html><head><meta name='viewport' content='width=device-width,initial-scale=1'></head>"
                + "<body style='margin:0;height:100vh;display:flex;align-items:center;justify-content:center;"
                + "background:#0f766e;color:#fff;font-family:sans-serif;text-align:center'>"
                + "<div><div style='font-size:28px;font-weight:600;margin-bottom:12px'>Borne momentanément indisponible</div>"
                + "<div style='font-size:18px;opacity:.85'>Merci de vous présenter directement à l'accueil.</div>"
                + "<div style='font-size:13px;opacity:.6;margin-top:28px'>Nouvel essai automatique…</div></div></body></html>";
        web.loadDataWithBaseURL(null, page, "text/html", "utf-8", null);
        handler.removeCallbacksAndMessages(null);
        handler.postDelayed(this::charger, NOUVEL_ESSAI_MS);
    }

    // ------------------------------------------------------------------
    // Pont avec la page « ticket » du serveur (impression Bluetooth)
    // ------------------------------------------------------------------

    private class Pont {
        /** Largeur du papier et page de codes, pour que le serveur prépare le ticket. */
        @JavascriptInterface
        public String reglages() {
            try {
                return new JSONObject()
                        .put("largeur", reglages.largeur())
                        .put("page", reglages.page())
                        .toString();
            } catch (Exception e) {
                return "{}";
            }
        }

        /** Reçoit le ticket (ESC/POS en base64) et l'envoie à l'imprimante Bluetooth. */
        @JavascriptInterface
        public void imprimer(String base64) {
            final byte[] donnees;
            try {
                donnees = Base64.decode(base64, Base64.DEFAULT);
            } catch (IllegalArgumentException e) {
                repondre(false, "Ticket illisible");
                return;
            }
            impression.execute(() -> {
                try {
                    ImprimanteBluetooth.envoyer(MainActivity.this, reglages.imprimante(), donnees);
                    repondre(true, "");
                } catch (IOException | SecurityException | IllegalArgumentException e) {
                    repondre(false, e.getMessage() == null ? "Erreur d'impression" : e.getMessage());
                }
            });
        }
    }

    private void repondre(boolean ok, String message) {
        String js = "window.borneImpressionTerminee && window.borneImpressionTerminee("
                + ok + "," + JSONObject.quote(message) + ")";
        web.post(() -> web.evaluateJavascript(js, null));
    }

    // ------------------------------------------------------------------
    // Divers
    // ------------------------------------------------------------------

    private void demanderBluetoothSiNecessaire() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                && !reglages.imprimante().isEmpty()
                && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT}, DEMANDE_BLUETOOTH);
        }
    }

    private boolean estEpinglee() {
        ActivityManager am = (ActivityManager) getSystemService(Context.ACTIVITY_SERVICE);
        return am != null && am.getLockTaskModeState() != ActivityManager.LOCK_TASK_MODE_NONE;
    }

    @SuppressWarnings("deprecation")
    private void pleinEcran() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) {
                c.hide(WindowInsets.Type.systemBars());
                c.setSystemBarsBehavior(WindowInsetsController.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
            }
        } else {
            getWindow().getDecorView().setSystemUiVisibility(
                    View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                            | View.SYSTEM_UI_FLAG_FULLSCREEN
                            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN);
        }
    }
}
