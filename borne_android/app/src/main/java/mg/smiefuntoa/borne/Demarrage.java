package mg.smiefuntoa.borne;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Relance la borne au démarrage de la tablette (coupure de courant).
 * Android 10 et plus limitent ce lancement : y définir plutôt « Borne FUNTOA »
 * comme application d'écran d'accueil par défaut (voir le README).
 */
public class Demarrage extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())) {
            Intent borne = new Intent(context, MainActivity.class);
            borne.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            try {
                context.startActivity(borne);
            } catch (RuntimeException ignore) {
                // lancement refusé par le système : l'écran d'accueil par défaut prend le relais
            }
        }
    }
}
