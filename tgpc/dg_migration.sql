-- DG contact-details as a SEPARATE table (kept apart from `rph`) holding
-- captcha-gated PII — service-role-only access, see the grant block below.
-- Source: TGPC getdetailsdg -> getdetailsviewdg.action (captcha flow).
-- Run in Supabase dashboard -> SQL Editor. Idempotent.
-- No FK to rph on purpose: DG rows must never block legitimate
-- deletes/rewrites of the base table (pipeline prunes removals);
-- orphan prevention is enforced in code (unknown regs are saved with
-- raw_notes for later offline review; sync_cloud refuses to run without
-- rph.json reference).

CREATE TABLE IF NOT EXISTS public.rph_dg_contacts (
  registration_number TEXT PRIMARY KEY,
  dob TEXT,                       -- Date Of Birth, verbatim DD-MM-YYYY
  date_of_registration TEXT,      -- Date Of Registration, verbatim DD-MM-YYYY
  renewal_validity TEXT,          -- Renewal Validity, verbatim DD-MM-YYYY
                                  -- (kept separate from rph.validity_date DD-Mon-YYYY)
  home_address TEXT,              -- home Address (distinct from work/college addresses)
  home_state TEXT,                -- home State
  work_study_address TEXT,        -- Working/Studying Address
  work_study_state TEXT,          -- Working/Studying State
  mobile_no TEXT,                 -- 10-digit Mobile No
  email_id TEXT,                  -- lowercase Email Id
  dg_fetched_at TIMESTAMPTZ       -- UTC ISO of DG capture
);

-- Access posture: SERVICE ROLE ONLY.
--
-- These columns are captcha-gated at the source: TGPC deliberately puts
-- mobile/email/home address behind a captcha on getdetailsdg. Granting
-- `anon SELECT USING (true)` here turned that gate into a single unpaginated
-- dump for anyone holding the publishable key that ships to every browser
-- (audit lead 1: captcha-gated PII anon-readable).
--
-- Only the enrichment pipeline reads this table and it connects with the
-- service_role key (tgpc/details_dg.py :: upsert_dg_batch), so anon and
-- authenticated get nothing. The table privilege is revoked *and* no policy
-- is left behind: dropping the policy alone would leave a future
-- `CREATE POLICY ... USING (true)` one statement away from re-exposing PII.
ALTER TABLE public.rph_dg_contacts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon select rph_dg_contacts" ON public.rph_dg_contacts;
REVOKE ALL ON TABLE public.rph_dg_contacts FROM anon, authenticated;
-- Column-level grants survive a table-level REVOKE, so clear those too.
REVOKE ALL (dob, home_address, home_state, work_study_address, work_study_state, mobile_no, email_id)
  ON TABLE public.rph_dg_contacts FROM anon, authenticated;
-- Pipeline keeps full access: service_role bypasses RLS in Supabase.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.rph_dg_contacts TO service_role;

COMMENT ON TABLE public.rph_dg_contacts IS 'TGPC getdetailsdg contact details (captcha flow), 1:1 by registration_number — captcha-gated PII, service_role only, never anon';
COMMENT ON COLUMN public.rph_dg_contacts.mobile_no IS 'DG getdetailsdg Mobile No, 10-digit';
COMMENT ON COLUMN public.rph_dg_contacts.email_id IS 'DG getdetailsdg Email Id, lowercase';

-- Tracker serial (1:1 with rph.serial_number, human-friendly ordering).
-- Added after initial rollout; re-running this whole file is safe.
ALTER TABLE public.rph_dg_contacts ADD COLUMN IF NOT EXISTS serial_number TEXT;

-- Verify:
-- SELECT COUNT(*) FROM public.rph_dg_contacts WHERE dg_fetched_at IS NOT NULL;
-- SELECT * FROM public.rph_dg_contacts WHERE registration_number = 'TS003261';
-- Expect: `permission denied for table rph_dg_contacts` (not a row count) —
-- run as the anon role, the same role the browser key maps to:
--   SET ROLE anon;
--   SELECT mobile_no FROM public.rph_dg_contacts LIMIT 1;
--   RESET ROLE;
-- Expect 0 rows when checking the catalog after re-running this file:
--   SELECT policyname FROM pg_policies WHERE tablename = 'rph_dg_contacts';
