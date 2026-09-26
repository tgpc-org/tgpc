import type { PharmacistRecord, Notice, DispatchFile, Stats, Category } from './types';
import { supabase } from './supabase';
import { MAX_SEARCH_RESULTS } from './searchLimits';
import { formatDDMonYYYY } from './dates';

// Strip PostgREST filter syntax (,()) and LIKE wildcards (%_*) so raw input can
// never alter the fallback .or() expression (CODE_REVIEW.md H4).
function sanitizeQuery(s: string): string {
  return s.replace(/[,()%_*]/g, ' ').replace(/\s+/g, ' ').trim();
}

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

// ilike values are escaped by supabase-js, but % and _ would still act as
// wildcards — strip them so user input matches literally.
function stripWildcards(s: string): string {
  return s.replace(/[%_*]/g, ' ').replace(/\s+/g, ' ').trim();
}

export async function searchRecords(query: string): Promise<PharmacistRecord[]> {
  const q = validateQuery(query);
  if (q.length < 3) return [];
  try {
    // Capped at MAX_SEARCH_RESULTS (CODE_REVIEW.md H5) — previously `lim: 100000`,
    // which pulled essentially the whole registry into the browser on a broad query.
    const { data, error } = await supabase.rpc('search_pharmacists', { q, lim: MAX_SEARCH_RESULTS });
    if (error) throw error;
    return rankRecords((data as PharmacistRecord[]) || [], q);
  } catch {
    try {
      const safe = sanitizeQuery(q);
      const { data } = await supabase
        .from('rph')
        .select('registration_number, name, father_name, category, gender, validity_date, status, photo_url')
        .or(`registration_number.ilike.%${safe}%,name.ilike.%${safe}%`)
        .limit(MAX_SEARCH_RESULTS);
      return rankRecords((data as PharmacistRecord[]) || [], q);
    } catch {
      return [];
    }
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
export async function searchWithRefiners(query: string, f: AdvancedFilters & { category?: Category[] }): Promise<PharmacistRecord[]> {
  const q = query.trim();
  const hasQ = q.length >= 3;
  const hasFilters = (f.name && f.name.trim()) || (f.father_name && f.father_name.trim()) || (f.registration_number && f.registration_number.trim())
    || (f.category && f.category.length > 0) || (f.gender && f.gender !== 'Any' && f.gender.trim()) || (f.status && f.status !== 'Any' && f.status.trim()) || (f.valid_till && f.valid_till.trim());
  if (!hasQ && !hasFilters) return [];
  // If only live query and no refiners, keep RPC path for ranked results
  if (hasQ && !hasFilters) return searchRecords(query);
  // Otherwise build filtered query (server-side, capped like every other path)
  try {
    let qb = supabase.from('rph').select('registration_number, name, father_name, category, gender, validity_date, status, photo_url');
    if (hasQ) {
      const safe = sanitizeQuery(q);
      qb = qb.or(`registration_number.ilike.%${safe}%,name.ilike.%${safe}%,father_name.ilike.%${safe}%`);
    }
    if (f.name && f.name.trim()) qb = qb.ilike('name', `%${stripWildcards(f.name)}%`);
    if (f.father_name && f.father_name.trim()) qb = qb.ilike('father_name', `%${stripWildcards(f.father_name)}%`);
    if (f.registration_number && f.registration_number.trim()) qb = qb.ilike('registration_number', `${stripWildcards(f.registration_number)}%`);
    if (f.category && f.category.length > 0) qb = qb.in('category', f.category);
    if (f.gender && f.gender !== 'Any' && f.gender.trim()) qb = qb.eq('gender', f.gender);
    if (f.status && f.status !== 'Any' && f.status.trim()) qb = qb.eq('status', f.status);
    if (f.valid_till && f.valid_till.trim()) {
      const dbDate = formatDDMonYYYY(f.valid_till);
      if (dbDate) qb = qb.eq('validity_date', dbDate);
    }
    qb = qb.limit(MAX_SEARCH_RESULTS);
    const { data, error } = await qb;
    if (error) throw error;
    const rows = (data as PharmacistRecord[]) || [];
    return hasQ ? rankRecords(rows, q) : sortRecords(rows);
  } catch {
    return [];
  }
}

export async function getRecord(regNo: string): Promise<PharmacistRecord | null> {
  const clean = regNo.trim().toUpperCase();
  if (!clean) return null;
  try {
    const { data, error } = await supabase
      .from('rph')
      .select('registration_number, name, father_name, category, gender, validity_date, status, photo_url, serial_number, education, work_experience')
      .eq('registration_number', clean)
      .single();
    if (error || !data) return null;
    return data as PharmacistRecord;
  } catch {
    return null;
  }
}

export async function getStats(): Promise<Stats | null> {
  try {
    const { data, error } = await supabase.rpc('get_rph_stats');
    if (error) throw error;
    if (data && typeof data === 'object') {
      const d = data as { total: number; active: number; inactive: number; categories: Record<string, number> };
      return {
        total: d.total ?? 0,
        active: d.active ?? 0,
        inactive: d.inactive ?? 0,
        BPharm: d.categories?.BPharm ?? 0,
        DPharm: d.categories?.DPharm ?? 0,
        MPharm: d.categories?.MPharm ?? 0,
        PharmD: d.categories?.PharmD ?? 0,
        QC: d.categories?.QC ?? 0,
        QP: d.categories?.QP ?? 0
      };
    }
    return null;
  } catch {
    return null;
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
