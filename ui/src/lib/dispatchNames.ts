/**
 * Dispatch PDF naming rule, shared by the listing and the download route.
 *
 * Both routes must agree: if the listing derived names differently from the
 * download route's validation, the page could advertise files that 404 on
 * click (audit F13). Real names look like `DL01052023.pdf` or
 * `DL03062020C.pdf` — "DL", day, month, year, then an optional letter for
 * same-day duplicates.
 *
 * Pure module (no `$app/env`), so `node --test` can import it directly.
 */

/** The R2 key prefix every dispatch PDF lives under. */
export const DISPATCH_PREFIX = 'dispatch/';

/** Longest plausible file name; also keeps absurd URLs out of the route. */
const MAX_NAME_LENGTH = 30;

/** `DL` + DD + MM + YYYY + optional suffix + `.pdf` */
const DISPATCH_NAME_RE = /^DL\d{2}\d{2}\d{4}[A-Z]*\.pdf$/i;

/**
 * True when `name` is a single dispatch PDF name: a bare file name (no path
 * separators, no `..`), bounded length, matching the schema.
 */
export function isDispatchName(name: string): boolean {
	return (
		name.length > 0 &&
		name.length <= MAX_NAME_LENGTH &&
		!name.includes('/') &&
		!name.includes('..') &&
		DISPATCH_NAME_RE.test(name)
	);
}

/**
 * Map an R2 object key to its dispatch file name, or null when the key is
 * outside `dispatch/` or not a dispatch PDF (nested folders, stray uploads).
 */
export function dispatchNameFromKey(key: string): string | null {
	if (!key.startsWith(DISPATCH_PREFIX)) return null;
	const name = key.slice(DISPATCH_PREFIX.length);
	return isDispatchName(name) ? name : null;
}
