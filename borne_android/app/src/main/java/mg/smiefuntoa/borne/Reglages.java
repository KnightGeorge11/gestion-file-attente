package mg.smiefuntoa.borne;

import android.content.Context;
import android.content.SharedPreferences;

/** Réglages de la borne, enregistrés sur la tablette. */
final class Reglages {

    private static final String FICHIER = "reglages_borne";

    private final SharedPreferences prefs;

    Reglages(Context context) {
        prefs = context.getSharedPreferences(FICHIER, Context.MODE_PRIVATE);
    }

    /** Adresse de la page borne, ex. http://192.168.1.10:8000/circuit/borne/ */
    String url() { return prefs.getString("url", ""); }

    /** Adresse MAC de l'imprimante Bluetooth appairée (vide = aucune). */
    String imprimante() { return prefs.getString("imprimante", ""); }

    String nomImprimante() { return prefs.getString("nom_imprimante", ""); }

    /** 48 caractères par ligne (papier 80 mm) ou 32 (papier 58 mm). */
    String largeur() { return prefs.getString("largeur", "48"); }

    /** Page de codes : ascii (sans accents), cp858 ou cp850. */
    String page() { return prefs.getString("page", "ascii"); }

    boolean epingler() { return prefs.getBoolean("epingler", false); }

    String code() { return prefs.getString("code", ""); }

    boolean estConfiguree() { return !url().isEmpty() && !code().isEmpty(); }

    void enregistrer(String url, String imprimante, String nomImprimante, String largeur,
                     String page, boolean epingler, String code) {
        prefs.edit()
                .putString("url", url)
                .putString("imprimante", imprimante)
                .putString("nom_imprimante", nomImprimante)
                .putString("largeur", largeur)
                .putString("page", page)
                .putBoolean("epingler", epingler)
                .putString("code", code)
                .apply();
    }
}
