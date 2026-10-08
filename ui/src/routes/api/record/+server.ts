import { isAuthed } from '#lib/server/auth.js';
import { normalizeReg } from '#lib/server/contacts.js';
import { rateLimitedSearch } from '#lib/server/rateLimit.js';
import type { RequestHandler } from './$types';

const COLS = [
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
].join(',');

/**
 * Session-gated single-record fetch (layer 2: the browser no longer reads
 * the database directly). Reads with the service key server-side and
 * returns the same eleven safe columns the pinned search RPC emits.
 */
export const GET: RequestHandler = async (event) => {
  const { platform, cookies, url } = event;

  if (rateLimitedSearch('record:' + event.getClientAddress())) {
    return new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '60' }
    });
  }

  if (!(await isAuthed(cookies, platform))) {
    return new Response('Unauthorized', { status: 403 });
  }

  const reg = normalizeReg(url.searchParams.get('reg'));
  if (!reg) {
    return new Response('Bad registration number', { status: 400 });
  }

  const env = (platform?.env || {}) as Record<string, string>;
  const surl = env['SUPABASE_URL'];
  const serviceKey = env['SUPABASE_SECRET_KEY'];
  if (!surl || !serviceKey) {
    return new Response('Not configured', { status: 500 });
  }

  try {
    const r = await fetch(
      `${surl}/rest/v1/rph?registration_number=eq.${reg}&select=${COLS}`,
      {
        headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}` }
      }
    );
    if (!r.ok) {
      return new Response('Upstream error', { status: 502 });
    }
    const rows = await r.json();
    if (!Array.isArray(rows) || !rows.length) {
      return new Response('No such record', { status: 404 });
    }
    return new Response(JSON.stringify(rows[0]), {
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
    });
  } catch {
    return new Response('Upstream error', { status: 502 });
  }
};
