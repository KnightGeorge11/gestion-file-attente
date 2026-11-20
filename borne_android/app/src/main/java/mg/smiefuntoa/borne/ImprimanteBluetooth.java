package mg.smiefuntoa.borne;

import android.annotation.SuppressLint;
import android.bluetooth.BluetoothAdapter;
import android.bluetooth.BluetoothDevice;
import android.bluetooth.BluetoothManager;
import android.bluetooth.BluetoothSocket;
import android.content.Context;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.OutputStream;
import java.nio.charset.Charset;
import java.text.Normalizer;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.UUID;

/**
 * Envoi d'octets ESC/POS à une imprimante thermique Bluetooth déjà appairée
 * (profil série SPP, géré par la quasi-totalité des imprimantes de tickets).
 * Le contenu du ticket est préparé par le serveur ; ici on ne fait que l'envoyer.
 */
final class ImprimanteBluetooth {

    private static final UUID SPP = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB");
    /** Délai maximum d'une tentative de connexion (connect() n'en a pas). */
    private static final long DELAI_CONNEXION_MS = 6000;

    private ImprimanteBluetooth() { }

    /** Envoie les octets ; lève IOException avec un message lisible en cas d'échec. */
    @SuppressLint("MissingPermission")
    static synchronized void envoyer(Context context, String adresse, byte[] donnees) throws IOException {
        if (adresse == null || adresse.isEmpty()) {
            throw new IOException("Aucune imprimante choisie dans les réglages");
        }
        BluetoothManager manager = (BluetoothManager) context.getSystemService(Context.BLUETOOTH_SERVICE);
        BluetoothAdapter adaptateur = manager == null ? null : manager.getAdapter();
        if (adaptateur == null) {
            throw new IOException("Cette tablette n'a pas de Bluetooth");
        }
        if (!adaptateur.isEnabled()) {
            throw new IOException("Bluetooth désactivé");
        }
        try {
            // Une recherche d'appareils en cours ralentit fortement la connexion.
            adaptateur.cancelDiscovery();
        } catch (SecurityException ignore) {
            // permission de recherche absente : sans importance
        }
        BluetoothDevice appareil = adaptateur.getRemoteDevice(adresse);
        BluetoothSocket socket = appareil.createRfcommSocketToServiceRecord(SPP);
        try {
            connecter(socket);
        } catch (IOException premiereErreur) {
            fermer(socket);
            // Certaines imprimantes bon marché n'acceptent que la connexion « non sécurisée ».
            socket = appareil.createInsecureRfcommSocketToServiceRecord(SPP);
            try {
                connecter(socket);
            } catch (IOException e) {
                fermer(socket);
                throw new IOException("Imprimante injoignable (allumée ? à portée ?)", e);
            }
        }
        try {
            OutputStream sortie = socket.getOutputStream();
            sortie.write(donnees);
            sortie.flush();
            // Laisser à l'imprimante le temps de tout recevoir avant de couper la liaison.
            Thread.sleep(800);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
        } finally {
            fermer(socket);
        }
    }

    /** connect() avec un délai : passé DELAI_CONNEXION_MS, le socket est fermé et connect() échoue. */
    @SuppressLint("MissingPermission")
    private static void connecter(BluetoothSocket socket) throws IOException {
        Thread minuterie = new Thread(() -> {
            try {
                Thread.sleep(DELAI_CONNEXION_MS);
                fermer(socket);
            } catch (InterruptedException ignore) {
                // connexion établie à temps
            }
        });
        minuterie.setDaemon(true);
        minuterie.start();
        try {
            socket.connect();
        } finally {
            minuterie.interrupt();
        }
    }

    private static void fermer(BluetoothSocket socket) {
        try {
            socket.close();
        } catch (IOException ignore) {
            // rien à faire
        }
    }

    /** Ticket de test (réglages), pour vérifier la liaison et les accents. */
    static byte[] ticketDeTest(String page, String nomImprimante) {
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        o.write(0x1B); o.write('@');                                   // initialiser
        if ("cp858".equals(page)) { o.write(0x1B); o.write('t'); o.write(19); }
        if ("cp850".equals(page)) { o.write(0x1B); o.write('t'); o.write(2); }
        o.write(0x1B); o.write('a'); o.write(1);                        // centrer
        o.write(0x1D); o.write('!'); o.write(0x11);                     // double taille
        ecrire(o, "TEST IMPRIMANTE\n", page);
        o.write(0x1D); o.write('!'); o.write(0);
        ecrire(o, nomImprimante + "\n", page);
        ecrire(o, "Accents : é è à ç ô ù\n", page);
        ecrire(o, new SimpleDateFormat("dd/MM/yyyy HH:mm", Locale.FRANCE).format(new Date()) + "\n", page);
        o.write(0x1B); o.write('d'); o.write(4);                        // avancer
        o.write(0x1D); o.write('V'); o.write(0x42); o.write(0);         // couper
        return o.toByteArray();
    }

    private static void ecrire(ByteArrayOutputStream o, String texte, String page) {
        byte[] octets;
        try {
            if ("cp858".equals(page)) {
                octets = texte.getBytes(Charset.forName("IBM00858"));
            } else if ("cp850".equals(page)) {
                octets = texte.getBytes(Charset.forName("IBM850"));
            } else {
                octets = sansAccents(texte);
            }
        } catch (RuntimeException tableAbsente) {
            octets = sansAccents(texte);
        }
        o.write(octets, 0, octets.length);
    }

    private static byte[] sansAccents(String texte) {
        String simple = Normalizer.normalize(texte, Normalizer.Form.NFD).replaceAll("\\p{M}", "");
        return simple.getBytes(Charset.forName("US-ASCII"));
    }
}
