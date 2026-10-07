// Best-effort, in-isolate brute-force limiter for login endpoints
// (CODE_REVIEW.md H7). Each Pages isolate holds its own window, so this is
// defence-in-depth rather than a substitute for a Cloudflare Rate Limiting
// rule on /api/admin — set that in the dashboard too.
const WINDOW_MS = 60_000;
const MAX_ATTEMPTS = 5;

const attempts = new Map<string, number[]>();

function limited(key: string, max: number, windowMs: number): boolean {
	const now = Date.now();
	const recent = (attempts.get(key) ?? []).filter((t) => now - t < windowMs);
	if (recent.length >= max) {
		attempts.set(key, recent);
		return true;
	}
	recent.push(now);
	attempts.set(key, recent);
	return false;
}

export function rateLimited(key: string): boolean {
	return limited(key, MAX_ATTEMPTS, WINDOW_MS);
}

/** Higher-budget limiter for high-frequency authed endpoints (search-as-you-type). */
export function rateLimitedSearch(key: string): boolean {
	return limited(key, 120, WINDOW_MS);
}
