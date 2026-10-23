/**
 * Site-wide gate path classification (used by hooks.server.ts).
 *
 * TGPC RPh Index is a PUBLIC search portal: search, profiles, notices,
 * dispatch lists, stats and health are reachable without a session (the
 * data APIs are rate-limited per IP inside their handlers). Only the admin
 * surface needs a session: the login endpoint's siblings (/api/admin/*
 * below the login route, /api/usage) and the dev /sandbox page.
 * Everything else under /api/ answers 403; gated pages redirect to /admin.
 *
 * Pure so it is unit-testable.
 */

const OPEN_EXACT = new Set([
	'/',
	'/admin',
	'/notice',
	'/dispatch',
	'/sitemap.xml',
	'/api/admin',
	'/api/stats',
	'/api/search',
	'/api/record',
	'/api/refine',
	'/api/notice',
	'/api/health'
]);


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
	// Sitemap chunks (/sitemap/<n>.xml): public XML, no registry data beyond
	// registration numbers; the route 404s anything out of range.
	if (pathname.startsWith('/sitemap/')) return { kind: 'open' };
	if (pathname.startsWith('/api/')) {
		// /api/dispatch has a [name] file sub-route, so the whole subtree
		// is public; every other /api/ path is denied unless allowlisted.
		if (pathname === '/api/dispatch' || pathname.startsWith('/api/dispatch/')) {
			return { kind: 'open' };
		}
		return { kind: 'api-deny' };
	}
	// Public profile pages live under /rph/<registration_number>.
	if (pathname.startsWith('/rph/')) return { kind: 'open' };
	return { kind: 'page-login' };
}
