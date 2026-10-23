import type { Handle } from '@sveltejs/kit/hooks';
import { dev } from '$app/env';
import { isAuthed } from '#lib/server/auth.js';
import { gatePath } from '#lib/server/gate.js';
import { edgeCache } from '#lib/server/edgeCache.js';

// Public-portal gate: search, profiles, notices, dispatch lists, stats and
// health are reachable without a session (data APIs rate-limit per IP in
// their handlers). Only the admin surface (/api/admin/* below the login
// route, /api/usage, /sandbox) needs one. API routes answer 403; gated
// pages redirect to /admin. Path classification lives in
// $lib/server/gate (unit-tested).
//
// NOTE: files under ui/static/ are served directly by Cloudflare Pages and
// bypass SvelteKit + this hook. That is acceptable because static/ holds no
// registry or PII data (notice titles link public council circulars; the
// search data always flows through rate-limited Functions). Local `vite dev`
// stays open: there is no platform secret there, so the gate could never pass.

// Security headers applied to every function response (CODE_REVIEW.md H6).
// Mirrors `ui/_headers` (project root, as adapter-cloudflare requires), which
// covers static assets served directly by Cloudflare Pages (those bypass
// SvelteKit + this hook).
//
// NOTE: CSP lives ONLY in svelte.config.js (kit.csp.mode 'auto') — SvelteKit
// injects per-request nonces into the scripts it renders and sends the
// matching header itself. Setting Content-Security-Policy here as well
// creates a second enforced policy whose nonce never matches the rendered
// scripts, which bricks hydration (all inline scripts blocked).

/** Public profile page: /rph/<registration_number> */
const PROFILE_PATH = /^\/rph\/[^/]+\/?$/;
/** Cache API TTL for a rendered profile page (seconds). */
const PROFILE_TTL_SECONDS = 3600;

export const handle: Handle = async ({ event, resolve }) => {
	if (!dev) {
		const decision = gatePath(event.url.pathname);
		if (decision.kind !== 'open' && !await isAuthed(event.cookies, event.platform)) {
			if (decision.kind === 'api-deny') {
				return new Response('Unauthorized', { status: 403 });
			}
			return Response.redirect(new URL('/admin', event.url), 302);
		}
	}

	// Profile pages are public, identical for every visitor (the layout renders
	// no user state) and change only when the pipeline syncs. Cache the rendered
	// page in the Workers Cache API (available on *.pages.dev, per data centre)
	// so repeat views skip both the Supabase read and the render.
	//
	// Only 200 GETs are stored, and only after the security headers below are
	// applied, so a HIT serves exactly what a MISS serves — including the
	// per-render CSP nonce, which is cached together with the body it signs.
	const cacheKey =
		!dev && event.request.method === 'GET' && PROFILE_PATH.test(event.url.pathname)
			? event.url.origin + event.url.pathname
			: null;
	const cache = cacheKey ? edgeCache() : null;

	if (cacheKey && cache) {
		const hit = await cache.match(cacheKey);
		if (hit) {
			const response = new Response(hit.body, hit);
			response.headers.set('X-Cache', 'HIT');
			return response;
		}
	}

	const response = await resolve(event);

	// Static headers that apply to all responses
	const staticHeaders: Record<string, string> = {
		'X-Content-Type-Options': 'nosniff',
		'X-Frame-Options': 'DENY',
		'Referrer-Policy': 'strict-origin-when-cross-origin',
		'Strict-Transport-Security': 'max-age=31536000; includeSubDomains',
		'Permissions-Policy': 'camera=(), microphone=(), geolocation=(), interest-cohort=()',
		// Isolate this origin from cross-origin windows (BFCache + Spectre hardening)
		'Cross-Origin-Opener-Policy': 'same-origin',
		// Prevent other origins from embedding our resources
		'Cross-Origin-Resource-Policy': 'same-origin'
	};

	for (const [key, value] of Object.entries(staticHeaders)) {
		response.headers.set(key, value);
	}

	if (cacheKey && cache && response.status === 200 && !response.headers.has('set-cookie')) {
		response.headers.set('Cache-Control', `public, max-age=0, s-maxage=${PROFILE_TTL_SECONDS}`);
		response.headers.set('X-Cache', 'MISS');
		try {
			// Await rather than waitUntil: adapter-cloudflare v8 does not pass
			// the execution context through `platform` in production.
			await cache.put(cacheKey, response.clone());
		} catch {
			// Caching is best-effort; a failed put must never fail the request.
		}
	}

	return response;
};
