-- ---------------------------------------------------------------------------
-- Shared rate-limit counter (audit F3 / F7).
--
-- Why the database and not Cloudflare KV: the primary fix (an edge Rate
-- Limiting rule) needs a zone, and this deployment has none - it is served
-- from `tgpc.pages.dev`, which is Cloudflare-owned. A KV binding was the next
-- option, but bindings are unreachable from application code here:
-- adapter-cloudflare v8 does not pass `platform` to `server.respond`, so
-- `event.platform` is undefined in production and only `$app/env` values are
-- resolvable (see ui/src/lib/server/appEnv.ts and F13 in the audit record).
-- The Supabase service key is already resolved through `$app/env` and is
-- proven working in production, so it is the only shared, atomic store
-- available without new credentials.
--
-- The credential limiter consults this on every attempt (volume is inherently
-- tiny). The high-frequency public limiter only consults it once an isolate's
-- local budget is spent, so ordinary search traffic performs no SQL.
--
-- Buckets are SHA-256 hashes of `kind:client-address`, never raw addresses:
-- this table exists to stop abuse, not to build an IP log. Rows expire after
-- an hour via the opportunistic delete inside the function, so no cron is
-- needed.
-- ---------------------------------------------------------------------------

create table if not exists public.rate_limit (
  bucket       text        not null,
  window_start timestamptz not null,
  hits         integer     not null default 0,
  primary key (bucket, window_start)
);

-- Supports the expired-row sweep, which filters on window_start alone.
create index if not exists rate_limit_window_start_idx
  on public.rate_limit (window_start);

-- Browser-reachable roles have no business here at all. The counter is only
-- ever written by the server proxies on the service key.
alter table public.rate_limit enable row level security;
revoke all on public.rate_limit from anon, authenticated;
grant all on public.rate_limit to service_role;

create or replace function public.bump_rate_limit(
  p_bucket text,
  p_window_seconds integer,
  p_max integer
)
returns boolean
language plpgsql
security definer
set search_path = public
as $function$
declare
  -- Fixed window: truncate now() to a p_window_seconds boundary so concurrent
  -- callers across isolates agree on the bucket, and the upsert below is the
  -- atomic point of serialisation.
  w       timestamptz := to_timestamp(
                          floor(extract(epoch from now()) / p_window_seconds)
                          * p_window_seconds);
  n       integer;
begin
  if p_window_seconds is null or p_window_seconds < 1 or p_max is null or p_max < 1 then
    -- Never let bad parameters disable the limiter silently.
    return false;
  end if;

  insert into public.rate_limit as rl (bucket, window_start, hits)
  values (p_bucket, w, 1)
  on conflict (bucket, window_start)
    do update set hits = rl.hits + 1
  returning rl.hits into n;

  delete from public.rate_limit where window_start < now() - interval '1 hour';

  return n <= p_max;
end;
$function$;

revoke all on function public.bump_rate_limit(text, integer, integer)
  from public, anon, authenticated;
grant execute on function public.bump_rate_limit(text, integer, integer)
  to service_role;

-- ---------------------------------------------------------------------------
-- Verify after applying (as service_role / postgres):
--   select public.bump_rate_limit('selftest', 60, 3);  -- true,  true,  true
--   select public.bump_rate_limit('selftest', 60, 3);  -- false (4th)
--   delete from public.rate_limit where bucket = 'selftest';
--
-- And that anon cannot touch it:
--   select table_name, grantee, privilege_type
--   from information_schema.role_table_grants
--   where table_schema='public' and table_name='rate_limit'
--     and grantee in ('anon','authenticated');
-- Expect: zero rows.
-- ---------------------------------------------------------------------------
