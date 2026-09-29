/**
 * "Recently viewed pharmacists" — a small localStorage-backed list shown on
 * the homepage empty state so returning users get one-tap recall.
 *
 * Entries carry only what the strip renders and needs to re-open the profile
 * drawer; the full record is refetched from the API on open, so a stale
 * entry can never show outdated registry data. All storage errors are
 * swallowed: private mode / full quota degrades to "no strip", never to a
 * broken page. Shared storage key prefix style with lib/cache.ts.
 */

export type RecentRecord = {
  /** RPC number, the identity used to re-open the drawer. */
  registration_number: string;
  name: string;
  category: string;
  /** Epoch ms when the profile was last opened — the strip sorts by this. */
  viewedAt: number;
};

const KEY = 'tgpc_recent_rph';
const MAX = 4;

export function recentRecords(): RecentRecord[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(
      (r): r is RecentRecord =>
        !!r && typeof r === 'object' &&
        typeof (r as RecentRecord).registration_number === 'string' &&
        typeof (r as RecentRecord).name === 'string'
    );
  } catch {
    return [];
  }
}

export function recordViewed(r: Pick<RecentRecord, 'registration_number' | 'name' | 'category'>): void {
  try {
    const next: RecentRecord[] = [
      { ...r, registration_number: r.registration_number.trim().toUpperCase(), viewedAt: Date.now() },
      ...recentRecords().filter((x) => x.registration_number !== r.registration_number)
    ].slice(0, MAX);
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {}
}

export function clearRecent(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {}
}
