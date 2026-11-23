ALTER TABLE conversations
 ADD COLUMN human_requested_at timestamptz,
 ADD COLUMN human_request_reason text,
 ADD COLUMN human_request_agent_id uuid REFERENCES agents(id) ON DELETE SET NULL,
 ADD COLUMN human_request_episode_id uuid;
-- statement
CREATE INDEX conversations_pending_human ON conversations(tenant_id,human_requested_at)
 WHERE human_requested_at IS NOT NULL AND deletion_requested_at IS NULL;
-- statement
ALTER TABLE team_emails DROP CONSTRAINT team_emails_kind_check;
-- statement
ALTER TABLE team_emails ADD CONSTRAINT team_emails_kind_check
 CHECK (kind IN ('invitation','urgency','low_credits','credits_depleted','human_request'));
-- statement
CREATE FUNCTION team_queue_human_request() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE selected_agent uuid;
BEGIN
 IF NEW.state='human' THEN
  NEW.human_requested_at := NULL;
  NEW.human_request_reason := NULL;
  NEW.human_request_agent_id := NULL;
 END IF;
 IF NEW.human_requested_at IS NULL THEN
  NEW.human_request_episode_id := NULL;
  RETURN NEW;
 END IF;
 IF NEW.human_request_episode_id IS NULL OR OLD.human_requested_at IS NULL THEN
  NEW.human_request_episode_id := gen_random_uuid();
  INSERT INTO attendance_events(tenant_id,conversation_id,actor_name,action,target_name)
    VALUES(NEW.tenant_id,NEW.id,'Cliente / IA','human_requested',NEW.human_request_reason);
 END IF;
 selected_agent := COALESCE(NEW.human_request_agent_id,NEW.current_agent_id,
   (SELECT id FROM agents WHERE tenant_id=NEW.tenant_id AND is_entry_point LIMIT 1));
 NEW.human_request_agent_id := selected_agent;
 IF NEW.state='agent' AND selected_agent IS NOT NULL THEN
  NEW.current_agent_id := selected_agent;
 END IF;
 -- Testes registram a pendência, mas nunca enviam e-mail à equipe real.
 IF NEW.is_test OR NEW.deletion_requested_at IS NOT NULL THEN RETURN NEW; END IF;
 INSERT INTO team_emails(tenant_id,kind,conversation_id,episode_id,user_id,agent_id)
 SELECT NEW.tenant_id,'human_request',NEW.id,NEW.human_request_episode_id,m.user_id,selected_agent
 FROM office_memberships m JOIN users u ON u.id=m.user_id
 WHERE m.tenant_id=NEW.tenant_id AND m.active AND u.active AND (
  EXISTS(SELECT 1 FROM agent_lawyers a WHERE a.tenant_id=m.tenant_id
    AND a.agent_id=selected_agent AND a.user_id=m.user_id AND m.role='lawyer')
  OR (m.role='admin' AND NOT EXISTS(
    SELECT 1 FROM agent_lawyers a JOIN office_memberships v
      ON v.user_id=a.user_id AND v.tenant_id=a.tenant_id
    JOIN users vi ON vi.id=v.user_id
    WHERE a.tenant_id=NEW.tenant_id AND a.agent_id=selected_agent
      AND v.active AND vi.active AND v.role='lawyer'))
 ) ON CONFLICT (episode_id,user_id) DO UPDATE
 SET agent_id=EXCLUDED.agent_id,status='pending',available_at=now(),attempts=0
 WHERE team_emails.status='cancelled';
 RETURN NEW;
END $$;
-- statement
CREATE TRIGGER team_human_request BEFORE UPDATE OF
 human_requested_at,human_request_agent_id,current_agent_id,state
 ON conversations FOR EACH ROW EXECUTE FUNCTION team_queue_human_request();
-- statement
CREATE FUNCTION requests_human_attendance(message text) RETURNS boolean
 LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE clause text; normalized text;
BEGIN
 normalized := translate(lower(coalesce(message,'')),
   'áàâãäéèêëíìîïóòôõöúùûüç','aaaaaeeeeiiiiooooouuuuc');
 FOR clause IN SELECT regexp_split_to_table(normalized,'[,.!?;\n]+|\mmas\M') LOOP
  IF clause ~ '(^|,\s*)(eu\s+)?(nao|nunca)\s+(quero|preciso|prefiro|desejo|gostaria)' THEN
   CONTINUE;
  END IF;
  -- Apenas pedidos diretos. Identidade ("quero saber se voce e humano"),
  -- negações e relatos de terceiros ficam para a interpretação contextual da IA.
  IF clause ~ '^\s*(por favor\s+)?(eu\s+)?(quero|preciso|prefiro|desejo|gostaria)\s+(de\s+)?((falar|conversar)\s+com\s+|ser atendid[oa]\s+por\s+|atendimento\s+(de\s+|por\s+)?|)(um[a]?\s+)?(humano|human[oa]|pessoa|advogad[oa]|atendente|alguem da equipe)\M'
   OR clause ~ '^\s*(por favor\s+)?(pode me|me)\s+(passar|passe|encaminhar|encaminhe|transferir|transfira)\s+(para\s+)?(um[a]?\s+)?(humano|pessoa|advogad[oa]|atendente)\M'
   OR clause ~ '^\s*(atendimento humano|um humano|uma pessoa|um advogado humano|uma advogada humana)\s*$'
  THEN RETURN true; END IF;
 END LOOP;
 RETURN false;
END $$;
-- statement
CREATE FUNCTION contact_requests_human() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.sender_type='contact' AND requests_human_attendance(NEW.content) THEN
  UPDATE conversations SET human_requested_at=now(),
    human_request_reason='Cliente solicitou atendimento humano',
    human_request_agent_id=current_agent_id
  WHERE id=NEW.conversation_id AND tenant_id=NEW.tenant_id AND state!='human'
    AND human_requested_at IS NULL AND deletion_requested_at IS NULL;
 END IF;
 RETURN NEW;
END $$;
-- statement
CREATE TRIGGER contact_requests_human AFTER INSERT ON messages
 FOR EACH ROW EXECUTE FUNCTION contact_requests_human();
