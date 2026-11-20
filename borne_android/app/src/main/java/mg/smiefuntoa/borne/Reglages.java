package mg.smiefuntoa.borne;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;

/** Réglages de la borne, enregistrés sur la tablette. */
final class Reglages {

    private static final String FICHIER = "reglages_borne";

    private final SharedPreferences prefs;

    Reglages(Context context) {
        prefs = context.getSharedPreferences(FICHIER, Context.MODE_PRIVATE);
        // Anciennes versions : code enregistré en clair. On le remplace par son empreinte.
        String ancien = prefs.getString("code", "");
        if (!ancien.isEmpty()) {
            enregistrerCode(ancien);
            prefs.edit().remove("code").apply();
        }
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

    /** Le code des réglages n'est jamais conservé en clair : seulement sel + empreinte SHA-256. */
    boolean aUnCode() { return !prefs.getString("code_empreinte", "").isEmpty(); }

    boolean verifierCode(String saisi) {
        String sel = prefs.getString("code_sel", "");
        String attendu = prefs.getString("code_empreinte", "");
        return !attendu.isEmpty() && MessageDigest.isEqual(
                empreinte(sel, saisi).getBytes(StandardCharsets.UTF_8),
                attendu.getBytes(StandardCharsets.UTF_8));
    }

    boolean estConfiguree() { return !url().isEmpty() && aUnCode(); }

    /** `code` vide : le code actuel est conservé. */
    void enregistrer(String url, String imprimante, String nomImprimante, String largeur,
                     String page, boolean epingler, String code) {
        prefs.edit()
                .putString("url", url)
                .putString("imprimante", imprimante)
                .putString("nom_imprimante", nomImprimante)
                .putString("largeur", largeur)
                .putString("page", page)
                .putBoolean("epingler", epingler)
                .apply();
        if (code != null && !code.isEmpty()) {
            enregistrerCode(code);
        }
    }

    private void enregistrerCode(String code) {
        byte[] octets = new byte[16];
        new SecureRandom().nextBytes(octets);
        String sel = Base64.encodeToString(octets, Base64.NO_WRAP);
        prefs.edit()
                .putString("code_sel", sel)
                .putString("code_empreinte", empreinte(sel, code))
                .apply();
    }

    private static String empreinte(String sel, String code) {
        try {
            MessageDigest sha = MessageDigest.getInstance("SHA-256");
            byte[] resultat = sha.digest((sel + ":" + code).getBytes(StandardCharsets.UTF_8));
            return Base64.encodeToString(resultat, Base64.NO_WRAP);
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
