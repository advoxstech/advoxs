ALTER TABLE team_emails DROP CONSTRAINT IF EXISTS team_emails_kind_check;
-- statement
ALTER TABLE team_emails ADD CONSTRAINT team_emails_kind_check
 CHECK (kind IN ('invitation','urgency','low_credits','credits_depleted'));
-- statement
-- Same threshold used by the worker before sending: 20% of the latest purchase,
-- falling back to 100 credits for offices without a recorded purchase.
CREATE OR REPLACE FUNCTION low_credits_threshold(office uuid) RETURNS numeric
 LANGUAGE sql STABLE SECURITY DEFINER SET search_path FROM CURRENT AS $$
 SELECT COALESCE((SELECT amount_credits * 0.2 FROM credit_transactions
   WHERE tenant_id=office AND type='purchase' AND amount_credits > 0
   ORDER BY created_at DESC LIMIT 1), 100)
$$;
-- statement
-- Every debit path (worker, test conversations, summaries) goes through this
-- trigger. SECURITY DEFINER keeps the queue insert independent of the caller's
-- RLS context, and a notification failure never aborts the debit itself.
CREATE OR REPLACE FUNCTION team_queue_credit_alert() RETURNS trigger
 LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $$
DECLARE alert text; threshold numeric;
BEGIN
 IF NEW.credit_balance >= OLD.credit_balance THEN RETURN NEW; END IF;
 IF OLD.credit_balance > 0 AND NEW.credit_balance <= 0 THEN
  alert := 'credits_depleted';
 ELSE
  threshold := low_credits_threshold(NEW.id);
  IF OLD.credit_balance > threshold AND NEW.credit_balance <= threshold THEN
   alert := 'low_credits';
  END IF;
 END IF;
 IF alert IS NULL THEN RETURN NEW; END IF;
 BEGIN
  INSERT INTO team_emails(tenant_id,kind,episode_id,user_id)
  SELECT NEW.id, alert, episode, m.user_id
  FROM (SELECT gen_random_uuid() AS episode) e, office_memberships m
  JOIN users u ON u.id=m.user_id
  WHERE m.tenant_id=NEW.id AND m.role='admin' AND m.active AND u.active;
 EXCEPTION WHEN OTHERS THEN
  RAISE WARNING 'credit alert not queued: %', SQLSTATE;
 END;
 RETURN NEW;
END $$;
-- statement
CREATE TRIGGER team_queue_credit_alert AFTER UPDATE OF credit_balance ON tenants
 FOR EACH ROW EXECUTE FUNCTION team_queue_credit_alert();
