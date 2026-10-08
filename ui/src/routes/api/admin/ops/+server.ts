import { getAdminSecret, isAuthed } from '#lib/server/auth.js';
import { buildOpsSnapshot } from '#lib/server/ops.js';
import { rateLimited } from '#lib/server/rateLimit.js';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async (event) => {
  const { platform, cookies } = event;

  if (rateLimited('ops:' + event.getClientAddress())) {
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

  const snapshot = await buildOpsSnapshot(platform);
  return Response.json(snapshot, { headers: { 'Cache-Control': 'no-store' } });
};
