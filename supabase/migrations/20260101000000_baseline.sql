-- Baseline schema for the TGPC registry.
--
-- Reconstructed from the live project (vhgpyvzgmvhijqgsapnk) on 2026-10-21 via
-- pg_catalog, NOT hand-written from memory. Idempotent: safe to run on an empty
-- database or against the current one (IF NOT EXISTS / CREATE OR REPLACE).
--
-- This file describes what production actually has. If a table changes in the
-- Supabase dashboard, change it here in the same PR.

create extension if not exists pg_trgm;

-- ---------------------------------------------------------------------------
-- rph — the public registry
-- ---------------------------------------------------------------------------
-- `id` is a surrogate PK because `registration_number` has been reissued by
-- the source over time; the pipeline upserts on registration_number.
create sequence if not exists public.pharmacists_id_seq;

create table if not exists public.rph (
  id                   bigint not null default nextval('public.pharmacists_id_seq'),
  serial_number        integer,
  registration_number  text not null,
  name                 text not null,
  father_name          text,
  category             text,
  gender               text default '',
  validity_date        text default '',
  status               text default '',
  education            jsonb default '[]'::jsonb,
  work_experience      jsonb default '{}'::jsonb,
  photo_url            text,
  -- merged from rph_dg_contacts by tgpc/merge_refresh.py
  date_of_registration text,
  home_state           text,
  -- DG-sourced PII. These are NOT anon-readable; see
  -- 20260101000003_security_hardening.sql before touching grants.
  dob                  text,
  home_address         text,
  mobile_no            text,
  email_id             text,
  renewal_validity     text
);

-- Full-text + trigram search vector, kept in lockstep by Postgres itself.
do $$
begin
  if not exists (
    select 1 from information_schema.columns
    where table_schema = 'public' and table_name = 'rph' and column_name = 'tsv'
  ) then
    alter table public.rph add column tsv tsvector generated always as (
      to_tsvector(
        'english'::regconfig,
        coalesce(name, '') || ' ' || coalesce(father_name, '') || ' ' || coalesce(registration_number, '')
      )
    ) stored;
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.rph'::regclass and contype = 'p'
  ) then
    alter table public.rph add constraint pharmacists_pkey primary key (id);
  end if;
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.rph'::regclass and conname = 'pharmacists_registration_number_key'
  ) then
    alter table public.rph add constraint pharmacists_registration_number_key unique (registration_number);
  end if;
end $$;

alter sequence public.pharmacists_id_seq owned by public.rph.id;

-- NOTE: `idx_name_trgm` and `rph_name_trgm` are the same index (both gin on
-- name). Kept identical to production; one can be dropped in a cleanup
-- migration without changing query plans.
create index if not exists idx_father          on public.rph (father_name);
create index if not exists idx_name            on public.rph (name);
create index if not exists idx_registration    on public.rph (registration_number);
create index if not exists idx_name_trgm       on public.rph using gin (name gin_trgm_ops);
create index if not exists rph_name_trgm       on public.rph using gin (name gin_trgm_ops);
create index if not exists rph_reg_trgm        on public.rph using gin (registration_number gin_trgm_ops);
create index if not exists rph_tsv_gin         on public.rph using gin (tsv);
create index if not exists rph_validity_idx    on public.rph (validity_date);

-- ---------------------------------------------------------------------------
-- metadata — key/value store (last_sync, etc.)
-- ---------------------------------------------------------------------------
create table if not exists public.metadata (
  key        text not null,
  value      text not null,
  updated_at timestamptz default now()
);

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.metadata'::regclass and contype = 'p'
  ) then
    alter table public.metadata add constraint metadata_pkey primary key (key);
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- rph_dg_contacts — captcha-gated PII from the DG flow
-- ---------------------------------------------------------------------------
-- 1:1 with rph by registration_number, but deliberately NO foreign key: a DG
-- backfill must never block deleting a record from rph. Service-role only.
create table if not exists public.rph_dg_contacts (
  registration_number text not null,
  serial_number       integer,
  dob                 text,
  date_of_registration text,
  renewal_validity    text,
  home_address        text,
  home_state          text,
  work_study_address  text,
  work_study_state    text,
  mobile_no           text,
  email_id            text,
  dg_fetched_at       timestamptz
);

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conrelid = 'public.rph_dg_contacts'::regclass and contype = 'p'
  ) then
    alter table public.rph_dg_contacts
      add constraint rph_dg_contacts_pkey primary key (registration_number);
  end if;
end $$;
