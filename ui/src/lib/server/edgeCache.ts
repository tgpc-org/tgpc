/**
 * Workers Cache API access (`caches.default`), shared by `hooks.server.ts`
 * and the sitemap routes.
 *
 * Available on `*.pages.dev` (per data centre, no tiered replication) and in
 * `wrangler pages dev`; absent under plain Node, where every helper degrades
 * to a pass-through. Responses with `Set-Cookie` are never stored (the runtime
 * refuses them), which is the behaviour we want for anything session-shaped.
 */

/** `caches.default`, or null when the runtime has no Cache API. */
export function edgeCache(): Cache | null {
	const store = (globalThis as unknown as { caches?: { default?: Cache } }).caches;
	return store?.default ?? null;
}

/**
 * Serve `key` from the edge cache, building and storing it on a miss.
 *
 * `maxAgeSeconds` is the Cache API TTL (`s-maxage`), not the browser's: the
 * response also tells browsers to revalidate (`max-age=0`) so a deploy is
 * picked up immediately. `X-Cache: HIT|MISS` reports which path served the
 * request, which is also how deploys are verified.
 */
export async function edgeCached(
	key: string,
	maxAgeSeconds: number,
	build: () => Promise<Response>
): Promise<Response> {
	const cache = edgeCache();

	if (cache) {
		const hit = await cache.match(key);
		if (hit) {
			const response = new Response(hit.body, hit);
			response.headers.set('X-Cache', 'HIT');
			return response;
		}
	}

	const response = await build();

	if (cache && response.status === 200) {
		response.headers.set('Cache-Control', `public, max-age=0, s-maxage=${maxAgeSeconds}`);
		response.headers.set('X-Cache', 'MISS');
		try {
			// Await rather than waitUntil: adapter-cloudflare v8 does not pass
			// the execution context through `platform` in production.
			await cache.put(key, response.clone());
		} catch {
			// Caching is best-effort; a failed put must never fail the request.
		}
	}

	return response;
}
