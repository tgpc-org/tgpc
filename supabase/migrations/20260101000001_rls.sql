-- Row Level Security for the TGPC registry.
--
-- Effective posture (verified 2026-10-21):
--   rph              RLS on, anon SELECT policy, service_role ALL.
--                    anon has NO select *grant*, so the policy is inert today
--                    (and the app never uses the anon key — Layer 2 proxies
--                    run on the service key). Kept as defence in depth.
--   metadata         RLS on, anon SELECT.
--   rph_dg_contacts  RLS on, NO policies, NO grants — service role only.
--
-- Production accumulated duplicate policies over time (metadata had five
-- SELECT policies, rph had two). Dropping them changes nothing semantically —
-- RLS is permissive-OR — but removes the "which one is load-bearing?" trap.
-- All drops are guarded so this file is safe to re-run.

alter table public.rph             enable row level security;
alter table public.metadata        enable row level security;
alter table public.rph_dg_contacts enable row level security;

-- ---------------------------------------------------------------------------
-- rph
-- ---------------------------------------------------------------------------
drop policy if exists "Allow public read access"    on public.rph;
drop policy if exists "anon select rph"             on public.rph;
drop policy if exists "Allow service role full access" on public.rph;

-- Recreated explicitly. Reads stay anon-readable *by policy*; the actual
-- guarantee is that anon holds no SELECT grant (see the hardening migration).
create policy "rph_anon_select" on public.rph
  for select to anon using (true);

create policy "rph_service_all" on public.rph
  for all to service_role using (true) with check (true);

-- ---------------------------------------------------------------------------
-- metadata
-- ---------------------------------------------------------------------------
drop policy if exists "Allow anonymous read"          on public.metadata;
drop policy if exists "Allow public read on metadata" on public.metadata;
drop policy if exists "Allow public select"           on public.metadata;
drop policy if exists "Allow realtime"                on public.metadata;
drop policy if exists "anon select metadata"          on public.metadata;

create policy "metadata_anon_select" on public.metadata
  for select to anon using (true);

-- ---------------------------------------------------------------------------
-- rph_dg_contacts — no policies. RLS with zero policies denies everyone but
-- the service role (which bypasses RLS). This is intentional: the columns are
-- captcha-gated PII at the source.
-- ---------------------------------------------------------------------------
