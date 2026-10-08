/**
 * Server-only ops snapshot + R2 control channel.
 *
 * Sources (every one best-effort, per-section errors — one dead source never
 * blanks the page):
 * - Supabase (service key): exact counts for rph / rph_dg_contacts + metadata.last_sync
 * - R2 private DG bucket ops/: dg_stats.json (loop heartbeat) + ctl.json (halt flag)
 *   + dg_fetch_checkpoint.json (coverage: completed / terminal lengths)
 * - GitHub Actions: latest runs for tgpc-org/tgpc (unauthenticated, short timeout)
 *
 * Auth model matches the other admin endpoints: session cookie, fail-closed
 * when ADMIN_SECRET is unconfigured. PII never enters logs.
 */

import { createClient } from '@supabase/supabase-js';
import type { OpsSnapshot } from '#lib/ops.js';
import { envVal } from './appEnv.js';

const enc = new TextEncoder();

function cfg(platform: App.Platform | undefined) {
  const supabaseUrl = envVal(platform, 'SUPABASE_URL', 'PUBLIC_SUPABASE_URL') || '';
  const serviceKey = envVal(platform, 'SUPABASE_SECRET_KEY') || '';
  const accountId = envVal(platform, 'CLOUDFLARE_ACCOUNT_ID') || '';
  const accessKey = envVal(platform, 'R2_ACCESS_KEY_ID') || '';
  const secretKey = envVal(platform, 'R2_SECRET_ACCESS_KEY') || '';
  const bucket = envVal(platform, 'TGPC_R2_DG_BUCKET') || '';
  const githubToken = envVal(platform, 'GITHUB_TOKEN', 'GITHUB_PAT') || '';
  return { supabaseUrl, serviceKey, accountId, accessKey, secretKey, bucket, githubToken };
}

// --- SHA-256 / HMAC via WebCrypto (Workers-safe) ---

async function sha256Hex(data: string | Uint8Array): Promise<string> {
  const bytes = typeof data === 'string' ? enc.encode(data) : data;
  const digest = await crypto.subtle.digest('SHA-256', bytes as BufferSource);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function hmacSha256(key: Uint8Array | ArrayBuffer, msg: string): Promise<Uint8Array> {
  const cryptoKey = await crypto.subtle.importKey(
    'raw',
    key as BufferSource,
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  const sig = await crypto.subtle.sign('HMAC', cryptoKey, enc.encode(msg));
  return new Uint8Array(sig);
}

async function signingKey(secret: string, date: string): Promise<Uint8Array> {
  const kDate = await hmacSha256(enc.encode('AWS4' + secret), date);
  const kRegion = await hmacSha256(kDate, 'auto');
  const kService = await hmacSha256(kRegion, 's3');
  return hmacSha256(kService, 'aws4_request');
}

function toHex(bytes: Uint8Array): string {
  return [...bytes].map((b) => b.toString(16).padStart(2, '0')).join('');
}

/** Signed fetch against R2's S3 endpoint (GET or PUT). Throws on non-2xx. */
export async function r2Fetch(
  platform: App.Platform | undefined,
  key: string,
  method: 'GET' | 'PUT',
  body?: string
): Promise<string> {
  const { accountId, accessKey, secretKey, bucket } = cfg(platform);
  if (!accountId || !accessKey || !secretKey || !bucket) {
    throw new Error('R2 credentials missing (CLOUDFLARE_ACCOUNT_ID / R2 keys / TGPC_R2_DG_BUCKET)');
  }
  const host = `${accountId}.r2.cloudflarestorage.com`;
  const path = `/${bucket}/${key}`;
  const now = new Date();
  const amzDate = now.toISOString().replace(/[-:.]/g, '').slice(0, 15) + 'Z';
  const dateStamp = amzDate.slice(0, 8);
  const payloadHash = await sha256Hex(body ?? '');
  const signedHeaders = 'host;x-amz-content-sha256;x-amz-date';
  const canonical =
    `${method}\n${encodeURI(path)}\n\n` +
    `host:${host}\nx-amz-content-sha256:${payloadHash}\nx-amz-date:${amzDate}\n\n` +
    `${signedHeaders}\n${payloadHash}`;
  const scope = `${dateStamp}/auto/s3/aws4_request`;
  const stringToSign = `AWS4-HMAC-SHA256\n${amzDate}\n${scope}\n${await sha256Hex(canonical)}`;
  const sig = toHex(await hmacSha256(await signingKey(secretKey, dateStamp), stringToSign));
  const auth =
    `AWS4-HMAC-SHA256 Credential=${accessKey}/${scope}, ` +
    `SignedHeaders=${signedHeaders}, Signature=${sig}`;
  const headers: Record<string, string> = {
    Authorization: auth,
    'x-amz-date': amzDate,
    'x-amz-content-sha256': payloadHash
  };
  if (method === 'PUT' && body !== undefined) {
    headers['Content-Type'] = 'application/json';
    headers['Content-Length'] = String(enc.encode(body).length);
  }
  const resp = await fetch(`https://${host}${encodeURI(path)}`, { method, headers, body });
  if (!resp.ok) throw new Error(`R2 ${resp.status}`);
  return resp.text();
}

async function r2Json(platform: App.Platform | undefined, key: string): Promise<Record<string, unknown>> {
  const text = await r2Fetch(platform, key, 'GET');
  const parsed: unknown = JSON.parse(text || '{}');
  if (!parsed || typeof parsed !== 'object') throw new Error('Bad JSON');
  return parsed as Record<string, unknown>;
}

// --- Section builders (each fail-soft) ---

async function supabaseSection(platform: App.Platform | undefined): Promise<OpsSnapshot['supabase']> {
  const { supabaseUrl, serviceKey } = cfg(platform);
  if (!supabaseUrl || !serviceKey) return { error: 'Supabase credentials missing' };
  try {
    const sb = createClient(supabaseUrl, serviceKey);
    const [rph, dg, meta] = await Promise.all([
      sb.from('rph').select('registration_number', { count: 'exact', head: true }),
      sb.from('rph_dg_contacts').select('registration_number', { count: 'exact', head: true }),
      sb.from('metadata').select('value').eq('key', 'last_sync').maybeSingle()
    ]);
    if (rph.error) throw new Error(rph.error.message);
    if (dg.error) throw new Error(dg.error.message);
    return {
      rph: rph.count ?? undefined,
      rph_dg_contacts: dg.count ?? undefined,
      last_sync: typeof meta.data?.value === 'string' ? meta.data.value : undefined
    };
  } catch (e) {
    return { error: String(e instanceof Error ? e.message : e).slice(0, 160) };
  }
}

async function vpsSection(platform: App.Platform | undefined): Promise<OpsSnapshot['vps']> {
  try {
    const stats = await r2Json(platform, 'ops/dg_stats.json');
    const updated = typeof stats['updated_at'] === 'string' ? (stats['updated_at'] as string) : '';
    let age: number | null = null;
    try {
      if (updated) age = (Date.now() - Date.parse(updated)) / 60000;
    } catch {
      age = null;
    }
    let halt = false;
    try {
      const ctl = await r2Json(platform, 'ops/ctl.json');
      halt = (ctl as Record<string, unknown>)['halt'] === true;
    } catch {
      halt = false;
    }
    const fbr = stats['fail_by_reason'];
    return {
      done: typeof stats['done'] === 'number' ? (stats['done'] as number) : 0,
      failed: typeof stats['failed'] === 'number' ? (stats['failed'] as number) : 0,
      updated_at: updated || undefined,
      age_min: age === null ? null : Math.round(age * 10) / 10,
      alive: age !== null && age < 15 && !halt,
      halt,
      fail_by_reason:
        fbr && typeof fbr === 'object' ? (fbr as Record<string, number>) : undefined
    };
  } catch (e) {
    return { error: String(e instanceof Error ? e.message : e).slice(0, 160) };
  }
}

async function ctlSection(platform: App.Platform | undefined): Promise<OpsSnapshot['ctl']> {
  try {
    const ctl = await r2Json(platform, 'ops/ctl.json');
    return {
      halt: ctl['halt'] === true,
      note: typeof ctl['note'] === 'string' ? (ctl['note'] as string).slice(0, 140) : '',
      updated_at: typeof ctl['updated_at'] === 'string' ? (ctl['updated_at'] as string) : ''
    };
  } catch (e) {
    return { error: String(e instanceof Error ? e.message : e).slice(0, 160) };
  }
}

async function ciSection(platform: App.Platform | undefined): Promise<OpsSnapshot['ci']> {
  try {
    const { githubToken } = cfg(platform);
    const headers: Record<string, string> = { Accept: 'application/vnd.github+json' };
    if (githubToken) headers['Authorization'] = `Bearer ${githubToken}`;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    try {
      const resp = await fetch(
        'https://api.github.com/repos/tgpc-org/tgpc/actions/runs?per_page=5',
        { headers, signal: controller.signal }
      );
      if (!resp.ok) throw new Error(`GitHub ${resp.status}`);
      const data = (await resp.json()) as {
        workflow_runs?: Array<{
          name?: string;
          head_branch?: string;
          conclusion?: string | null;
          status?: string;
          created_at?: string;
        }>;
      };
      return (data.workflow_runs || []).slice(0, 5).map((r) => ({
        name: r.name || 'workflow',
        branch: r.head_branch || '',
        result: r.conclusion || r.status || '',
        created_at: (r.created_at || '').slice(0, 16)
      }));
    } finally {
      clearTimeout(timer);
    }
  } catch (e) {
    return { error: String(e instanceof Error ? e.message : e).slice(0, 160) };
  }
}

export async function buildOpsSnapshot(platform: App.Platform | undefined): Promise<OpsSnapshot> {
  
  const [supabase, vps, ctl, ci] = await Promise.all([
    supabaseSection(platform),
    vpsSection(platform),
    ctlSection(platform),
    ciSection(platform)
  ]);
  // Coverage reuses the Supabase count already fetched to avoid a second query.
  let coverage: OpsSnapshot['coverage'];
  if ('error' in vps && vps.error && vps.error.includes('R2 credentials')) {
    coverage = { error: 'R2 credentials missing' };
  } else {
    try {
      const cp = await r2Json(platform, 'ops/dg_fetch_checkpoint.json');
      const completed = Array.isArray(cp['completed']) ? (cp['completed'] as unknown[]).length : 0;
      const ft = cp['failed_terminal'];
      const terminal = ft && typeof ft === 'object' ? Object.keys(ft as object).length : 0;
      const total =
        typeof supabase.rph === 'number' ? supabase.rph : completed + terminal;
      coverage = {
        completed,
        terminal,
        uncovered: Math.max(0, total - completed - terminal),
        total,
        pct_resolved: total ? Math.min(100, Math.round((100 * (completed + terminal)) / total)) : 0
      };
    } catch (e) {
      coverage = { error: String(e instanceof Error ? e.message : e).slice(0, 160) };
    }
  }
  return {
    generated_at: new Date().toISOString(),
    supabase,
    vps,
    ctl,
    coverage,
    ci
  };
}

/** Read-modify-write ops/ctl.json halt flag. Returns the written doc. */
export async function setCtlHalt(
  platform: App.Platform | undefined,
  halt: boolean,
  note: string
): Promise<{ halt: boolean; note: string; updated_at: string }> {
  
  let existing: Record<string, unknown>;
  try {
    existing = await r2Json(platform, 'ops/ctl.json');
  } catch {
    existing = {};
  }
  const doc = {
    halt,
    ssh_exclude: typeof existing['ssh_exclude'] === 'string' ? existing['ssh_exclude'] : '',
    note: note.slice(0, 140),
    updated_at: new Date().toISOString().replace(/\.\d+Z$/, 'Z')
  };
  await r2Fetch(platform, 'ops/ctl.json', 'PUT', JSON.stringify(doc, null, 2));
  return { halt: doc.halt, note: doc.note, updated_at: doc.updated_at };
}
