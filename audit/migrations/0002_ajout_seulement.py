from django.db import migrations

SQL = """
CREATE OR REPLACE FUNCTION refuser_modification_historique()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'Historique en ajout seulement : % refusé sur %', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_journalaudit_ajout_seulement
BEFORE UPDATE OR DELETE ON audit_journalaudit
FOR EACH ROW EXECUTE FUNCTION refuser_modification_historique();
"""

SQL_INVERSE = "DROP TRIGGER IF EXISTS audit_journalaudit_ajout_seulement ON audit_journalaudit;"


class Migration(migrations.Migration):

    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(SQL, SQL_INVERSE)]
