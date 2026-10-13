CREATE TABLE office_memberships (
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 user_id uuid NOT NULL REFERENCES users(id),
 role varchar NOT NULL CHECK (role IN ('admin','lawyer')),
 active boolean NOT NULL DEFAULT true,
 PRIMARY KEY (tenant_id,user_id)
);
-- statement
INSERT INTO office_memberships(tenant_id,user_id,role,active)
 SELECT tenant_id,id,role,active FROM users;
-- statement
CREATE INDEX office_memberships_user ON office_memberships(user_id);
-- statement
UPDATE users SET active=true, session_version=session_version+1;
-- statement
CREATE UNIQUE INDEX users_email_normalized ON users(lower(email));
-- statement
ALTER TABLE office_memberships ENABLE ROW LEVEL SECURITY;
-- statement
CREATE POLICY tenant_isolation ON office_memberships
 USING (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid)
 WITH CHECK (tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid);
-- statement
ALTER TABLE users ENABLE ROW LEVEL SECURITY;
-- statement
DROP POLICY IF EXISTS tenant_isolation ON users;
-- statement
CREATE POLICY tenant_isolation ON users USING (
 EXISTS (SELECT 1 FROM office_memberships m WHERE m.user_id=users.id
 AND m.tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid))
 WITH CHECK (EXISTS (SELECT 1 FROM office_memberships m WHERE m.user_id=users.id
 AND m.tenant_id=nullif(current_setting('app.tenant_id',true),'')::uuid));
-- statement
DO $$ DECLARE c record; BEGIN
 FOR c IN SELECT conname,conrelid::regclass AS tbl FROM pg_constraint
 WHERE contype='f' AND confrelid='users'::regclass AND cardinality(conkey)=2
 LOOP EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I',c.tbl,c.conname); END LOOP;
END $$;
-- statement
ALTER TABLE conversations ADD CONSTRAINT conversation_member
 FOREIGN KEY (tenant_id,assigned_user_id) REFERENCES office_memberships(tenant_id,user_id);
-- statement
ALTER TABLE agent_lawyers ADD CONSTRAINT agent_member
 FOREIGN KEY (tenant_id,user_id) REFERENCES office_memberships(tenant_id,user_id);
-- statement
DO $$ BEGIN
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='advoxs_app') THEN
 GRANT SELECT,INSERT,UPDATE,DELETE ON office_memberships TO advoxs_app,advoxs_system;
 END IF;
END $$;
-- statement
CREATE FUNCTION initial_office_membership() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO office_memberships(tenant_id,user_id,role,active)
 VALUES (NEW.tenant_id,NEW.id,NEW.role,NEW.active);
 RETURN NEW;
END $$;
-- statement
CREATE TRIGGER initial_office_membership AFTER INSERT ON users
 FOR EACH ROW EXECUTE FUNCTION initial_office_membership();
-- statement
CREATE OR REPLACE FUNCTION team_queue_urgency() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE selected_agent uuid;
BEGIN
 IF NEW.urgent_since IS NULL THEN
  NEW.urgency_episode_id := NULL;
  RETURN NEW;
 END IF;
 IF NEW.is_test OR NEW.deletion_requested_at IS NOT NULL THEN RETURN NEW; END IF;
 IF NEW.urgency_episode_id IS NULL OR OLD.urgent_since IS NULL THEN
  NEW.urgency_episode_id := gen_random_uuid();
 END IF;
 selected_agent := COALESCE(NEW.current_agent_id, NEW.urgent_agent_id,
   (SELECT id FROM agents WHERE tenant_id=NEW.tenant_id AND is_entry_point LIMIT 1));
 INSERT INTO team_emails(tenant_id,kind,conversation_id,episode_id,user_id,agent_id)
 SELECT NEW.tenant_id,'urgency',NEW.id,NEW.urgency_episode_id,u.user_id,selected_agent
 FROM office_memberships u JOIN users identity ON identity.id=u.user_id
 WHERE u.tenant_id=NEW.tenant_id AND u.active AND identity.active AND (
  EXISTS(SELECT 1 FROM agent_lawyers a WHERE a.tenant_id=u.tenant_id
    AND a.agent_id=selected_agent AND a.user_id=u.user_id AND u.role='lawyer')
  OR (u.role='admin' AND NOT EXISTS(
    SELECT 1 FROM agent_lawyers a JOIN office_memberships v ON v.user_id=a.user_id AND v.tenant_id=a.tenant_id
    JOIN users vi ON vi.id=v.user_id
    WHERE a.tenant_id=NEW.tenant_id AND a.agent_id=selected_agent
      AND v.active AND vi.active AND v.role='lawyer'))
 ) ON CONFLICT (episode_id,user_id) DO UPDATE
 SET agent_id=EXCLUDED.agent_id, status='pending',available_at=now(),attempts=0
 WHERE team_emails.status='cancelled';
 RETURN NEW;
END $$;

-- statement
CREATE TABLE office_sessions (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(), user_id uuid NOT NULL REFERENCES users(id),
 tenant_id uuid REFERENCES tenants(id), version integer NOT NULL DEFAULT 0
);
-- statement
ALTER TABLE office_sessions ENABLE ROW LEVEL SECURITY;
-- statement
DO $$ BEGIN
 IF EXISTS(SELECT FROM pg_roles WHERE rolname='advoxs_system') THEN
 GRANT SELECT,INSERT,UPDATE,DELETE ON office_sessions TO advoxs_system;
 END IF;
END $$;
