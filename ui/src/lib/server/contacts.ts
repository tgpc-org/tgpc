/**
 * Admin-only contact lookup helpers (server-only).
 *
 * PII from `rph_dg_contacts` never enters the public bundle: it is read
 * service-side (Supabase management API, account PAT) and served only to a
 * valid admin session by `routes/api/admin/contacts/+server.ts`. This module
 * holds the pure pieces — registration validation and row shaping — so they
 * are unit-testable without credentials.
 */

export interface DgContact {
  dob: string | null;
  date_of_registration: string | null;
  renewal_validity: string | null;
  home_address: string | null;
  home_state: string | null;
  work_study_address: string | null;
  work_study_state: string | null;
  mobile_no: string | null;
  email_id: string | null;
}

const REG_RE = /^(TS|TG|TSDR|TGDR)\d+$/;

/** Normalize a raw `?reg=` value, or null when it is not a valid reg number. */
export function normalizeReg(input: unknown): string | null {
  if (typeof input !== 'string') return null;
  const reg = input.trim().toUpperCase();
  return REG_RE.test(reg) ? reg : null;
}

const DG_KEYS = [
  'dob',
  'date_of_registration',
  'renewal_validity',
  'home_address',
  'home_state',
  'work_study_address',
  'work_study_state',
  'mobile_no',
  'email_id'
] as const;

const BASE_KEYS = [
  'registration_number',
  'name',
  'father_name',
  'category',
  'gender',
  'validity_date',
  'status',
  'photo_url',
  'serial_number',
  'education',
  'work_experience'
] as const;

/** Allowlisted projection: unexpected columns from either table never leak through. */
export function shapeRow<T extends string>(
  row: Record<string, unknown>,
  keys: readonly T[]
): Record<T, unknown> {
  const out = {} as Record<T, unknown>;
  for (const k of keys) out[k] = row[k] ?? null;
  return out;
}

export function shapeBase(row: Record<string, unknown>) {
  return shapeRow(row, BASE_KEYS);
}

export function shapeContact(row: Record<string, unknown>): DgContact {
  return shapeRow(row, DG_KEYS) as unknown as DgContact;
}

/** True when a shaped contact carries no details (no DG row, or an all-blank one). */
export function isContactEmpty(c: DgContact): boolean {
  return (Object.keys(c) as (keyof DgContact)[]).every((k) => {
    const v = c[k];
    return typeof v === 'string' ? v === '' : v === null;
  });
}
