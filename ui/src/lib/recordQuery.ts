/**
 * PostgREST query shape for a single pharmacist record, shared by the public
 * `/api/record` route and the profile page's server load.
 *
 * The two callers must return identical shapes: the profile page renders the
 * same eleven columns the API advertises, so the list and the URL builder live
 * here instead of being duplicated per route.
 *
 * Pure module (no `$app/env`), so `node --test` can import it directly.
 */

/** The eleven safe columns the public API and profile page may expose. */
export const RECORD_COLUMNS = [
	'registration_number',
	'name',
	'father_name',
	'category',
	'gender',
	'validity_date',
	'status',
	'photo_url',
	'serial_number',
	'education',
	'work_experience'
].join(',');

/**
 * Absolute PostgREST URL for one registration number.
 *
 * `reg` is expected to be normalised (uppercase alphanumerics, see
 * `normalizeReg`); it is still URL-encoded so a caller that skips validation
 * cannot break out of the query string.
 */
export function recordQueryUrl(supabaseUrl: string, reg: string): string {
	const base = supabaseUrl.replace(/\/+$/, '');
	return `${base}/rest/v1/rph?registration_number=eq.${encodeURIComponent(reg)}&select=${RECORD_COLUMNS}`;
}

/**
 * The first row of a PostgREST response, or null when the result is empty or
 * not an array (an error body, `null`, a truncated response).
 */
export function firstRecord(rows: unknown): Record<string, unknown> | null {
	if (!Array.isArray(rows) || rows.length === 0) return null;
	const row: unknown = rows[0];
	return typeof row === 'object' && row !== null ? (row as Record<string, unknown>) : null;
}
