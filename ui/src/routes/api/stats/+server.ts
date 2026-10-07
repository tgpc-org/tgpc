import { isAuthed } from '$lib/server/auth';
import { rateLimitedSearch } from '$lib/server/rateLimit';
import type { RequestHandler } from './$types';

/**
 * Session-gated stats + last-sync payload (layer 2: replaces the realtime
 * metadata subscription and the anon-key RPC call). Reads with the service
 * key server-side; the browser never touches the database for stats.
 * Polled every 60s by the layout instead of a realtime channel.
 */
export const GET: RequestHandler = async (event) => {
  const { platform, cookies } = event;

  if (rateLimitedSearch('stats:' + event.getClientAddress())) {
    return new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '60' }
    });
  }

  if (!(await isAuthed(cookies, platform))) {
    return new Response('Unauthorized', { status: 403 });
  }

  const env = (platform?.env || {}) as Record<string, string>;
  const surl = env['SUPABASE_URL'];
  const serviceKey = env['SUPABASE_SECRET_KEY'];
  if (!surl || !serviceKey) {
    return new Response('Not configured', { status: 500 });
  }
  const headers = {
    apikey: serviceKey,
    Authorization: `Bearer ${serviceKey}`,
    'Content-Type': 'application/json'
  };

  try {
    const [statsR, syncR] = await Promise.all([
      fetch(`${surl}/rest/v1/rpc/get_rph_stats`, { method: 'POST', headers, body: '{}' }),
      fetch(`${surl}/rest/v1/metadata?key=eq.last_sync&select=value`, { headers })
    ]);
    if (!statsR.ok) {
      return new Response('Upstream error', { status: 502 });
    }
    const d = await statsR.json();
    let lastSync = '';
    if (syncR.ok) {
      const rows = await syncR.json();
      // Raw ISO only — locale formatting happens in the browser, whose ICU
      // is complete. Workers runtimes may lack it (RangeError), which would
      // 502 this whole endpoint and blank the stats strip.
      if (Array.isArray(rows) && typeof rows[0]?.value === 'string') {
        lastSync = rows[0].value;
      }
    }
    const stats = {
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
    return new Response(JSON.stringify({ stats, lastSync }), {
      headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
    });
  } catch {
    return new Response('Upstream error', { status: 502 });
  }
};
