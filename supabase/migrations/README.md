# Supabase migrations

Version-controlled SQL for the `tgpc` Supabase project (`vhgpyvzgmvhijqgsapnk`).

These files describe **what production actually has**. They were reconstructed
from the live database on 2026-10-21 (`pg_catalog`, `pg_get_functiondef`,
`information_schema`) rather than written from memory, because the schema had
drifted from `ARCHITECTURE.md` in two ways:

| Drift found | Detail |
|---|---|
| `search_pharmacists` had no `lim` clamp | Documented as `lim := least(greatest(lim,1),200)`; the deployed function used `LIMIT lim` verbatim. Supabase's Max Rows was the only backstop. An empty `q` also matched every row (`ILIKE '%'`). |
| Leftover write grants on `rph` / `metadata` | Reads were deliberately closed by migration `20261007125739` (`revoke_anon_reads_layer2`), and the DG PII columns on `rph` were merged deliberately with `GRANT ALL (col) TO service_role` only. But `anon`/`authenticated` still carry the table-level INSERT/UPDATE/DELETE/TRUNCATE grants Supabase applies at creation. RLS denies the row writes; TRUNCATE is not RLS-protected. |

| File | Purpose | Changes production? |
|---|---|---|
| `20260101000000_baseline.sql` | Tables, columns, generated `tsv`, indexes, extensions. Idempotent. | No — no-ops against the current DB |
| `20260101000001_rls.sql` | RLS enabled + deduplicated policies (metadata had 5 SELECT policies, rph had 2). | Policy names change; effective access is identical |
| `20260101000002_rpc.sql` | `get_rph_stats()`, `search_pharmacists()` — **adds the missing `lim` clamp** and a blank-query guard. | **Yes** (function bodies) |
| `20260101000003_security_hardening.sql` | Revokes INSERT/UPDATE/DELETE/TRUNCATE/REFERENCES/TRIGGER from `anon`/`authenticated` on `rph`; revokes all on `rph_dg_contacts`. | **Yes** (grants) |

## Not applied yet

Nothing here has been run against the database. Apply with the Supabase MCP
`apply_migration` tool or:

```bash
supabase link --project-ref vhgpyvzgmvhijqgsapnk
supabase db push
```

Apply `0002` and `0003` in that order, and verify with the query at the bottom
of `0003`.

## Why `0003` is safe

The deployed app never uses the anon/publishable key — every read goes through
`/api/search`, `/api/refine`, `/api/record`, `/api/stats`, `/api/health`, all of
which use `SUPABASE_SECRET_KEY` server-side. `grep -r anonKey ui/src` returns
nothing. `service_role` bypasses RLS and keeps full access, so revoking from
`anon`/`authenticated` changes no application behaviour.

## Regenerating from the live database

The authoritative dump, if you need to refresh these files:

```sql
-- functions
select proname, pg_get_functiondef(oid) from pg_proc
join pg_namespace n on n.oid = pronamespace
where n.nspname = 'public';

-- policies
select tablename, policyname, roles, cmd, qual, with_check from pg_policies
where schemaname = 'public';

-- grants
select table_name, grantee, privilege_type
from information_schema.role_table_grants
where table_schema = 'public' and grantee in ('anon','authenticated');
```
