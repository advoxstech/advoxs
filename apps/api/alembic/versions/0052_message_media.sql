CREATE TABLE message_media (
 message_id uuid PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
 tenant_id uuid NOT NULL REFERENCES tenants(id),
 kind varchar(16) NOT NULL CHECK (kind IN ('audio','document','image')),
 status varchar(16) NOT NULL CHECK (status IN ('pending','processing','done','failed','skipped')),
 attempts integer NOT NULL DEFAULT 0,
 error_code varchar(32) CHECK (error_code IN ('download_failed','timeout','provider_unavailable',
  'invalid_format','too_large','too_long','silence','no_credits','billing_gate')),
 text text,
 uncertain_spans jsonb,
 duration_seconds numeric(8,2),
 provider varchar(64),
 cost_usd numeric(10,6),
 credits_consumed numeric(12,4),
 started_at timestamptz,
 finished_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now()
);
-- statement
CREATE INDEX message_media_tenant_status ON message_media(tenant_id, status);
-- statement
ALTER TABLE message_media ENABLE ROW LEVEL SECURITY;
-- statement
CREATE POLICY tenant_isolation ON message_media
 USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
 WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid);
-- statement
DO $$ BEGIN
 IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'advoxs_app') THEN
  GRANT SELECT, INSERT, UPDATE, DELETE ON message_media TO advoxs_app, advoxs_system;
 END IF;
END $$;
-- statement
ALTER TABLE pricing_configs ADD COLUMN transcription_credits_per_minute numeric(8,4);
-- statement
INSERT INTO pricing_configs
 (id, tokens_per_credit, input_weight, output_weight, transcription_credits_per_minute, effective_at)
SELECT gen_random_uuid(), tokens_per_credit, input_weight, output_weight, 1.0, now()
FROM pricing_configs
WHERE effective_at <= now()
ORDER BY effective_at DESC
LIMIT 1;
