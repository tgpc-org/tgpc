/**
 * Site-wide gate path classification (used by hooks.server.ts).
 *
 * Pure so it is unit-testable: only the login flow (/admin page +
 * /api/admin) and the inert assets the login page needs to render are
 * reachable without a session. Everything under /api/ otherwise answers
 * 403; pages redirect to /admin.
 */

const OPEN_EXACT = new Set(['/admin', '/api/admin']);

const OPEN_ASSETS = new Set([
  '/favicon.ico',
  '/favicon.svg',
  '/favicon-192.png',
  '/manifest.json',
  '/robots.txt'
]);

export function isOpenAsset(pathname: string): boolean {
  return pathname.startsWith('/_app/') || OPEN_ASSETS.has(pathname);
}

export type GateDecision =
  | { kind: 'open' }
  | { kind: 'api-deny' }
  | { kind: 'page-login' };

export function gatePath(pathname: string): GateDecision {
  if (OPEN_EXACT.has(pathname) || isOpenAsset(pathname)) return { kind: 'open' };
  if (pathname.startsWith('/api/')) return { kind: 'api-deny' };
  return { kind: 'page-login' };
}
