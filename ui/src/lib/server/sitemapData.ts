/**
 * Registry reads for the sitemap routes (service key, PostgREST).
 *
 * Only `registration_number` is read, and only for active registrations:
 * inactive profiles still resolve, but advertising them as canonical index
 * entries would be wrong for a registry whose point is verifying *current*
 * registration.
 *
 * Returns null on any failure so the route can answer 503 (retryable) instead
 * of serving a truncated sitemap, which crawlers would treat as authoritative.
 */
import { envVal } from './appEnv.js';

/** PostgREST page size; the API returns this many rows per request. */
const PAGE_LIMIT = 5_000;

function authHeaders(secret: string): Record<string, string> {
	return { apikey: secret, Authorization: `Bearer ${secret}` };
}

function endpoint(platform: App.Platform | undefined): { url: string; key: string } | null {
	const url = envVal(platform, 'SUPABASE_URL');
	const key = envVal(platform, 'SUPABASE_SECRET_KEY');
	return url && key ? { url: url.replace(/\/+$/, ''), key } : null;
}

/** Total active registrations, or null when unconfigured/unreachable. */
export async function activeRecordTotal(platform: App.Platform | undefined): Promise<number | null> {
	const target = endpoint(platform);
	if (!target) return null;

	try {
		const response = await fetch(
			`${target.url}/rest/v1/rph?select=registration_number&status=eq.Active&limit=1`,
			{ headers: { ...authHeaders(target.key), Prefer: 'count=exact' } }
		);
		if (!response.ok) return null;
		// PostgREST reports the exact count in `Content-Range: 0-0/<total>`.
		const total = Number(response.headers.get('content-range')?.split('/')[1]);
		return Number.isFinite(total) ? total : null;
	} catch {
		return null;
	}
}

/**
 * Active registration numbers in ascending order, for one chunk.
 *
 * Ordered explicitly: offset pagination over an unordered result would skip
 * or repeat rows between chunks, and a sitemap must not do either.
 */
export async function activeRegs(
	platform: App.Platform | undefined,
	offset: number,
	limit: number
): Promise<string[] | null> {
	const target = endpoint(platform);
	if (!target) return null;

	const regs: string[] = [];
	try {
		for (let start = offset; start < offset + limit; start += PAGE_LIMIT) {
			const size = Math.min(PAGE_LIMIT, offset + limit - start);
			const response = await fetch(
				`${target.url}/rest/v1/rph?select=registration_number&status=eq.Active&order=registration_number.asc&limit=${size}&offset=${start}`,
				{ headers: authHeaders(target.key) }
			);
			if (!response.ok) return null;
			const rows: unknown = await response.json();
			if (!Array.isArray(rows)) return null;
			for (const row of rows) {
				const reg = (row as { registration_number?: unknown }).registration_number;
				if (typeof reg === 'string' && reg) regs.push(reg);
			}
			if (rows.length < size) break;
		}
	} catch {
		return null;
	}

	return regs;
}
