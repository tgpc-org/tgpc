import { envVal } from '#lib/server/appEnv.js';
import { rateLimitedSearch } from '#lib/server/rateLimit.js';
import type { RequestHandler } from './$types';

const MAX_LIM = 200;

/**
 * Public search proxy (the browser never holds a database key). Forwards to
 * the pinned `search_pharmacists` RPC with the service key server-side and
 * clamps `lim` to 1..200 here — the server-side ceiling ARCHITECTURE.md
 * calls for, so no caller can ask for more than the cap by invoking the
 * endpoint directly. Rate-limited per IP; no login required.
 */
export const GET: RequestHandler = async (event) => {
  const { platform, url } = event;

  if (await rateLimitedSearch('search:' + event.getClientAddress(), platform)) {
    return new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '60' }
    });
  }

  const q = (url.searchParams.get('q') || '').trim();
  if (q.length < 1 || q.length > 100) {
    return new Response('Bad query', { status: 400 });
  }
  const rawLim = Number(url.searchParams.get('lim') || MAX_LIM);
  const lim = Math.min(Math.max(Math.floor(rawLim) || MAX_LIM, 1), MAX_LIM);

  const surl = envVal(platform, 'SUPABASE_URL');
  const serviceKey = envVal(platform, 'SUPABASE_SECRET_KEY');
  if (!surl || !serviceKey) {
    return new Response('Not configured', { status: 500 });
  }

  try {
    const r = await fetch(`${surl}/rest/v1/rpc/search_pharmacists`, {
      method: 'POST',
      headers: {
        apikey: serviceKey,
        Authorization: `Bearer ${serviceKey}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ q, lim })
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
