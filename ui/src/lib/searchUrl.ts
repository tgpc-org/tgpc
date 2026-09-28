// URL <-> search state for the home page. The URL is the source of truth on
// entry (shared links, refresh, back/forward); the page pushes canonical
// state back out with replaceState. Only whitelisted keys survive parsing,
// so crafted query strings can't smuggle arbitrary params into state.

import { CATEGORIES } from './colors.ts';

export const SEARCH_SORT_KEYS = ['rank', 'registration_number', 'name', 'category', 'validity_date', 'status'] as const;
export type SearchSortKey = (typeof SEARCH_SORT_KEYS)[number];

export type SearchGender = '' | 'Male' | 'Female';
export type SearchStatus = '' | 'Active' | 'Inactive';

export interface SearchUrlState {
  q: string;
  cat: string; // 'all' or a category name
  sort: SearchSortKey;
  dir: 'asc' | 'desc';
  rpc: string;
  name: string;
  father: string;
  gender: SearchGender;
  status: SearchStatus;
  valid: string; // ISO yyyy-mm-dd or ''
}

export const DEFAULT_SEARCH_STATE: SearchUrlState = {
  q: '',
  cat: 'all',
  sort: 'rank',
  dir: 'asc',
  rpc: '',
  name: '',
  father: '',
  gender: '',
  status: '',
  valid: ''
};

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

function str(sp: URLSearchParams, key: string, max: number): string {
  return (sp.get(key) || '').trim().slice(0, max);
}

function clamp(s: string, allowed: readonly string[], fallback: string): string {
  return allowed.includes(s) ? s : fallback;
}

export function parseSearchUrl(sp: URLSearchParams): SearchUrlState {
  const raw = {
    q: str(sp, 'q', 80),
    cat: str(sp, 'cat', 16),
    sort: str(sp, 'sort', 32),
    dir: str(sp, 'dir', 8),
    rpc: str(sp, 'rpc', 24),
    name: str(sp, 'name', 60),
    father: str(sp, 'father', 60),
    gender: str(sp, 'gender', 10),
    status: str(sp, 'status', 10),
    valid: str(sp, 'valid', 10)
  };
  return {
    q: raw.q,
    cat: clamp(raw.cat, ['all', ...CATEGORIES], 'all'),
    sort: clamp(raw.sort, SEARCH_SORT_KEYS, 'rank') as SearchSortKey,
    dir: raw.dir === 'desc' ? 'desc' : 'asc',
    rpc: raw.rpc,
    name: raw.name,
    father: raw.father,
    gender: clamp(raw.gender, ['Male', 'Female'], '') as SearchGender,
    status: clamp(raw.status, ['Active', 'Inactive'], '') as SearchStatus,
    valid: ISO_DATE.test(raw.valid) ? raw.valid : ''
  };
}

/** Canonical query string: only non-default values, fixed key order. */
export function buildSearchQuery(s: SearchUrlState): URLSearchParams {
  const sp = new URLSearchParams();
  if (s.q) sp.set('q', s.q);
  if (s.cat && s.cat !== 'all') sp.set('cat', s.cat);
  if (s.sort !== 'rank') {
    sp.set('sort', s.sort);
    if (s.dir === 'desc') sp.set('dir', 'desc');
  }
  if (s.rpc) sp.set('rpc', s.rpc);
  if (s.name) sp.set('name', s.name);
  if (s.father) sp.set('father', s.father);
  if (s.gender) sp.set('gender', s.gender);
  if (s.status) sp.set('status', s.status);
  if (s.valid) sp.set('valid', s.valid);
  return sp;
}
