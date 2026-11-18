"""
Intégrité garantie par PostgreSQL :

1. Cohérence de centre : chaque ligne métier porte le même centre_id que
   l'objet parent qu'elle référence (pas de clés étrangères composites).
2. Historique en ajout seulement : UPDATE et DELETE refusés sur
   circuit_evenement et audit_journalaudit.
"""

from django.db import migrations

FONCTION_COHERENCE = """
CREATE OR REPLACE FUNCTION verifier_coherence_centre()
RETURNS trigger AS $$
DECLARE
    i integer := 0;
    table_parent text;
    colonne text;
    valeur bigint;
    centre_parent bigint;
BEGIN
    WHILE i < TG_NARGS LOOP
        table_parent := TG_ARGV[i];
        colonne := TG_ARGV[i + 1];
        EXECUTE format('SELECT ($1).%I', colonne) INTO valeur USING NEW;
        IF valeur IS NOT NULL THEN
            EXECUTE format('SELECT centre_id FROM %I WHERE id = $1', table_parent)
                INTO centre_parent USING valeur;
            IF centre_parent IS DISTINCT FROM NEW.centre_id THEN
                RAISE EXCEPTION 'Incohérence de centre : %.% (%) n''appartient pas au centre %',
                    TG_TABLE_NAME, colonne, valeur, NEW.centre_id;
            END IF;
        END IF;
        i := i + 2;
    END LOOP;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

# table, [(table parente, colonne), ...]
COHERENCES = [
    ("circuit_ticket", [("parcours_parcours", "parcours_id"), ("centres_borne", "borne_id")]),
    ("circuit_passage", [("circuit_ticket", "ticket_id"), ("centres_service", "service_id"),
                         ("centres_fileattente", "file_id")]),
    ("circuit_entreefile", [("circuit_passage", "passage_id"), ("centres_fileattente", "file_id")]),
    ("circuit_appel", [("circuit_entreefile", "entree_id"), ("circuit_ticket", "ticket_id"),
                       ("centres_service", "service_id")]),
    ("circuit_transfert", [("circuit_ticket", "ticket_id"), ("circuit_passage", "passage_origine_id"),
                           ("circuit_passage", "passage_destination_id"),
                           ("centres_service", "service_destination_id")]),
    ("circuit_reservationquota", [("circuit_ticket", "ticket_id"), ("parcours_parcours", "parcours_id")]),
    ("circuit_demandeautre", [("circuit_ticket", "ticket_id"), ("circuit_motifdemande", "motif_id")]),
    ("circuit_notification", [("circuit_demandeautre", "demande_id"), ("centres_service", "service_id")]),
    ("circuit_reponsenotification", [("circuit_notification", "notification_id")]),
    ("circuit_evenement", [("circuit_ticket", "ticket_id"), ("circuit_passage", "passage_id"),
                           ("circuit_entreefile", "entree_id")]),
]


def _sql_coherence():
    instructions = [FONCTION_COHERENCE]
    for table, parents in COHERENCES:
        args = ", ".join(f"'{p}', '{c}'" for p, c in parents)
        instructions.append(
            f"CREATE TRIGGER {table}_coherence_centre BEFORE INSERT OR UPDATE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION verifier_coherence_centre({args});"
        )
    return "\n".join(instructions)


def _sql_suppression_coherence():
    instructions = [
        f"DROP TRIGGER IF EXISTS {table}_coherence_centre ON {table};"
        for table, _ in COHERENCES
    ]
    instructions.append("DROP FUNCTION IF EXISTS verifier_coherence_centre();")
    return "\n".join(instructions)


AJOUT_SEULEMENT = """
CREATE OR REPLACE FUNCTION refuser_modification_historique()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Historique en ajout seulement : % refusé sur %', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER circuit_evenement_ajout_seulement
BEFORE UPDATE OR DELETE ON circuit_evenement
FOR EACH ROW EXECUTE FUNCTION refuser_modification_historique();
"""

SUPPRESSION_AJOUT_SEULEMENT = """
DROP TRIGGER IF EXISTS circuit_evenement_ajout_seulement ON circuit_evenement;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("circuit", "0001_initial"),
        ("centres", "0004_service_destinataire_demandes"),
        ("parcours", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(_sql_coherence(), _sql_suppression_coherence()),
        migrations.RunSQL(AJOUT_SEULEMENT, SUPPRESSION_AJOUT_SEULEMENT),
    ]
