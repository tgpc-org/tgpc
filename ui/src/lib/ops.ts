/** Shared ops-dashboard types + pure helpers (client-safe, no secrets). */

export interface OpsSectionError {
  error: string;
}

export interface OpsSupabase {
  rph?: number;
  rph_dg_contacts?: number;
  last_sync?: string;
  error?: string;
}

export interface OpsVpsStats {
  done?: number;
  failed?: number;
  updated_at?: string;
  age_min?: number | null;
  alive?: boolean;
  halt?: boolean;
  note?: string;
  fail_by_reason?: Record<string, number>;
  error?: string;
}

export interface OpsCoverage {
  completed?: number;
  terminal?: number;
  uncovered?: number;
  total?: number;
  pct_resolved?: number;
  error?: string;
}

export interface OpsCiRun {
  name: string;
  branch: string;
  result: string;
  created_at: string;
}

export interface OpsSnapshot {
  generated_at: string;
  supabase: OpsSupabase;
  vps: OpsVpsStats;
  ctl: { halt: boolean; note: string; updated_at: string } | OpsSectionError;
  coverage: OpsCoverage;
  ci: OpsCiRun[] | OpsSectionError;
}

export function coveragePct(done: number, terminal: number, total: number): number {
  if (!total || total <= 0) return 0;
  const pct = Math.round((100 * (done + terminal)) / total);
  return Math.min(100, Math.max(0, pct));
}

export function fmtInt(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n)) return '—';
  return n.toLocaleString('en-IN');
}

export function driftTone(drift: number | null): 'ok' | 'bad' | 'unknown' {
  if (drift === null || drift === undefined) return 'unknown';
  return drift === 0 ? 'ok' : 'bad';
}

/** Validate halt/resume POST body. Returns note or throws with a safe message. */
export function parseCtlBody(body: unknown): { halt: boolean; note: string } {
  if (!body || typeof body !== 'object') throw new Error('Bad request');
  const b = body as Record<string, unknown>;
  if (typeof b.halt !== 'boolean') throw new Error('halt must be boolean');
  const raw = typeof b.note === 'string' ? b.note : '';
  const note = raw.trim().slice(0, 140);
  return { halt: b.halt, note };
}
