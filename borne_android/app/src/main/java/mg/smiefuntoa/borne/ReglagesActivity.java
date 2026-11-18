package mg.smiefuntoa.borne;

import android.Manifest;
import android.annotation.SuppressLint;
import android.app.Activity;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.RadioGroup;
import android.widget.Spinner;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.io.IOException;
import java.util.ArrayList;
import java.util.List;

/** Réglages : adresse du serveur, imprimante Bluetooth, papier, verrouillage, code. */
public class ReglagesActivity extends Activity {

    private static final int DEMANDE_BLUETOOTH = 2;

    private Reglages reglages;
    private EditText url;
    private EditText code;
    private Spinner imprimantes;
    private RadioGroup largeur;
    private RadioGroup page;
    private Switch epingler;
    private TextView etat;
    private final List<String> adresses = new ArrayList<>();
    private final List<String> noms = new ArrayList<>();

    @Override
    protected void onCreate(Bundle etatSauve) {
        super.onCreate(etatSauve);
        setContentView(R.layout.activity_reglages);
        reglages = new Reglages(this);

        url = findViewById(R.id.url);
        code = findViewById(R.id.code);
        imprimantes = findViewById(R.id.imprimantes);
        largeur = findViewById(R.id.largeur);
        page = findViewById(R.id.page);
        epingler = findViewById(R.id.epingler);
        etat = findViewById(R.id.etat);

        url.setText(reglages.url());
        code.setText(reglages.code());
        largeur.check("32".equals(reglages.largeur()) ? R.id.largeur_58 : R.id.largeur_80);
        page.check("cp858".equals(reglages.page()) ? R.id.page_cp858
                : "cp850".equals(reglages.page()) ? R.id.page_cp850 : R.id.page_ascii);
        epingler.setChecked(reglages.epingler());

        ((Button) findViewById(R.id.actualiser)).setOnClickListener(v -> listerImprimantes());
        ((Button) findViewById(R.id.tester)).setOnClickListener(v -> imprimerTest());
        ((Button) findViewById(R.id.enregistrer)).setOnClickListener(v -> enregistrer());
        ((Button) findViewById(R.id.quitter)).setOnClickListener(v -> quitter());

        listerImprimantes();
    }

    /** Sans réglages valides, Retour ferme l'application (sinon on revient sans fin ici). */
    @SuppressWarnings("deprecation")
    @Override
    public void onBackPressed() {
        if (reglages.estConfiguree()) {
            super.onBackPressed();
        } else {
            finishAffinity();
        }
    }

    // ------------------------------------------------------------------

    @SuppressLint("MissingPermission")
    private void listerImprimantes() {
        adresses.clear();
        noms.clear();
        adresses.add("");
        noms.add("Aucune (impression réseau ou pas d'impression)");
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S
                && checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.BLUETOOTH_CONNECT}, DEMANDE_BLUETOOTH);
            etat.setText("Autorisez l'accès Bluetooth, puis touchez « Actualiser ».");
        } else {
            BluetoothManager manager = (BluetoothManager) getSystemService(BLUETOOTH_SERVICE);
            BluetoothAdapter adaptateur = manager == null ? null : manager.getAdapter();
            if (adaptateur == null) {
                etat.setText("Cette tablette n'a pas de Bluetooth.");
            } else if (!adaptateur.isEnabled()) {
                etat.setText("Bluetooth désactivé : activez-le dans les paramètres Android.");
            } else {
                for (BluetoothDevice appareil : adaptateur.getBondedDevices()) {
                    adresses.add(appareil.getAddress());
                    noms.add((appareil.getName() == null ? "Appareil" : appareil.getName())
                            + "  (" + appareil.getAddress() + ")");
                }
                etat.setText(adresses.size() == 1
                        ? "Aucun appareil appairé : appairez d'abord l'imprimante dans les paramètres Bluetooth d'Android."
                        : "");
            }
        }
        ArrayAdapter<String> adaptateurListe =
                new ArrayAdapter<>(this, android.R.layout.simple_spinner_dropdown_item, noms);
        imprimantes.setAdapter(adaptateurListe);
        int choisie = adresses.indexOf(reglages.imprimante());
        imprimantes.setSelection(Math.max(choisie, 0));
    }

    @Override
    public void onRequestPermissionsResult(int demande, String[] permissions, int[] resultats) {
        super.onRequestPermissionsResult(demande, permissions, resultats);
        if (demande == DEMANDE_BLUETOOTH && resultats.length > 0
                && resultats[0] == PackageManager.PERMISSION_GRANTED) {
            listerImprimantes();
        }
    }

    private String adresseChoisie() {
        int i = imprimantes.getSelectedItemPosition();
        return i >= 0 && i < adresses.size() ? adresses.get(i) : "";
    }

    private String nomChoisi() {
        int i = imprimantes.getSelectedItemPosition();
        return i > 0 && i < noms.size() ? noms.get(i) : "";
    }

    private String pageChoisie() {
        int id = page.getCheckedRadioButtonId();
        return id == R.id.page_cp858 ? "cp858" : id == R.id.page_cp850 ? "cp850" : "ascii";
    }

    private void imprimerTest() {
        String adresse = adresseChoisie();
        if (adresse.isEmpty()) {
            Toast.makeText(this, "Choisissez d'abord une imprimante", Toast.LENGTH_SHORT).show();
            return;
        }
        byte[] donnees = ImprimanteBluetooth.ticketDeTest(pageChoisie(), nomChoisi());
        etat.setText("Impression du ticket de test…");
        new Thread(() -> {
            String resultat;
            try {
                ImprimanteBluetooth.envoyer(this, adresse, donnees);
                resultat = "Ticket de test envoyé. Vérifiez les accents ; s'ils sont faux, choisissez « Sans accents ».";
            } catch (IOException | SecurityException | IllegalArgumentException e) {
                resultat = "Échec : " + e.getMessage();
            }
            String message = resultat;
            runOnUiThread(() -> etat.setText(message));
        }).start();
    }

    private void enregistrer() {
        String adresse = url.getText().toString().trim();
        String codeSaisi = code.getText().toString().trim();
        Uri uri = Uri.parse(adresse);
        if (uri.getScheme() == null || !(adresse.startsWith("http://") || adresse.startsWith("https://"))
                || uri.getHost() == null) {
            url.setError("Adresse complète attendue, ex. http://192.168.1.10:8000/circuit/borne/");
            return;
        }
        if (codeSaisi.length() < 4) {
            code.setError("Au moins 4 chiffres");
            return;
        }
        reglages.enregistrer(
                adresse,
                adresseChoisie(),
                nomChoisi(),
                largeur.getCheckedRadioButtonId() == R.id.largeur_58 ? "32" : "48",
                pageChoisie(),
                epingler.isChecked(),
                codeSaisi);
        Toast.makeText(this, "Réglages enregistrés", Toast.LENGTH_SHORT).show();
        finish();
    }

    /** Sortie de la borne (maintenance) : retire l'épinglage et ferme l'application. */
    private void quitter() {
        try {
            stopLockTask();
        } catch (RuntimeException ignore) {
            // la borne n'était pas épinglée
        }
        finishAffinity();
    }
}
