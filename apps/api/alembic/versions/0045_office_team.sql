ALTER TABLE users ADD COLUMN active boolean NOT NULL DEFAULT true,
 ADD CONSTRAINT team_role CHECK (role IN ('admin', 'lawyer')),
 ADD CONSTRAINT users_tenant_id_id UNIQUE (tenant_id, id);
-- statement
ALTER TABLE agents ADD CONSTRAINT agents_tenant_id_id UNIQUE (tenant_id, id);
-- statement
ALTER TABLE conversations
 ADD COLUMN assigned_user_id uuid,
 ADD COLUMN assigned_user_name text,
 ADD COLUMN urgency_episode_id uuid,
 ADD FOREIGN KEY (tenant_id, assigned_user_id) REFERENCES users(tenant_id,id);
-- statement
ALTER TABLE messages ADD COLUMN sender_user_id uuid REFERENCES users(id),
 ADD COLUMN sender_user_name text;
-- statement
CREATE TABLE agent_lawyers (
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 agent_id uuid NOT NULL,
 user_id uuid NOT NULL,
 PRIMARY KEY (tenant_id,agent_id,user_id),
 FOREIGN KEY (tenant_id,agent_id) REFERENCES agents(tenant_id,id) ON DELETE CASCADE,
 FOREIGN KEY (tenant_id,user_id) REFERENCES users(tenant_id,id)
);
-- statement
CREATE TABLE team_invitations (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 email text NOT NULL,
 name text NOT NULL,
 role text NOT NULL CHECK (role IN ('admin','lawyer')),
 token_hash text NOT NULL UNIQUE,
 expires_at timestamptz NOT NULL,
 accepted_at timestamptz,
 cancelled_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now()
);
-- statement
CREATE UNIQUE INDEX team_invitation_pending ON team_invitations(tenant_id,lower(email))
 WHERE accepted_at IS NULL AND cancelled_at IS NULL;
-- statement
CREATE TABLE attendance_events (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 actor_id uuid REFERENCES users(id),
 actor_name text NOT NULL,
 action text NOT NULL,
 target_name text,
 created_at timestamptz NOT NULL DEFAULT now()
);
-- statement
CREATE TABLE team_emails (
 id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 kind text NOT NULL CHECK (kind IN ('invitation','urgency')),
 invitation_id uuid REFERENCES team_invitations(id) ON DELETE CASCADE,
 conversation_id uuid REFERENCES conversations(id) ON DELETE CASCADE,
 episode_id uuid,
 user_id uuid REFERENCES users(id),
 agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
 secret_encrypted text,
 status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','sent','failed','cancelled')),
 attempts integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL DEFAULT now(),
 locked_at timestamptz,
 claim_id uuid,
 last_error text,
 created_at timestamptz NOT NULL DEFAULT now(),
 sent_at timestamptz,
 UNIQUE (episode_id,user_id),
 UNIQUE (invitation_id)
);
-- statement
CREATE INDEX team_emails_pending ON team_emails(status,available_at);
-- statement
CREATE INDEX attendance_events_conversation ON attendance_events(tenant_id,conversation_id,created_at);
-- statement
DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['agent_lawyers','team_invitations','attendance_events','team_emails'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
  EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''app.tenant_id'',true),'''')::uuid) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'',true),'''')::uuid)', t);
  IF EXISTS(SELECT FROM pg_roles WHERE rolname='advoxs_app') THEN
   EXECUTE format('GRANT SELECT,INSERT,UPDATE,DELETE ON %I TO advoxs_app,advoxs_system',t);
  END IF;
 END LOOP;
END $$;
-- statement
CREATE FUNCTION team_queue_urgency() RETURNS trigger LANGUAGE plpgsql AS $$
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
 SELECT NEW.tenant_id,'urgency',NEW.id,NEW.urgency_episode_id,u.id,selected_agent
 FROM users u WHERE u.tenant_id=NEW.tenant_id AND u.active AND (
  EXISTS(SELECT 1 FROM agent_lawyers a WHERE a.tenant_id=u.tenant_id
    AND a.agent_id=selected_agent AND a.user_id=u.id AND u.role='lawyer')
  OR (u.role='admin' AND NOT EXISTS(
    SELECT 1 FROM agent_lawyers a JOIN users v ON v.id=a.user_id
    WHERE a.tenant_id=NEW.tenant_id AND a.agent_id=selected_agent
      AND v.active AND v.role='lawyer'))
 ) ON CONFLICT (episode_id,user_id) DO UPDATE
 SET agent_id=EXCLUDED.agent_id, status='pending',available_at=now(),attempts=0
 WHERE team_emails.status='cancelled';
 RETURN NEW;
END $$;
-- statement
CREATE TRIGGER team_urgency BEFORE UPDATE OF urgent_since,current_agent_id,urgent_agent_id
 ON conversations FOR EACH ROW EXECUTE FUNCTION team_queue_urgency();
