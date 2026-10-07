import { formatDDMonYYYY } from '$lib/dates';
import { isAuthed } from '$lib/server/auth';
import { rateLimitedSearch } from '$lib/server/rateLimit';
import type { RequestHandler } from './$types';

const SELECT_COLS =
  'registration_number,name,father_name,category,gender,validity_date,status,photo_url';
const HARD_LIMIT = 200;
const CATEGORIES = ['BPharm', 'DPharm', 'MPharm', 'PharmD', 'QC', 'QP'];
const GENDERS = ['Male', 'Female'];
const STATUSES = ['Active', 'Inactive'];

/** Mirror of the client sanitizeQuery (api.ts): strip PostgREST structural
 *  metacharacters and LIKE wildcards so raw input can never alter the
 *  filter expression. Kept inline (not imported) so server code never
 *  pulls in the client database module. */
function clean(s: string, max = 60): string {
  return s
    .replace(/[,()%_*]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, max);
}

/**
 * Session-gated refined search (layer 2: the last browser-held database
 * query). Rebuilds the client filter set server-side with allowlisted
 * enums and sanitized text, then issues one capped PostgREST query with
 * the service key. The row ceiling is a fixed literal — no caller input
 * can raise it.
 */
export const GET: RequestHandler = async (event) => {
  const { platform, cookies, url } = event;

  if (rateLimitedSearch('refine:' + event.getClientAddress())) {
    return new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '60' }
    });
  }

  if (!(await isAuthed(cookies, platform))) {
    return new Response('Unauthorized', { status: 403 });
  }

  const sp = url.searchParams;
  const q = clean(sp.get('q') ?? '', 200);
  const name = clean(sp.get('name') ?? '');
  const father = clean(sp.get('father_name') ?? '');
  const reg = clean(sp.get('registration_number') ?? '');
  const cats = (sp.get('category') ?? '')
    .split(',')
    .map((c) => c.trim())
    .filter((c) => CATEGORIES.includes(c));
  const gender = sp.get('gender') ?? '';
  const status = sp.get('status') ?? '';
  const validTillRaw = (sp.get('valid_till') ?? '').trim();

  const hasQ = q.length >= 3;
  const hasFilters =
    name !== '' ||
    father !== '' ||
    reg !== '' ||
    cats.length > 0 ||
    GENDERS.includes(gender) ||
    STATUSES.includes(status) ||
    validTillRaw !== '';
  if (!hasQ && !hasFilters) {
    return new Response('Nothing to filter', { status: 400 });
  }

  const env = (platform?.env || {}) as Record<string, string>;
  const surl = env['SUPABASE_URL'];
  const serviceKey = env['SUPABASE_SECRET_KEY'];
  if (!surl || !serviceKey) {
    return new Response('Not configured', { status: 500 });
  }

  const params = new URLSearchParams();
  params.set('select', SELECT_COLS);
  if (hasQ) {
    params.set(
      'or',
      `registration_number.ilike.%${q}%,name.ilike.%${q}%,father_name.ilike.%${q}%`
    );
  }
  if (name !== '') params.set('name', `ilike.*${name}*`);
  if (father !== '') params.set('father_name', `ilike.*${father}*`);
  if (reg !== '') params.set('registration_number', `ilike.${reg}*`);
  if (cats.length > 0) params.set('category', `in.(${cats.join(',')})`);
  if (GENDERS.includes(gender)) params.set('gender', `eq.${gender}`);
  if (STATUSES.includes(status)) params.set('status', `eq.${status}`);
  if (validTillRaw !== '') {
    const dbDate = formatDDMonYYYY(validTillRaw);
    if (dbDate) params.set('validity_date', `eq.${dbDate}`);
  }
  params.set('limit', String(HARD_LIMIT));

  try {
    const r = await fetch(`${surl}/rest/v1/rph?${params.toString()}`, {
      headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}` }
    });
    if (!r.ok) {
      return new Response('Upstream error', { status: 502 });
    }
    const rows = await r.json();
    return new Response(JSON.stringify(Array.isArray(rows) ? rows : []), {
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
    });
  } catch {
    return new Response('Upstream error', { status: 502 });
  }
};
