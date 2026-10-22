import { lookupRecord } from '#lib/server/records.js';
import { rateLimitedSearch } from '#lib/server/rateLimitShared.js';
import type { RequestHandler } from './$types';

/**
 * Public single-record fetch. Reads with the service key server-side and
 * returns the same eleven safe columns the pinned search RPC emits.
 * Rate-limited per IP; no login required.
 *
 * The profile page reads the same columns through `#lib/server/records`
 * directly — it must never call this route over HTTP from SSR (relative URLs
 * throw in the Workers runtime and the per-IP limit would surface as 404s).
 */
export const GET: RequestHandler = async (event) => {
	const { platform, url } = event;

	if (await rateLimitedSearch('record:' + event.getClientAddress(), platform)) {
		return new Response('Too Many Requests', {
			status: 429,
			headers: { 'Retry-After': '60' }
		});
	}

	const result = await lookupRecord(platform, url.searchParams.get('reg'));

	switch (result.kind) {
		case 'invalid':
			return new Response('Bad registration number', { status: 400 });
		case 'unconfigured':
			return new Response('Not configured', { status: 500 });
		case 'upstream':
			return new Response('Upstream error', { status: 502 });
		case 'not-found':
			return new Response('No such record', { status: 404 });
		case 'ok':
			return new Response(JSON.stringify(result.record), {
				headers: { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }
			});
	}
};
