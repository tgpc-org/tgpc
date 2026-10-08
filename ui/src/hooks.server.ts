import type { Handle } from '@sveltejs/kit/hooks';
import { dev } from '$app/env';
import { isAuthed } from '#lib/server/auth.js';
import { gatePath } from '#lib/server/gate.js';

// Site-wide gate: every data-bearing route requires the admin session.
// Only the login flow (/admin page + /api/admin) and the inert assets the
// login page needs to render (JS/CSS chunks, icons, manifest) are reachable
// without one. API routes answer 403; pages redirect to /admin. Path
// classification lives in $lib/server/gate (unit-tested).
//
// NOTE: files under ui/static/ are served directly by Cloudflare Pages and
// bypass SvelteKit + this hook. That is acceptable because static/ holds no
// registry or PII data (notice titles link public council circulars; the
// search data always flows through gated Functions). Local `vite dev` stays
// open: there is no platform secret there, so the gate could never pass.

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

	return response;
};
