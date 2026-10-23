/**
 * Sitemap XML for the public registry.
 *
 * Profiles are the whole point of this site and they are only reachable
 * through the client-rendered search box, so a sitemap is the one way a
 * crawler can find them. `/sitemap.xml` is an index; `/sitemap/<n>.xml` holds
 * at most `SITEMAP_CHUNK_SIZE` URLs, under the 50,000-URL limit sitemaps.org
 * imposes per file.
 *
 * Pure module (no `$app/env`), so `node --test` can import it directly.
 */

export const SITE_ORIGIN = 'https://tgpc.pages.dev';

/** sitemaps.org limit is 50,000 URLs / 50 MB uncompressed; stay well under. */
export const SITEMAP_CHUNK_SIZE = 10_000;

/** 50 chunks = 500k profiles, ~5x the current registry. */
export const SITEMAP_MAX_CHUNKS = 50;

/** Path of chunk `n` (1-based), as referenced by the index and the route. */
export function chunkPath(n: number): string {
	return `/sitemap/${n}.xml`;
}

/** Number of chunks needed for `total` profiles (0 when there is nothing). */
export function chunkCount(total: number): number {
	if (!Number.isFinite(total) || total <= 0) return 0;
	return Math.min(SITEMAP_MAX_CHUNKS, Math.ceil(total / SITEMAP_CHUNK_SIZE));
}

/** True for an in-range chunk number: a positive integer within the cap. */
export function isValidChunkNumber(n: unknown): n is number {
	return typeof n === 'number' && Number.isInteger(n) && n >= 1 && n <= SITEMAP_MAX_CHUNKS;
}

/** Escape the five XML entities. Registration numbers are `[A-Z0-9]+`, but a
 *  sitemap is machine-read and one bad character invalidates the whole file. */
export function escapeXml(value: string): string {
	return value
		.replace(/&/g, '&amp;')
		.replace(/</g, '&lt;')
		.replace(/>/g, '&gt;')
		.replace(/"/g, '&quot;')
		.replace(/'/g, '&apos;');
}

const XML_HEAD = '<?xml version="1.0" encoding="UTF-8"?>';

/** `<sitemapindex>` listing every chunk, or an empty index when there are none. */
export function sitemapIndexXml(chunks: number): string {
	const entries = Array.from({ length: chunks }, (_, i) => {
		const loc = escapeXml(SITE_ORIGIN + chunkPath(i + 1));
		return `  <sitemap><loc>${loc}</loc></sitemap>`;
	});
	return (
		`${XML_HEAD}\n` +
		`<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
		entries.join('\n') +
		`\n</sitemapindex>\n`
	);
}

/** One chunk: a `<urlset>` of profile URLs. */
export function urlsetXml(registrationNumbers: string[]): string {
	const entries = registrationNumbers.map((reg) => {
		const loc = escapeXml(`${SITE_ORIGIN}/rph/${reg}`);
		return `  <url><loc>${loc}</loc><changefreq>monthly</changefreq></url>`;
	});
	return (
		`${XML_HEAD}\n` +
		`<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
		entries.join('\n') +
		`\n</urlset>\n`
	);
}
