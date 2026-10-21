-- Least-privilege grants for the registry.
--
-- AUDIT FINDING (2026-10-21): `anon` and `authenticated` held INSERT, UPDATE,
-- DELETE, TRUNCATE, REFERENCES and TRIGGER on public.rph — every column,
-- including the DG-sourced PII columns (dob, mobile_no, email_id,
-- home_address). Row Level Security blocked the row-level writes only because
-- no permissive write policy exists; TRUNCATE is not RLS-protected at all.
--
-- Context from the migration history, so the story is not overstated:
--   * The PII merge was deliberate (20261007130122 merge_dob_column and
--     siblings added the columns with `GRANT ALL (col) TO service_role` only).
--   * The anon SELECT revoke was deliberate (20261007125739
--     revoke_anon_reads_layer2 revoked SELECT on rph *and* metadata).
--   * What was left behind was the table-level write grant that Supabase
--     applies at creation. RLS blocks it today, but a single future write
--     policy would make the publishable key able to modify — or TRUNCATE —
--     the registry. Least privilege says close it.
--
-- The deployed app does not use the anon key at all — every read goes through
-- the server proxies on the service role (Layer 2). service_role retains full
-- access, so nothing here changes application behaviour.

-- ---------------------------------------------------------------------------
-- rph: the public registry. No writes from a browser-reachable role.
-- ---------------------------------------------------------------------------
revoke insert, update, delete, truncate, references, trigger
  on public.rph from anon, authenticated;

-- No reads either: the PII columns live on this table, and the app never needs
-- anon reads (the proxies use service_role). Defence in depth.
revoke select on public.rph from anon, authenticated;

revoke all on sequence public.pharmacists_id_seq from anon, authenticated;

-- ---------------------------------------------------------------------------
-- metadata: read-only is the only thing anyone needs.
-- ---------------------------------------------------------------------------
revoke insert, update, delete, truncate, references, trigger
  on public.metadata from anon, authenticated;

-- Do NOT grant SELECT back here. Migration 20261007125739
-- (revoke_anon_reads_layer2) deliberately revoked SELECT on `rph` *and*
-- `metadata` from anon/authenticated; the app reads both through the service
-- key. Re-granting would undo that decision for no benefit.

-- ---------------------------------------------------------------------------
-- rph_dg_contacts: captcha-gated PII. Service role only — no grants at all.
-- ---------------------------------------------------------------------------
revoke all on public.rph_dg_contacts from anon, authenticated;

-- ---------------------------------------------------------------------------
-- Verify after applying:
--   select table_name, grantee, privilege_type
--   from information_schema.role_table_grants
--   where table_schema='public'
--     and table_name in ('rph','metadata','rph_dg_contacts')
--     and grantee in ('anon','authenticated');
-- Expect: ZERO rows. postgres and service_role keep their grants.
-- ---------------------------------------------------------------------------
