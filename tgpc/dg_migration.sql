-- DG contact-details as a SEPARATE public table (kept apart from `rph`).
-- Source: TGPC getdetailsdg -> getdetailsviewdg.action (captcha flow).
-- Run in Supabase dashboard -> SQL Editor. Idempotent.
-- No FK to rph on purpose: DG rows must never block legitimate
-- deletes/rewrites of the base table (pipeline prunes removals);
-- orphan prevention is enforced in code (identity guard + unknown_reg
-- quarantine, sync_cloud refuses to run without rph.json reference).

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

-- Public read-only posture, mirroring rph (all DG data is public):
ALTER TABLE public.rph_dg_contacts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "anon select rph_dg_contacts" ON public.rph_dg_contacts;
CREATE POLICY "anon select rph_dg_contacts" ON public.rph_dg_contacts
  FOR SELECT TO anon USING (true);

COMMENT ON TABLE public.rph_dg_contacts IS 'TGPC getdetailsdg contact details (captcha flow), 1:1 by registration_number';
COMMENT ON COLUMN public.rph_dg_contacts.mobile_no IS 'DG getdetailsdg Mobile No, 10-digit';
COMMENT ON COLUMN public.rph_dg_contacts.email_id IS 'DG getdetailsdg Email Id, lowercase';

-- Tracker serial (1:1 with rph.serial_number, human-friendly ordering).
-- Added after initial rollout; re-running this whole file is safe.
ALTER TABLE public.rph_dg_contacts ADD COLUMN IF NOT EXISTS serial_number TEXT;

-- Verify:
-- SELECT COUNT(*) FROM public.rph_dg_contacts WHERE dg_fetched_at IS NOT NULL;
-- SELECT * FROM public.rph_dg_contacts WHERE registration_number = 'TS003261';
