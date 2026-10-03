-- rph_dg_contacts.serial_number: TEXT -> INTEGER.
--
-- Why: the tracker is 1:1 with rph.serial_number (INTEGER) and used for
-- ordering; TEXT sorts lexicographically ('100' < '99') and wastes space.
-- Audit 2026-10: all 36,632 live values numeric, zero NULLs — the cast is
-- total. Run in Supabase dashboard -> SQL Editor. Guarded: safe to re-run.
--
-- After applying, the pipeline sends integers (build_supabase_payload).

DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'rph_dg_contacts'
      AND column_name = 'serial_number' AND data_type = 'text'
  ) THEN
    ALTER TABLE public.rph_dg_contacts
      ALTER COLUMN serial_number TYPE INTEGER USING serial_number::INTEGER;
    RAISE NOTICE 'serial_number migrated to INTEGER';
  ELSE
    RAISE NOTICE 'serial_number already INTEGER — nothing to do';
  END IF;
END $$;

-- Verify:
--   SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name = 'rph_dg_contacts' AND column_name = 'serial_number';
-- Expect: integer.
