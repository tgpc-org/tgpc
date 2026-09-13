import type { Handle } from '@sveltejs/kit';

// Security headers applied to every function response (CODE_REVIEW.md H6).
// Mirrors `ui/static/_headers`, which covers static assets served directly by
// Cloudflare Pages (those bypass SvelteKit + this hook).
//
// NOTE: CSP lives ONLY in svelte.config.js (kit.csp.mode 'auto') — SvelteKit
// injects per-request nonces into the scripts it renders and sends the
// matching header itself. Setting Content-Security-Policy here as well
// creates a second enforced policy whose nonce never matches the rendered
// scripts, which bricks hydration (all inline scripts blocked).

export const handle: Handle = async ({ event, resolve }) => {
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
