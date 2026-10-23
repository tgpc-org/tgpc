// One sitemap chunk: up to SITEMAP_CHUNK_SIZE profile URLs, referenced by
// /sitemap.xml. Cached at the edge so a crawl of 77k profiles costs eight
// cold builds a day, not 77k database reads.
import { error } from '@sveltejs/kit';
import type { RequestHandler } from './$types';
import { SITEMAP_CHUNK_SIZE, isValidChunkNumber, urlsetXml } from '#lib/sitemap.js';
import { activeRegs } from '#lib/server/sitemapData.js';
import { edgeCached } from '#lib/server/edgeCache.js';

const TTL_SECONDS = 6 * 60 * 60;

export const GET: RequestHandler = async ({ params, platform, url }) => {
	const n = Number(params.n);
	if (!isValidChunkNumber(n)) {
		error(404, 'Not found');
	}

	return edgeCached(url.origin + `/sitemap/${n}.xml`, TTL_SECONDS, async () => {
		const regs = await activeRegs(platform, (n - 1) * SITEMAP_CHUNK_SIZE, SITEMAP_CHUNK_SIZE);
		if (regs === null) {
			error(503, 'Sitemap temporarily unavailable');
		}
		if (regs.length === 0) {
			error(404, 'Not found');
		}

		return new Response(urlsetXml(regs), {
			headers: { 'Content-Type': 'application/xml; charset=utf-8' }
		});
	});
};
