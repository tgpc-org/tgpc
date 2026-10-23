import { PUBLIC_SUPABASE_URL } from '$app/env/public';
import { SUPABASE_SECRET_KEY } from '$app/env/private';
import { createClient } from '@supabase/supabase-js';
import { rateLimitedStrict } from '#lib/server/rateLimitShared.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async ({ platform, getClientAddress }) => {
  // Each call costs two PostgREST queries, so an unauthenticated caller could
  // use this as a cheap database load generator. Monitors poll once a minute
  // at most; the read tier (120/min per IP) leaves plenty of headroom.
  if (await rateLimitedStrict('health:' + getClientAddress(), platform, 30)) {
    return new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '60' }
    });
  }

  // Credentials come from $app/env (SvelteKit 3): the adapter wires these
  // names to the Worker bindings declared in src/env.ts. `import.meta.env` is
  // the Vite build-time env and is empty for them in production — reading it
  // here made health report "Missing Supabase credentials" (503) forever.
  // The service key is server-only; health must keep working after anonymous
  // database access is revoked.
  const supabaseUrl = PUBLIC_SUPABASE_URL;

  const supabaseKey = SUPABASE_SECRET_KEY;


  interface CheckResult {
    status: 'ok' | 'down' | 'stale';
    latency_ms?: number;
    error?: string;
    value?: string;
    hours_ago?: number;
  }

  const checks: { supabase: CheckResult; last_sync: CheckResult } = {
    supabase: { status: 'down' },
    last_sync: { status: 'down' }
  };

  // Check Supabase connectivity
  if (supabaseUrl && supabaseKey) {
    try {
      const start = Date.now();
      const supabase = createClient(supabaseUrl, supabaseKey);
      const { error } = await supabase.from('rph').select('registration_number').limit(1);
      const latency = Date.now() - start;

      if (!error) {
        checks.supabase = { status: 'ok', latency_ms: latency };
      } else {
        checks.supabase = { status: 'down', error: error.message };
      }

      // Check last_sync freshness
      const { data: syncData, error: syncError } = await supabase
        .from('metadata')
        .select('value')
        .eq('key', 'last_sync')
        .single();

      if (!syncError && syncData?.value) {
        const lastSync = new Date(syncData.value);
        const hoursAgo = (Date.now() - lastSync.getTime()) / (1000 * 60 * 60);

        checks.last_sync = {
          status: hoursAgo < 48 ? 'ok' : 'stale',
          value: lastSync.toISOString(),
          hours_ago: Math.round(hoursAgo * 10) / 10
        };
      }
    } catch (e) {
      checks.supabase = { status: 'down', error: String(e) };
    }
  } else {
    checks.supabase = { status: 'down', error: 'Missing Supabase credentials' };
  }

  // Determine overall status
  const allOk = checks.supabase.status === 'ok' && checks.last_sync.status === 'ok';
  const degraded = checks.supabase.status === 'ok' && checks.last_sync.status === 'stale';

  const status = {
    status: allOk ? 'ok' : degraded ? 'degraded' : 'down',
    timestamp: new Date().toISOString(),
    version: '2.0.0',
    checks
  };

  const httpStatus = status.status === 'down' ? 503 : 200;

  return Response.json(status, {
    status: httpStatus,
    headers: {
      'Cache-Control': 'no-cache, max-age=0',
      'Access-Control-Allow-Origin': '*'
    }
  });
}
