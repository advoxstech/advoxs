"""Tarefas duráveis de ingestão e exclusão de arquivos.

Revision ID: 0055
Revises: 0054
"""

from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade():
    statements = """
        ALTER TABLE knowledge_base_files ADD COLUMN deletion_requested_at timestamptz;
        -- statement
        CREATE TABLE knowledge_file_tasks (
            file_id uuid NOT NULL,
            tenant_id uuid NOT NULL REFERENCES tenants(id),
            operation text NOT NULL CHECK (operation IN ('ingest', 'delete')),
            state text NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'running', 'completed', 'failed')),
            attempts integer NOT NULL DEFAULT 0,
            lease_until timestamptz,
            lease_token uuid,
            next_attempt_at timestamptz NOT NULL DEFAULT now(),
            last_error text,
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (file_id, operation)
        );
        -- statement
        CREATE INDEX ix_file_tasks_recovery ON knowledge_file_tasks(next_attempt_at)
            WHERE state IN ('pending', 'running');
        -- statement
        ALTER TABLE knowledge_file_tasks ENABLE ROW LEVEL SECURITY;
        -- statement
        CREATE POLICY tenant_isolation ON knowledge_file_tasks
            USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
        -- statement
        CREATE FUNCTION schedule_knowledge_file_task() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE task_operation text;
        BEGIN
            IF NEW.deletion_requested_at IS NOT NULL THEN
                IF TG_OP = 'UPDATE' AND NEW.deletion_requested_at
                   IS NOT DISTINCT FROM OLD.deletion_requested_at THEN
                    RETURN NEW;
                END IF;
                task_operation := 'delete';
            ELSIF NEW.status = 'processing' THEN
                IF TG_OP = 'UPDATE' AND OLD.status = 'processing' THEN RETURN NEW; END IF;
                task_operation := 'ingest';
            ELSE RETURN NEW;
            END IF;
            INSERT INTO knowledge_file_tasks(file_id, tenant_id, operation)
                VALUES (NEW.id, NEW.tenant_id, task_operation)
            ON CONFLICT (file_id, operation) DO UPDATE SET
                state='pending', attempts=0, lease_until=NULL, lease_token=NULL,
                next_attempt_at=now(), last_error=NULL, updated_at=now();
            RETURN NEW;
        END $$;
        -- statement
        CREATE TRIGGER schedule_knowledge_file_task
            AFTER INSERT OR UPDATE OF status, deletion_requested_at ON knowledge_base_files
            FOR EACH ROW EXECUTE FUNCTION schedule_knowledge_file_task();
        -- statement
        INSERT INTO knowledge_file_tasks(file_id, tenant_id, operation)
            SELECT id, tenant_id, 'ingest' FROM knowledge_base_files WHERE status='processing';
    """
    for statement in statements.split("-- statement"):
        op.execute(statement)


def downgrade():
    for statement in (
        "DROP TRIGGER schedule_knowledge_file_task ON knowledge_base_files",
        "DROP FUNCTION schedule_knowledge_file_task()",
        "DROP TABLE knowledge_file_tasks",
        "ALTER TABLE knowledge_base_files DROP COLUMN deletion_requested_at",
    ):
        op.execute(statement)
