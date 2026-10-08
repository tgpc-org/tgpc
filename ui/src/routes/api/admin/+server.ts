import { dev } from '$app/env';
import {
  SESSION_COOKIE,
  SESSION_TTL_SECONDS,
  createSession,
  getAdminSecret,
  safeEqual
} from '#lib/server/auth.js';
import { rateLimited } from '#lib/server/rateLimit.js';
import type { RequestHandler } from './$types';

/** Log in: verify the shared secret, then issue an HttpOnly session cookie. */
export const POST: RequestHandler = async (event) => {
  const { request, platform, cookies } = event;

  if (rateLimited(event.getClientAddress())) {
    return new Response('Too Many Requests', {
      status: 429,
      headers: { 'Retry-After': '60' }
    });
  }

  const adminSecret = getAdminSecret(platform);
  if (!adminSecret) {
    return new Response('Not configured', { status: 500 });
  }

  const body = await request.json().catch(() => null);
  const rawSecret = (body as { secret?: unknown } | null)?.secret;

  // Always create a session token and always await the same amount of time
  // (crypto constant-time compare + a fixed artificial delay), so responses
  // are indistinguishable for correct and incorrect secrets (timing side
  // channel). Also throttles brute force to ~120 attempts/minute per isolate.
  const [isValid, token] = await Promise.all([
    typeof rawSecret === 'string' ? safeEqual(rawSecret, adminSecret) : Promise.resolve(false),
    createSession(adminSecret),
    new Promise((r) => setTimeout(r, 250))
  ]);

  if (!isValid) {
    return new Response('Unauthorized', { status: 403 });
  }

  cookies.set(SESSION_COOKIE, token, {
    path: '/',
    httpOnly: true,
    secure: !dev,
    sameSite: 'strict',
    maxAge: SESSION_TTL_SECONDS
  });

  return new Response(JSON.stringify({ ok: true }), {
    headers: { 'Content-Type': 'application/json' }
  });
};

/** Log out: clear the session cookie. */
export const DELETE: RequestHandler = async ({ cookies }) => {
  cookies.delete(SESSION_COOKIE, { path: '/' });
  return new Response(JSON.stringify({ ok: true }), {
    headers: { 'Content-Type': 'application/json' }
  });
};
