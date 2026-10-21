// Best-effort brute-force limiter for the public and admin endpoints
// (CODE_REVIEW.md H7, audit F3/F7).
//
// Two tiers, cheapest first:
//
//  1. Per-isolate in-memory window. Costs nothing, no I/O, and is the tier
//     that runs today. It is *not* shared: each Pages isolate keeps its own
//     counters, so the effective global budget is roughly
//     `limit x live_isolates`.
//
//  2. Optional shared counter in Workers KV (`RATE_LIMIT` binding). It is
//     consulted *only* after the local budget for a key is exhausted, so
//     ordinary traffic costs zero KV operations — important because KV bills
//     and quota-caps writes, and /api/stats is polled by every open tab.
//     Only a caller that already looks abusive pays for the global check.
//
// Why not do this at the edge: a Cloudflare WAF / Rate Limiting rule needs a
// zone. This project is served from `tgpc.pages.dev` and the account owns no
// zones (verified 2026-10-21), so there is nothing to attach a rule to. The
// Workers Rate Limiting binding is likewise not among the bindings Pages
// Functions support. Adding a custom domain would restore the edge option;
// until then this is the honest ceiling.
//
// Availability beats strictness: if KV misbehaves we fall back to the local
// verdict rather than failing the request.
const WINDOW_MS = 60_000;
const MAX_ATTEMPTS = 5;
/** Higher budget for high-frequency, unauthenticated read endpoints. */
const MAX_SEARCH_ATTEMPTS = 120;

/** Upper bound on tracked keys, so spoofed/rotating addresses cannot grow the
 *  map without limit for the lifetime of an isolate. */
const MAX_KEYS = 5_000;

/** The slice of a KV namespace binding this module needs (kept structural so
 *  tests can pass a fake and so the module compiles without worker types). */
export interface RateLimitKV {
  get(_key: string): Promise<string | null>;
  put(
    _key: string,
    _value: string,
    _options?: { expirationTtl?: number }
  ): Promise<void>;
}

/** Each key holds the timestamps of its hits inside the current window. */
const buckets = new Map<string, number[]>();

/**
 * Record a hit locally. Returns true when the caller is still inside its
 * local budget, false when it has exceeded it.
 */
function takeLocal(key: string, max: number, windowMs: number): boolean {
  const now = Date.now();
  const recent = (buckets.get(key) ?? []).filter((t) => now - t < windowMs);

  if (recent.length >= max) {
    // Keep the pruned array so the entry stops growing, but do not add a hit:
    // the window is already full and repeated hits must not extend it.
    buckets.set(key, recent);
    sweep(now, windowMs);
    return false;
  }

  recent.push(now);
  buckets.set(key, recent);
  sweep(now, windowMs);
  return true;
}

/**
 * Drop stale keys once the map grows past its cap. Map iteration follows
 * insertion order, so deleting from the front evicts the oldest keys — which
 * are also the least likely to still be inside an active window.
 */
function sweep(now: number, windowMs: number): void {
  if (buckets.size <= MAX_KEYS) return;

  for (const [key, hits] of buckets) {
    const last = hits.length ? hits[hits.length - 1] : 0;
    if (!last || now - last >= windowMs) buckets.delete(key);
  }

  while (buckets.size > MAX_KEYS) {
    const oldest = buckets.keys().next().value;
    if (oldest === undefined) break;
    buckets.delete(oldest);
  }
}

async function limited(
  key: string,
  max: number,
  windowMs: number,
  platform?: App.Platform
): Promise<boolean> {
  if (takeLocal(key, max, windowMs)) return false;

  // Local budget is spent. With no shared counter there is nothing else to
  // consult, which is exactly the pre-existing behaviour.
  const kv = platform?.env?.RATE_LIMIT;
  if (!kv) return true;

  try {
    const window = Math.floor(Date.now() / windowMs);
    const kvKey = `rl:${key}:${window}`;
    const seen = Number(await kv.get(kvKey));
    const count = Number.isFinite(seen) ? seen : 0;

    if (count >= max) return true;

    await kv.put(kvKey, String(count + 1), {
      // Outlive the window so the counter cannot be read/reset mid-flight,
      // then expire on its own (no cleanup job).
      expirationTtl: Math.ceil(windowMs / 1000) + 60
    });
    return false;
  } catch {
    // KV unavailable: keep the local verdict instead of failing the request.
    return true;
  }
}

/**
 * Login / credential-checking limiter. `platform` is optional so callers that
 * have no binding (and unit tests) keep the in-memory behaviour.
 */
export function rateLimited(
  key: string,
  platform?: App.Platform
): Promise<boolean> {
  return limited(key, MAX_ATTEMPTS, WINDOW_MS, platform);
}

/** Higher-budget limiter for high-frequency read endpoints (search-as-you-type). */
export function rateLimitedSearch(
  key: string,
  platform?: App.Platform
): Promise<boolean> {
  return limited(key, MAX_SEARCH_ATTEMPTS, WINDOW_MS, platform);
}

/** Test seam: forget every local window. */
export function resetRateLimits(): void {
  buckets.clear();
}

/** Test seam: how many keys the local map is currently tracking. */
export function trackedKeyCount(): number {
  return buckets.size;
}
