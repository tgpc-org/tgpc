/**
 * Server-side single-record reader (service key, PostgREST).
 *
 * Why this exists instead of calling `/api/record`:
 *
 *  - SSR must not round-trip through its own HTTP API. The profile page used
 *    to do `fetch('/api/record?...')` from `+page.server.ts`, but the global
 *    `fetch` in the Workers runtime cannot resolve a relative URL — it throws
 *    `TypeError: Invalid URL: /api/record?reg=...`. The old helper swallowed
 *    that and returned null, so every profile page rendered a 404 while the
 *    API answered 200 for the same record. Only `event.fetch` resolves
 *    relative URLs, and using it would still cost a second request.
 *  - `/api/record` rate-limits per client IP. Routing SSR through it means a
 *    burst of profile views from one NAT (campus, office) returns 429s that
 *    the old helper reported as "record not found".
 *
 * The route keeps its own rate limit for public callers; it shares the query
 * shape (see `$lib/recordQuery`) and maps outcomes onto status codes.
 */
import { envVal } from './appEnv.js';
import { normalizeReg } from './contacts.js';
import { firstRecord, recordQueryUrl } from '#lib/recordQuery.js';
import type { PharmacistRecord } from '#lib/types.js';

export type RecordLookup =
	| { kind: 'ok'; record: PharmacistRecord }
	/** Missing or malformed registration number. */
	| { kind: 'invalid' }
	| { kind: 'not-found' }
	/** Supabase credentials are not configured for this deployment. */
	| { kind: 'unconfigured' }
	/** Supabase answered 4xx/5xx, timed out, or returned a non-row body. */
	| { kind: 'upstream' };

/** Read one record by registration number with the service key. */
export async function lookupRecord(
	platform: App.Platform | undefined,
	rawReg: unknown
): Promise<RecordLookup> {
	const reg = normalizeReg(rawReg);
	if (!reg) return { kind: 'invalid' };

	const supabaseUrl = envVal(platform, 'SUPABASE_URL');
	const serviceKey = envVal(platform, 'SUPABASE_SECRET_KEY');
	if (!supabaseUrl || !serviceKey) return { kind: 'unconfigured' };

	try {
		const response = await fetch(recordQueryUrl(supabaseUrl, reg), {
			headers: { apikey: serviceKey, Authorization: `Bearer ${serviceKey}` }
		});
		if (!response.ok) return { kind: 'upstream' };
		const row = firstRecord(await response.json());
		if (!row) return { kind: 'not-found' };
		return { kind: 'ok', record: row as unknown as PharmacistRecord };
	} catch {
		return { kind: 'upstream' };
	}
}
