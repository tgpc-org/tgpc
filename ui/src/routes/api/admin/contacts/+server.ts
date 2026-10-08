import { isAuthed } from '#lib/server/auth.js';
import { envVal } from '#lib/server/appEnv.js';
import { normalizeReg, shapeBase, shapeContact } from '#lib/server/contacts.js';
import { rateLimited } from '#lib/server/rateLimit.js';
import type { RequestHandler } from './$types';

const CONTACT_COLS = [
  'dob',
  'date_of_registration',
  'renewal_validity',
  'home_address',
  'home_state',
  'work_study_address',
  'work_study_state',
  'mobile_no',
  'email_id'
].join(', ');

const BASE_COLS = [
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
].join(', ');

/**
 * Admin-only contact lookup: base record + DG contact details for one reg number.
 *
 * Fail-closed at every layer: unconfigured secret denies everyone, no session
 * denies everyone, unvalidated reg never reaches SQL (strict allowlist charset,
 * so interpolation cannot break out), and PII is logged nowhere — the audit
 * trail records the registration number only.
 */
export const GET: RequestHandler = async (event) => {
  const { platform, cookies, url } = event;

  if (rateLimited('contacts:' + event.getClientAddress())) {
    return new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '60' }
    });
  }

  const adminSecret = envVal(platform, 'ADMIN_SECRET', 'QUOTA_SECRET');
  if (!adminSecret) {
    return new Response('Not configured', { status: 500 });
  }
  if (!(await isAuthed(cookies, platform))) {
    return new Response('Unauthorized', { status: 403 });
  }

  const reg = normalizeReg(url.searchParams.get('reg'));
  if (!reg) {
    return new Response('Bad registration number', { status: 400 });
  }

  const pat = envVal(platform, 'SUPABASE_PAT');
  const surl = envVal(platform, 'SUPABASE_URL');
  if (!pat || !surl) {
    return new Response('Not configured', { status: 500 });
  }
  const ref = surl.match(/https:\/\/([^.]+)\.supabase\.co/)?.[1];
  if (!ref) {
    return new Response('Bad SUPABASE_URL', { status: 500 });
  }

  const query = `SELECT r.registration_number, ${BASE_COLS.split(', ').map((c) => `r.${c}`).join(', ')}, ${CONTACT_COLS.split(', ').map((c) => `d.${c}`).join(', ')} FROM public.rph r LEFT JOIN public.rph_dg_contacts d USING (registration_number) WHERE r.registration_number = '${reg}'`;
  let rows: Record<string, unknown>[];
  try {
    const r = await fetch(`https://api.supabase.com/v1/projects/${ref}/database/query`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${pat}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ query })
    });
    if (!r.ok) {
      return new Response('Upstream error', { status: 502 });
    }
    rows = (await r.json()) as Record<string, unknown>[];
  } catch {
    return new Response('Upstream error', { status: 502 });
  }

  if (!rows.length) {
    return new Response('No such record', { status: 404 });
  }

  // Audit: registration number only. PII never enters logs.
  console.log(`admin contacts lookup ${reg}`);

  const row = rows[0];
  return Response.json(
    { base: shapeBase(row), contact: shapeContact(row) },
    { headers: { 'Cache-Control': 'no-store' } }
  );
};
