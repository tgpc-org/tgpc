// Sitemap index. Profiles are the reason this site exists and they are only
// reachable through the client-rendered search box, so the sitemap is the one
// way a crawler can discover them; each chunk lives at /sitemap/<n>.xml.
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { chunkCount, sitemapIndexXml } from '#lib/sitemap.js';
import { activeRecordTotal } from '#lib/server/sitemapData.js';
import { edgeCached } from '#lib/server/edgeCache.js';

/** Rebuilt twice a day: the registry syncs twice a day at most. */
const TTL_SECONDS = 6 * 60 * 60;

export const GET: RequestHandler = async ({ platform, url }) => {
	return edgeCached(url.origin + '/sitemap.xml', TTL_SECONDS, async () => {
		const total = await activeRecordTotal(platform);
		if (total === null) {
			error(503, 'Sitemap temporarily unavailable');
		}

		return new Response(sitemapIndexXml(chunkCount(total)), {
			headers: { 'Content-Type': 'application/xml; charset=utf-8' }
		});
	});
};
