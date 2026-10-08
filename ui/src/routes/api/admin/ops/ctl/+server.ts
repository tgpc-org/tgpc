import { getAdminSecret, isAuthed } from '#lib/server/auth.js';
import { setCtlHalt } from '#lib/server/ops.js';
import { rateLimited } from '#lib/server/rateLimit.js';
import { parseCtlBody } from '#lib/ops.js';
import type { RequestHandler } from './$types';

export const POST: RequestHandler = async (event) => {
  const { platform, cookies, request } = event;

  if (rateLimited('ops-ctl:' + event.getClientAddress())) {
    return new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '60' }
    });
  }

  const adminSecret = getAdminSecret(platform);
  if (!adminSecret) {
    return new Response('Not configured', { status: 500 });
  }
  if (!(await isAuthed(cookies, platform))) {
    return new Response('Unauthorized', { status: 403 });
  }

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return new Response('Bad request', { status: 400 });
  }
  let parsed: { halt: boolean; note: string };
  try {
    parsed = parseCtlBody(body);
  } catch {
    return new Response('halt must be boolean', { status: 400 });
  }

  try {
    const doc = await setCtlHalt(platform, parsed.halt, parsed.note);
    // Audit: action + note only. No PII flows through this channel.
    console.log(`admin ops ctl halt=${parsed.halt} note=${parsed.note.slice(0, 80)}`);
    return Response.json({ ok: true, ctl: doc }, { headers: { 'Cache-Control': 'no-store' } });
  } catch (e) {
    return new Response(String(e instanceof Error ? e.message : e).slice(0, 160), {
      status: 502
    });
  }
};
