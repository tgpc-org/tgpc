// Best-effort brute-force limiter for the public and admin endpoints
// (CODE_REVIEW.md H7, audit F3/F7).
//
// Two tiers, cheapest first:
//
//  1. Per-isolate in-memory window. Costs nothing and has no dependencies.
//     It is NOT shared, and in practice this deployment spreads requests over
//     many short-lived isolates — measured 2026-10-21: twelve sequential
//     login attempts produced zero blocks, so the local tier alone offers
//     close to no brute-force protection.
//
//  2. A shared fixed-window counter in Postgres (`bump_rate_limit` RPC).
//     `requireShared` callers — the credential paths — consult it on every
//     attempt, because volume there is inherently tiny. High-frequency read
//     callers consult it only after an isolate's local budget is spent, so
//     ordinary search traffic performs no SQL at all.
//
// Why not the edge: a Cloudflare Rate Limiting rule needs a zone. This project
// is served from `tgpc.pages.dev`, the account owns no zones, and Pages
// Functions do not support the Workers Rate Limiting binding. Why not KV:
// Workers KV has no atomic read-modify-write, so a counter built on it would
// race - two requests can read the same value and both write the same +1. (A
// KV binding can be read now that bindings resolve through `$app/env`, see
// audit F13; it is the missing atomicity, not reachability, that rules it
// out.) The Supabase service key is the shared store that does both.
//
// Availability beats strictness: if the shared counter errors we fall back to
// the local verdict rather than failing the request.
//
// This module must stay free of `$app/env` imports so `node:test` can load it
// directly; the env-aware wrapper lives in rateLimitShared.ts.

const WINDOW_MS = 60_000;
const MAX_ATTEMPTS = 5;
/** Higher budget for high-frequency, unauthenticated read endpoints. */
const MAX_SEARCH_ATTEMPTS = 120;

/** Upper bound on tracked keys, so rotating addresses cannot grow the map for
 *  the lifetime of an isolate. */
const MAX_KEYS = 5_000;

/** PostgREST endpoint + service key for the shared counter. */
export interface SharedCounter {
  url: string;
  secret: string;
}

export interface RateLimitOptions {
  /** Shared counter, or null/undefined when unconfigured (local tier only). */
  shared?: SharedCounter | null;
  /** Consult the shared counter on every call, not just after the local
   *  budget is spent. Correct for low-volume credential checks. */
  requireShared?: boolean;
  /** Injectable fetch, for tests. */
  call?: typeof fetch;
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

/** Buckets are hashed so the counter never becomes an address log. */
async function bucketFor(key: string): Promise<string> {
  const bytes = new TextEncoder().encode(key);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Ask the shared counter. Returns true when the caller must be blocked, or
 * null when there is no usable verdict (unconfigured or upstream error).
 */
async function sharedBlocked(
  key: string,
  max: number,
  windowSeconds: number,
  options: RateLimitOptions
): Promise<boolean | null> {
  const shared = options.shared;
  if (!shared?.url || !shared.secret) return null;

  const call = options.call ?? fetch;
  try {
    const bucket = await bucketFor(key);
    const res = await call(
      `${shared.url.replace(/\/+$/, '')}/rest/v1/rpc/bump_rate_limit`,
      {
        method: 'POST',
        headers: {
          apikey: shared.secret,
          Authorization: `Bearer ${shared.secret}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          p_bucket: bucket,
          p_window_seconds: windowSeconds,
          p_max: max
        })
      }
    );
    if (!res.ok) return null;
    // The RPC returns true when the call is allowed.
    return (await res.json()) === false;
  } catch {
    return null;
  }
}

async function limited(
  key: string,
  max: number,
  windowMs: number,
  options: RateLimitOptions
): Promise<boolean> {
  const localAllowed = takeLocal(key, max, windowMs);

  // Fast path: inside the local budget and the shared tier is optional.
  if (localAllowed && !options.requireShared) return false;

  const verdict = await sharedBlocked(
    key,
    max,
    Math.max(1, Math.round(windowMs / 1000)),
    options
  );

  // No shared state: the local verdict is all we have.
  if (verdict === null) return !localAllowed;
  return verdict;
}

/**
 * Credential-checking limiter. Consults the shared counter on every call when
 * one is configured, since per-isolate windows are effectively unshared here.
 */
export function rateLimited(
  key: string,
  options: RateLimitOptions = {}
): Promise<boolean> {
  return limited(key, MAX_ATTEMPTS, WINDOW_MS, {
    ...options,
    requireShared: true
  });
}

/** Higher-budget limiter for high-frequency read endpoints (search-as-you-type). */
export function rateLimitedSearch(
  key: string,
  options: RateLimitOptions = {}
): Promise<boolean> {
  return limited(key, MAX_SEARCH_ATTEMPTS, WINDOW_MS, options);
}

/**
 * Read-endpoint limiter with an explicit budget that the shared counter
 * enforces on every call.
 *
 * `rateLimitedSearch` only reaches the shared tier once an isolate's local
 * budget is spent — and this deployment spreads requests over many
 * short-lived isolates, so a scraper sending one request at a time never
 * exhausts any single one (measured: 130 sequential requests to one endpoint
 * all returned 200). For endpoints where that is unacceptable (health and
 * dispatch hit Postgres/R2 per request) the shared counter must see every
 * call. The SQL cost is one RPC per request, which is cheaper than the work
 * the endpoint already does.
 */
export function rateLimitedStrict(
  key: string,
  max: number,
  options: RateLimitOptions = {}
): Promise<boolean> {
  return limited(key, max, WINDOW_MS, { ...options, requireShared: true });
}

/** Test seam: forget every local window. */
export function resetRateLimits(): void {
  buckets.clear();
}

/** Test seam: how many keys the local map is currently tracking. */
export function trackedKeyCount(): number {
  return buckets.size;
}
