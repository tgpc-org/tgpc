import { envVal } from '#lib/server/appEnv.js';
import { edgeCached } from '#lib/server/edgeCache.js';
import { rateLimitedSearch } from '#lib/server/rateLimitShared.js';
import type { RequestHandler } from './$types';

/**
 * Stats move only when the pipeline syncs (a few times a day at most), yet
 * every open tab polls this endpoint every 60 s — the site's highest-volume
 * database path. Cache the payload at the edge for 30 s so N visitors cost
 * one pair of Postgres queries per PoP per half-minute instead of two per
 * visitor per minute. Worst-case drift is poll (60 s) + cache (30 s) ≈ 90 s,
 * which is well inside what the strip already tolerates.
 */
const TTL_SECONDS = 30;

/**
 * Public stats + last-sync payload. Reads with the service key server-side;
 * the browser never touches the database for stats. Polled every 60s by the
 * layout instead of a realtime channel. Rate-limited per IP; no login
 * required.
 */
export const GET: RequestHandler = async (event) => {
  const { platform, url } = event;

  // The limiter runs before the cache: a HIT still costs the caller one unit
  // of its per-IP budget, so caching cannot be used to hammer the endpoint.
  if (await rateLimitedSearch('stats:' + event.getClientAddress(), platform)) {
    return new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '60' }
    });
  }

  return edgeCached(url.origin + '/api/stats', TTL_SECONDS, () => buildStats(platform));
};

async function buildStats(platform: App.Platform | undefined): Promise<Response> {
  const surl = envVal(platform, 'SUPABASE_URL');
  const serviceKey = envVal(platform, 'SUPABASE_SECRET_KEY');
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
}
