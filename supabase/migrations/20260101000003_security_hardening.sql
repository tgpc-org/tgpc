-- Least-privilege grants for the registry.
--
-- AUDIT FINDING (2026-10-21): `anon` and `authenticated` held INSERT, UPDATE,
-- DELETE, TRUNCATE, REFERENCES and TRIGGER on public.rph — every column,
-- including the DG-sourced PII columns (dob, mobile_no, email_id,
-- home_address). Row Level Security blocked the row-level writes only because
-- no permissive write policy exists; TRUNCATE is not RLS-protected at all.
--
-- Today `anon` holds no SELECT grant, so the public key cannot read the table
-- (verified: 0 SELECT column privileges). But the combination
--   permissive anon SELECT policy  +  a table that carries 36k mobiles/emails
-- is one `GRANT SELECT` away from a PII leak. This migration makes the
-- intended posture explicit instead of accidental.
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

-- metadata holds no PII; keep it readable so a client-side status check still
-- works if the publishable key is ever reintroduced.
grant select on public.metadata to anon, authenticated;

-- ---------------------------------------------------------------------------
-- rph_dg_contacts: captcha-gated PII. Service role only — no grants at all.
-- ---------------------------------------------------------------------------
revoke all on public.rph_dg_contacts from anon, authenticated;

-- ---------------------------------------------------------------------------
-- Verify after applying:
--   select grantee, privilege_type from information_schema.role_table_grants
--   where table_schema='public' and table_name in ('rph','rph_dg_contacts')
--     and grantee in ('anon','authenticated');
-- Expect: metadata SELECT only. Nothing on rph or rph_dg_contacts.
-- ---------------------------------------------------------------------------
