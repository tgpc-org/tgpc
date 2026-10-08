import { getAdminSecret, isAuthed, safeEqual } from '#lib/server/auth.js';
import type { UsageReport, ServiceUsage } from '#lib/types.js';
import type { RequestHandler } from './$types';

function fmt(n: number | null): string {
  if (n === null) return '?';
  return n.toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

async function checkSupabase(env: Record<string, string>): Promise<ServiceUsage> {
  const items: ServiceUsage['items'] = [];
  const pat = env['SUPABASE_PAT'];
  const url = env['SUPABASE_URL'];
  if (!pat || !url) return { name: 'Supabase', items, error: 'Missing SUPABASE_PAT or SUPABASE_URL' };

  const ref = url.match(/https:\/\/([^.]+)\.supabase\.co/)?.[1];
  if (!ref) return { name: 'Supabase', items, error: 'Bad SUPABASE_URL' };

  try {
    const aR = await fetch(`https://api.supabase.com/v1/projects/${ref}/analytics/endpoints/usage.api-requests-count`, {
      headers: { Authorization: `Bearer ${pat}` }
    });
    if (aR.ok) {
      const d: any = await aR.json();
      const count = d.result?.[0]?.count;
      items.push({ label: 'API Requests', used: fmt(count ?? null), limit: null, pct: '-' });
    } else {
      items.push({ label: 'API Requests', used: 'Error ' + aR.status, limit: '', pct: '-' });
    }
  } catch { items.push({ label: 'API', used: 'Error', limit: '', pct: '-' }); }

  try {
    const sqlR = await fetch(`https://api.supabase.com/v1/projects/${ref}/database/query`, {
      method: 'POST', headers: { Authorization: `Bearer ${pat}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: "SELECT (sum(pg_database_size(datname)) / 1073741824.0)::numeric(10,4) as size_gb FROM pg_database" })
    });
    if (sqlR.ok) {
      const d: any = await sqlR.json();
      const gb = parseFloat(d[0]?.size_gb);
      items.push({ label: 'Database Size', used: fmt(gb) + ' GB', limit: null, pct: '-' });
    }

    const stR = await fetch(`https://api.supabase.com/v1/projects/${ref}/database/query`, {
      method: 'POST', headers: { Authorization: `Bearer ${pat}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: "SELECT (sum((metadata->>'size')::int) / (1024.0*1024.0*1024.0))::numeric(10,4) as size_gb FROM storage.objects" })
    });
    if (stR.ok) {
      const d: any = await stR.json();
      const gb = parseFloat(d[0]?.size_gb) || 0;
      items.push({ label: 'Storage Used', used: fmt(gb) + ' GB', limit: null, pct: '-' });
    }
  } catch {}

  return { name: 'Supabase', items };
}

async function checkR2(env: Record<string, string>): Promise<ServiceUsage> {
  const items: ServiceUsage['items'] = [];
  const token = env['CLOUDFLARE_API_TOKEN'];
  const account_id = env['CLOUDFLARE_ACCOUNT_ID'];
  if (!token || !account_id) return { name: 'Cloudflare R2', items, error: 'Missing CLOUDFLARE_API_TOKEN or CLOUDFLARE_ACCOUNT_ID' };

  try {
    const r = await fetch(`https://api.cloudflare.com/client/v4/accounts/${account_id}/r2/buckets/tgpc/usage`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    if (r.ok) {
      const d: any = await r.json();
      const bytes = parseInt(d.result?.payloadSize) || 0;
      const gb = bytes / 1073741824;
      items.push({ label: 'Storage Used', used: fmt(gb) + ' GB', limit: null, pct: '-' });
      items.push({ label: 'Objects', used: fmt(d.result?.objectCount), limit: null, pct: '-' });
    }
  } catch {}

  try {
    const now = new Date().toISOString();
    const monthStart = new Date(new Date().getFullYear(), new Date().getMonth(), 1).toISOString();
    const gql = {
      query: `query { viewer { accounts(filter: {accountTag: "${account_id}"}) { r2OperationsAdaptiveGroups(limit: 10000, filter: { datetime_geq: "${monthStart}", datetime_leq: "${now}" }) { sum { requests } dimensions { actionType } } } } }`
    };
    const gqlR = await fetch(`https://api.cloudflare.com/client/v4/graphql`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(gql)
    });
    if (gqlR.ok) {
      const gd: any = await gqlR.json();
      const groups = gd?.data?.viewer?.accounts?.[0]?.r2OperationsAdaptiveGroups || [];
      const classA = new Set(['PutObject', 'DeleteObject', 'ListObjects', 'CreateMultipartUpload', 'PutBucket', 'DeleteBucket', 'HeadBucket']);
      const classB = new Set(['GetObject', 'HeadObject']);
      let a = 0, b = 0;
      for (const g of groups) {
        const t = g.dimensions?.actionType;
        const c = g.sum?.requests || 0;
        if (classA.has(t)) a += c;
        else if (classB.has(t)) b += c;
      }
      items.push({ label: 'Class A Ops', used: fmt(a), limit: null, pct: '-' });
      items.push({ label: 'Class B Ops', used: fmt(b), limit: null, pct: '-' });
    }
  } catch {}

  return { name: 'Cloudflare R2', items };
}

export const GET: RequestHandler = async ({ request, platform, cookies }) => {
  const env: Record<string, string> = (platform?.env || {}) as Record<string, string>;

  // Fail closed. An unconfigured secret must deny everyone — this handler holds
  // an account-level Supabase PAT and can execute SQL, so an open default is
  // not survivable. (CODE_REVIEW.md finding C3.)
  const adminSecret = getAdminSecret(platform);
  if (!adminSecret) {
    return new Response('Not configured', { status: 500 });
  }

  // Browser callers authenticate with the admin session cookie. The header is
  // kept for non-browser callers (scripts, CI) that hold the secret directly.
  const header = request.headers.get('x-quota-secret');
  const authorized =
    (await isAuthed(cookies, platform)) ||
    (header !== null && (await safeEqual(header, adminSecret)));

  if (!authorized) {
    return new Response('Unauthorized', { status: 403 });
  }

  const missing: string[] = [];
  for (const key of ['SUPABASE_PAT', 'SUPABASE_URL', 'CLOUDFLARE_API_TOKEN', 'CLOUDFLARE_ACCOUNT_ID']) {
    if (!env[key]) missing.push(key);
  }

  const services = await Promise.all([
    checkSupabase(env),
    checkR2(env),
  ]);

  const report: UsageReport = {
    generated_at: new Date().toISOString(),
    services,
    missing_vars: missing,
  };

  return Response.json(report, {
    headers: { 'Cache-Control': 'no-cache, max-age=0' }
  });
}
