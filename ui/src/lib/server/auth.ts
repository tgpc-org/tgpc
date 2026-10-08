/**
 * Server-only admin auth helpers.
 *
 * This module lives under `#lib/server/` — SvelteKit fails the build if it is
 * ever imported from client-reachable code, so the secret handling here cannot
 * leak into the browser bundle.
 *
 * Session model: a signed, expiring token in an HttpOnly cookie. The signing
 * key is the admin secret itself, so rotating the secret invalidates every
 * outstanding session for free.
 */

const enc = new TextEncoder();

export const SESSION_COOKIE = 'tgpc_admin';
export const SESSION_TTL_SECONDS = 60 * 60 * 8; // 8 hours

/** Read the configured admin secret, or null if unconfigured. */
export function getAdminSecret(platform: App.Platform | undefined): string | null {
  const env = platform?.env;
  return env?.['ADMIN_SECRET'] || env?.['QUOTA_SECRET'] || null;
}

/**
 * Constant-time string comparison.
 *
 * Both inputs are hashed first, so the comparison loop is always 32 bytes
 * regardless of input length — this leaks neither content nor length.
 */
export async function safeEqual(a: string, b: string): Promise<boolean> {
  const [da, db] = await Promise.all([
    crypto.subtle.digest('SHA-256', enc.encode(a)),
    crypto.subtle.digest('SHA-256', enc.encode(b))
  ]);
  const va = new Uint8Array(da);
  const vb = new Uint8Array(db);
  let diff = 0;
  for (let i = 0; i < va.length; i++) diff |= va[i] ^ vb[i];
  return diff === 0;
}

function b64url(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

async function sign(secret: string, msg: string): Promise<string> {
  const key = await crypto.subtle.importKey(
    'raw',
    enc.encode(secret),
    { name: 'HMAC', hash: 'SHA-256' },
    false,
    ['sign']
  );
  return b64url(await crypto.subtle.sign('HMAC', key, enc.encode(msg)));
}

/** Mint a session token valid for SESSION_TTL_SECONDS. */
export async function createSession(secret: string): Promise<string> {
  const exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  return `${exp}.${await sign(secret, `v1.${exp}`)}`;
}

/** Verify a session token's signature and expiry. */
export async function verifySession(
  token: string | undefined,
  secret: string
): Promise<boolean> {
  if (!token) return false;
  const dot = token.lastIndexOf('.');
  if (dot <= 0) return false;

  const exp = Number(token.slice(0, dot));
  if (!Number.isFinite(exp) || exp * 1000 <= Date.now()) return false;

  return safeEqual(token.slice(dot + 1), await sign(secret, `v1.${exp}`));
}

/**
 * Whether the current request carries a valid admin session.
 *
 * Fails closed: an unconfigured secret means nobody is authorized, rather than
 * everybody.
 */
export async function isAuthed(
  cookies: { get: (_name: string) => string | undefined },
  platform: App.Platform | undefined
): Promise<boolean> {
  const secret = getAdminSecret(platform) ?? (await appEnvAdminSecret());
  if (!secret) return false;
  return verifySession(cookies.get(SESSION_COOKIE), secret);
}

/**
 * $app/env fallback for the admin secret.
 *
 * This module must stay importable from `node:test` unit tests (no Vite
 * resolver for `$app/*` there), so the import is dynamic with a fail-closed
 * catch: under plain node it resolves to null and platform.env remains the
 * only source. See `$lib/server/appEnv.ts` for the static-import variant
 * used by untested handler modules.
 */
async function appEnvAdminSecret(): Promise<string | null> {
  try {
    const priv = (await import('$app/env/private')) as unknown as Record<string, string | undefined>;
    return priv['ADMIN_SECRET'] || priv['QUOTA_SECRET'] || null;
  } catch {
    return null;
  }
}
