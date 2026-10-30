CREATE TABLE contacts (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 phone_number varchar(15) NOT NULL CHECK (phone_number ~ '^[1-9][0-9]{7,14}$'),
 name varchar(120) NOT NULL DEFAULT '',
 human_only boolean NOT NULL DEFAULT false,
 created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,phone_number), UNIQUE(tenant_id,id)
);
-- statement
CREATE TABLE contact_tags (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 name varchar(50) NOT NULL CHECK (length(trim(name)) > 0),
 color varchar(7) NOT NULL DEFAULT '#2563EB' CHECK (color ~ '^#[0-9A-Fa-f]{6}$'),
 UNIQUE(tenant_id,name), UNIQUE(tenant_id,id)
);
-- statement
CREATE TABLE contact_tag_links (
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 contact_id uuid NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
 tag_id uuid NOT NULL REFERENCES contact_tags(id) ON DELETE CASCADE,
 PRIMARY KEY(contact_id,tag_id),
 FOREIGN KEY(tenant_id,contact_id) REFERENCES contacts(tenant_id,id) ON DELETE CASCADE,
 FOREIGN KEY(tenant_id,tag_id) REFERENCES contact_tags(tenant_id,id) ON DELETE CASCADE
);
-- statement
CREATE INDEX contact_tag_links_tag ON contact_tag_links(tenant_id,tag_id,contact_id);
-- statement
ALTER TABLE conversations ADD COLUMN contact_id uuid REFERENCES contacts(id) ON DELETE SET NULL,
 ADD COLUMN human_only boolean NOT NULL DEFAULT false;
-- statement
CREATE INDEX conversations_contact ON conversations(contact_id);
-- statement
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['contacts','contact_tags','contact_tag_links'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'',true),'''')::uuid)',t);
  IF EXISTS(SELECT FROM pg_roles WHERE rolname='advoxs_app') THEN
   EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON %I TO advoxs_app,advoxs_system',t);
  END IF;
 END LOOP;
END $$;
-- statement
CREATE FUNCTION contact_phone_key(phone text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT regexp_replace(regexp_replace(phone,'[^0-9]','','g'),'^00','')
$$;
-- statement
CREATE FUNCTION contact_lock_phone() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || ':' || NEW.phone_number, 0));
 RETURN NEW;
END $$;
-- statement
CREATE TRIGGER contact_lock BEFORE INSERT OR UPDATE ON contacts
 FOR EACH ROW EXECUTE FUNCTION contact_lock_phone();
-- statement
CREATE FUNCTION contact_route_conversation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE c contacts%ROWTYPE;
BEGIN
 IF NEW.is_test THEN NEW.contact_id := NULL; NEW.human_only := false; RETURN NEW; END IF;
 IF TG_OP='INSERT' THEN
  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.tenant_id::text || ':' || contact_phone_key(NEW.contact_phone_number),0));
 END IF;
 SELECT * INTO c FROM contacts WHERE tenant_id=NEW.tenant_id
  AND phone_number=contact_phone_key(NEW.contact_phone_number);
 NEW.contact_id := c.id;
 NEW.human_only := COALESCE(c.human_only,false);
 IF NEW.human_only THEN
  NEW.state := 'human'; NEW.automation_status := 'idle';
  NEW.billing_gate_step := NULL; NEW.billing_gate_retries := 0;
  NEW.billing_gate_checkout_url := NULL;
 END IF;
 RETURN NEW;
END $$;
-- statement
CREATE TRIGGER contact_routing BEFORE INSERT OR UPDATE ON conversations
 FOR EACH ROW EXECUTE FUNCTION contact_route_conversation();
-- statement
CREATE FUNCTION contact_sync_conversations() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP IN ('UPDATE','DELETE') THEN
  UPDATE conversations SET contact_id=NULL WHERE tenant_id=OLD.tenant_id AND NOT is_test
   AND contact_phone_key(contact_phone_number)=OLD.phone_number;
 END IF;
 IF TG_OP IN ('INSERT','UPDATE') THEN
  UPDATE conversations SET contact_id=NEW.id WHERE tenant_id=NEW.tenant_id AND NOT is_test
   AND contact_phone_key(contact_phone_number)=NEW.phone_number;
 END IF;
 RETURN NULL;
END $$;
-- statement
CREATE TRIGGER contact_sync AFTER INSERT OR UPDATE OR DELETE ON contacts
 FOR EACH ROW EXECUTE FUNCTION contact_sync_conversations();
-- statement
CREATE FUNCTION contact_cancel_deliveries() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.human_only THEN
  UPDATE messages SET delivery_status='cancelled' WHERE conversation_id=NEW.id
   AND sender_type='agent' AND delivery_status='pending';
  UPDATE outbound_message_jobs SET status='cancelled',locked_at=NULL,completed_at=now(),
   last_error='atendimento exclusivamente humano' WHERE conversation_id=NEW.id
   AND status IN ('pending','processing');
 END IF;
 RETURN NULL;
END $$;
-- statement
CREATE TRIGGER contact_delivery_guard AFTER INSERT OR UPDATE ON conversations
 FOR EACH ROW EXECUTE FUNCTION contact_cancel_deliveries();
