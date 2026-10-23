// Explicit `.ts` specifiers keep this module importable by the unit-test
// runner (`node --experimental-strip-types` does no extension guessing, and
// `api.test.ts` exercises the fetch paths below). Vite and svelte-check
// resolve them the same as the extensionless form.
import type { PharmacistRecord, Notice, DispatchFile, Stats, Category } from './types.ts';
import { MAX_SEARCH_RESULTS } from './searchLimits.ts';

/**
 * Why a browser-side call failed, described in terms the UI can act on.
 *
 * A transport failure is NOT an empty result: answering "No record found" to
 * a 429 or a 502 tells the user a record does not exist when it may well
 * exist. Callers must keep the two cases apart.
 */
export type ApiFailure =
  | { kind: 'rate-limited'; retryAfterSeconds?: number }
  | { kind: 'unavailable' };

/**
 * Outcome of one single-record lookup. Mirrors the server's `lookupRecord`
 * contract (`#lib/server/records.ts`) so both ends name the same cases.
 */
export type RecordLookup =
  | { kind: 'ok'; record: PharmacistRecord }
  | { kind: 'invalid' }
  | { kind: 'not-found' }
  | ApiFailure;

/** Outcome of one search request. `ok` may still carry zero records. */
export type SearchOutcome = { kind: 'ok'; records: PharmacistRecord[] } | ApiFailure;

/**
 * Map a non-2xx response to the failure the UI should surface.
 *
 * `/api/record` sends `Retry-After` with its 429; older/other proxies may
 * omit it, so the header is optional and a missing or unparsable value only
 * costs the countdown.
 */
function failureFrom(response: Response): ApiFailure {
  if (response.status !== 429) return { kind: 'unavailable' };
  // `Number(null)` is 0 and a garbage header is NaN — both fall through to
  // "rate-limited" without a countdown.
  const seconds = Number(response.headers.get('retry-after'));
  return Number.isFinite(seconds) && seconds > 0
    ? { kind: 'rate-limited', retryAfterSeconds: Math.ceil(seconds) }
    : { kind: 'rate-limited' };
}

// Strip PostgREST filter syntax (,()) and LIKE wildcards (%_*) so raw input can
// never alter the fallback .or() expression (CODE_REVIEW.md H4).

// Validate and sanitize raw search input (CODE_REVIEW.md H4).
// Rejects overly long strings; strips characters that could break
// PostgREST filter expressions or cause pathological LIKE patterns.
// Unicode letters (incl. transliterated Indian names) are preserved.
const MAX_QUERY_LENGTH = 200;

function validateQuery(raw: string): string {
  const trimmed = raw.trim();
  if (trimmed.length === 0 || trimmed.length > MAX_QUERY_LENGTH) return '';
  // Reject control characters directly via charCode checks (avoids
  // no-control-regex lint); strip structural metacharacters that could break
  // PostgREST filter expressions or produce pathological LIKE patterns.
  let out = '';
  for (const ch of trimmed) {
    const cp = ch.codePointAt(0)!;
    const isControl = cp < 0x20 || cp === 0x7f;
    if (isControl || ',()%_*'.includes(ch)) {
      out += ' ';
    } else {
      out += ch;
    }
  }
  return out.replace(/\s+/g, ' ').trim();
}


export async function searchRecords(query: string): Promise<SearchOutcome> {
  const q = validateQuery(query);
  if (q.length < 3) return { kind: 'ok', records: [] };
  // Layer 2: search goes through the session-gated server proxy, never
  // directly to the database — the browser holds no database key for this
  // path. Capped at MAX_SEARCH_RESULTS (CODE_REVIEW.md H5); the server
  // clamps again so direct endpoint callers cannot exceed the cap either.
  try {
    const r = await fetch(`/api/search?q=${encodeURIComponent(q)}&lim=${MAX_SEARCH_RESULTS}`);
    if (!r.ok) return failureFrom(r);
    const data = await r.json();
    return { kind: 'ok', records: rankRecords((data as PharmacistRecord[]) || [], q) };
  } catch {
    return { kind: 'unavailable' };
  }
}

export interface AdvancedFilters {
  name?: string;
  father_name?: string;
  registration_number?: string;
  category?: Category[];
  gender?: string;
  status?: string;
  valid_till?: string;
}

// Unified search: live query + advanced refiners as AND (Phase 1 refiners atop live)
export async function searchWithRefiners(query: string, f: AdvancedFilters & { category?: Category[] }): Promise<SearchOutcome> {
  const q = query.trim();
  const hasQ = q.length >= 3;
  const hasFilters = (f.name && f.name.trim()) || (f.father_name && f.father_name.trim()) || (f.registration_number && f.registration_number.trim())
    || (f.category && f.category.length > 0) || (f.gender && f.gender !== 'Any' && f.gender.trim()) || (f.status && f.status !== 'Any' && f.status.trim()) || (f.valid_till && f.valid_till.trim());
  if (!hasQ && !hasFilters) return { kind: 'ok', records: [] };
  // If only live query and no refiners, keep RPC path for ranked results
  if (hasQ && !hasFilters) return searchRecords(query);
  // Otherwise build filtered query (server-side, capped like every query path)
  // Layer 2: refiners go through the session-gated /api/refine proxy, which
  // re-validates every filter server-side and issues one capped query with
  // the service key. The browser holds no database access on this path.
  try {
    const sp = new URLSearchParams();
    if (hasQ) sp.set('q', q);
    if (f.name && f.name.trim()) sp.set('name', f.name.trim());
    if (f.father_name && f.father_name.trim()) sp.set('father_name', f.father_name.trim());
    if (f.registration_number && f.registration_number.trim()) sp.set('registration_number', f.registration_number.trim());
    if (f.category && f.category.length > 0) sp.set('category', f.category.join(','));
    if (f.gender && f.gender !== 'Any' && f.gender.trim()) sp.set('gender', f.gender.trim());
    if (f.status && f.status !== 'Any' && f.status.trim()) sp.set('status', f.status.trim());
    if (f.valid_till && f.valid_till.trim()) sp.set('valid_till', f.valid_till.trim());
    const r = await fetch(`/api/refine?${sp.toString()}`);
    if (!r.ok) return failureFrom(r);
    const rows = ((await r.json()) as PharmacistRecord[]) || [];
    return { kind: 'ok', records: hasQ ? rankRecords(rows, q) : sortRecords(rows) };
  } catch {
    return { kind: 'unavailable' };
  }
}

/**
 * Fetch one record for the profile drawer.
 *
 * Returns a discriminated result rather than `PharmacistRecord | null` so the
 * drawer can say "not found" only when the server actually said 404 — a
 * 429/5xx/offline answer is a failure, not an absence.
 */
export async function getRecord(regNo: string): Promise<RecordLookup> {
  const clean = regNo.trim().toUpperCase();
  if (!clean) return { kind: 'invalid' };
  // Layer 2: single-record fetch goes through the session-gated server
  // proxy — the browser holds no database key for this path either.
  try {
    const r = await fetch(`/api/record?reg=${encodeURIComponent(clean)}`);
    if (r.status === 404) return { kind: 'not-found' };
    if (r.status === 400) return { kind: 'invalid' };
    if (!r.ok) return failureFrom(r);
    return { kind: 'ok', record: (await r.json()) as PharmacistRecord };
  } catch {
    return { kind: 'unavailable' };
  }
}

export async function getStats(): Promise<Stats | null> {
  // Layer 2: stats come from the session-gated server proxy (service key
  // server-side). Same shape as before; polled, not realtime.
  try {
    const r = await fetch('/api/stats');
    if (!r.ok) return null;
    const d = await r.json();
    return (d?.stats as Stats) ?? null;
  } catch {
    return null;
  }
}

export async function getLastSync(): Promise<string> {
  try {
    const r = await fetch('/api/stats');
    if (!r.ok) return '';
    const d = await r.json();
    if (typeof d?.lastSync !== 'string' || !d.lastSync) return '';
    return new Date(d.lastSync)
      .toLocaleString('en-IN', {
        timeZone: 'Asia/Kolkata',
        weekday: 'short',
        day: '2-digit',
        month: 'short',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
      })
      .toUpperCase()
      .replace(/,/g, '');
  } catch {
    return '';
  }
}

export async function fetchNotices(): Promise<Notice[]> {
  try {
    const resp = await fetch('/api/notice');
    if (!resp.ok) throw new Error('Failed to load notices');
    const data = await resp.json();
    data.sort((a: Notice, b: Notice) => b.date.localeCompare(a.date));
    return data;
  } catch {
    return [];
  }
}

export async function fetchDispatchFiles(): Promise<DispatchFile[]> {
  try {
    const resp = await fetch('/api/dispatch');
    if (!resp.ok) throw new Error('API unavailable');
    return await resp.json();
  } catch {
    return [];
  }
}

function parseReg(reg: string): { prefix: string; num: number } {
  const m = reg.match(/^([A-Z]+)(\d+)$/);
  return m ? { prefix: m[1], num: parseInt(m[2], 10) } : { prefix: reg, num: 0 };
}

function sortRecords(data: PharmacistRecord[]): PharmacistRecord[] {
  return [...data].sort((a, b) => {
    const ra = parseReg(a.registration_number);
    const rb = parseReg(b.registration_number);
    if (ra.prefix !== rb.prefix) return ra.prefix.localeCompare(rb.prefix);
    return ra.num - rb.num;
  });
}

function rankRecords(data: PharmacistRecord[], q: string): PharmacistRecord[] {
  const qq = q.toLowerCase();
  function score(r: PharmacistRecord): number {
    const reg = r.registration_number.toLowerCase();
    const name = r.name.toLowerCase();
    const father = (r.father_name || '').toLowerCase();
    if (reg === qq) return 100;
    if (reg.startsWith(qq)) return 90;
    if (name === qq) return 80;
    if (name.startsWith(qq)) return 70;
    if (name.includes(qq)) return 60;
    if (father.includes(qq)) return 40;
    if (reg.includes(qq)) return 30;
    return 0;
  }
  return [...data].sort((a, b) => {
    const sa = score(a), sb = score(b);
    if (sa !== sb) return sb - sa;
    const ra = parseReg(a.registration_number);
    const rb = parseReg(b.registration_number);
    if (ra.prefix !== rb.prefix) return ra.prefix.localeCompare(rb.prefix);
    return ra.num - rb.num;
  });
}
