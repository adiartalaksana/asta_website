from django.db import migrations


FORWARD_SQL = """
CREATE OR REPLACE FUNCTION prevent_posted_journal_line_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.is_posted THEN
        RAISE EXCEPTION 'Posted journal lines are immutable; create a reversal entry instead.';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER journal_line_immutable_after_posting
BEFORE UPDATE OR DELETE ON ledger_journalline
FOR EACH ROW EXECUTE FUNCTION prevent_posted_journal_line_mutation();
"""

REVERSE_SQL = """
DROP TRIGGER IF EXISTS journal_line_immutable_after_posting ON ledger_journalline;
DROP FUNCTION IF EXISTS prevent_posted_journal_line_mutation();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("ledger", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
    ]
