import { json } from '@sveltejs/kit';
import { env } from '$env/dynamic/public';
import { createClient } from '@supabase/supabase-js';

export async function GET() {
  // NOTE: $env/dynamic/public, not import.meta.env — the latter is the Vite
  // build-time env and is empty for these keys in production, which made
  // health permanently report "Missing Supabase credentials" (503).
  const supabaseUrl = env.PUBLIC_SUPABASE_URL;
  const supabaseKey = env.PUBLIC_SUPABASE_PUBLISHABLE_KEY;

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

  return json(status, {
    status: httpStatus,
    headers: {
      'Cache-Control': 'no-cache, max-age=0',
      'Access-Control-Allow-Origin': '*'
    }
  });
}
