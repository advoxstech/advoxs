CREATE TABLE follow_up_batches (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 created_by_user_id uuid REFERENCES users(id) ON DELETE SET NULL,
 created_by_name text NOT NULL DEFAULT '',
 provider varchar(10) NOT NULL CHECK (provider IN ('meta','zapi')),
 template jsonb,
 params jsonb NOT NULL DEFAULT '[]'::jsonb,
 text text,
 last_enqueued_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 CHECK ((provider = 'meta' AND template IS NOT NULL) OR (provider = 'zapi' AND text IS NOT NULL)),
 UNIQUE(tenant_id,id)
);
-- statement
CREATE INDEX follow_up_batches_tenant_created ON follow_up_batches(tenant_id, created_at DESC);
-- statement
CREATE TABLE follow_up_recipients (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 batch_id uuid NOT NULL,
 contact_id uuid REFERENCES contacts(id) ON DELETE SET NULL,
 phone varchar(15) NOT NULL CHECK (phone ~ '^[1-9][0-9]{7,14}$'),
 contact_name varchar(120) NOT NULL DEFAULT '',
 conversation_id uuid REFERENCES conversations(id) ON DELETE SET NULL,
 message_id uuid REFERENCES messages(id) ON DELETE SET NULL,
 status varchar(10) NOT NULL DEFAULT 'pending'
  CHECK (status IN ('pending','processing','sent','failed','skipped')),
 attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(),
 locked_at timestamptz,
 error text,
 sent_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,batch_id) REFERENCES follow_up_batches(tenant_id,id) ON DELETE CASCADE,
 UNIQUE(batch_id,phone)
);
-- statement
CREATE INDEX follow_up_recipients_queue ON follow_up_recipients(status, available_at);
-- statement
CREATE INDEX follow_up_recipients_batch ON follow_up_recipients(tenant_id, batch_id);
-- statement
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['follow_up_batches','follow_up_recipients'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
  IF EXISTS(SELECT FROM pg_roles WHERE rolname='advoxs_app') THEN
   EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON %I TO advoxs_app,advoxs_system',t);
  END IF;
 END LOOP;
END $$;
