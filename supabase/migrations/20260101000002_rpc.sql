-- RPC functions used by the frontend data proxies.
--
-- Both definitions were dumped from the live database (pg_get_functiondef) on
-- 2026-10-21. They were previously maintained only in the Supabase dashboard,
-- which meant the deployed function could drift from ARCHITECTURE.md without
-- anyone noticing — and it had: the documented `lim` clamp was missing.

-- ---------------------------------------------------------------------------
-- get_rph_stats() — total / active / inactive / per-category counts
-- ---------------------------------------------------------------------------
create or replace function public.get_rph_stats()
returns json
language sql
as $function$
  select json_build_object(
    'total',      (select count(*) from rph),
    'active',     (select count(*) from rph where status = 'Active'),
    -- Explicit, not "everything that isn't Active": a record whose status is
    -- empty/NULL was never enriched (e.g. a delisted registration the source
    -- no longer serves), and reporting it as Inactive is a lie about a
    -- pharmacist's standing on a public portal.
    'inactive',   (select count(*) from rph where status = 'Inactive'),
    'unknown',    (select count(*) from rph
                   where status is null or status not in ('Active', 'Inactive')),
    'categories', (select json_object_agg(category, cnt)
                   from (select category, count(*) as cnt from rph group by category) sub)
  );
$function$;

-- ---------------------------------------------------------------------------
-- search_pharmacists(q, lim) — ranked search
--
-- Two deliberate hardening changes vs. the version this replaced:
--   1. `lim` is clamped to 1..200 inside the function. The proxies already
--      clamp, but a direct PostgREST caller asked for 1_000_000 rows and the
--      function honoured it (the only backstop was Supabase's Max Rows).
--   2. A blank `q` returns nothing. Previously `registration_number ILIKE '' || '%'`
--      matched every row, so an empty query dumped the table up to the limit.
-- ---------------------------------------------------------------------------
create or replace function public.search_pharmacists(q text, lim integer)
returns table(
  registration_number text,
  name text,
  father_name text,
  category text,
  gender text,
  validity_date text,
  status text,
  photo_url text,
  serial_number integer,
  education jsonb,
  work_experience jsonb
)
language sql
stable
as $function$
  select r.registration_number, r.name, r.father_name, r.category, r.gender,
         r.validity_date, r.status, r.photo_url, r.serial_number,
         r.education, r.work_experience
  from rph r
  where coalesce(q, '') <> ''
    and (
      r.tsv @@ plainto_tsquery('english', q)
      or r.name % q
      or r.registration_number ilike q || '%'
      or r.name ilike '%' || q || '%'
    )
  order by ts_rank(r.tsv, plainto_tsquery('english', q)) desc,
           similarity(r.name, q) desc,
           r.registration_number
  limit least(greatest(coalesce(lim, 50), 1), 200);
$function$;

grant execute on function public.get_rph_stats()               to anon, authenticated;
grant execute on function public.search_pharmacists(text, integer) to anon, authenticated;
