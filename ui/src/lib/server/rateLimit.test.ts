/**
 * Tests for the rate limiters.
 * Run with:
 *
 *   npm run test:unit
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  rateLimited,
  rateLimitedSearch,
  rateLimitedStrict,
  resetRateLimits,
  trackedKeyCount,
  type SharedCounter
} from './rateLimit.ts';

const SHARED: SharedCounter = {
  url: 'https://example.supabase.co',
  secret: 'service-key'
};

/** Fake PostgREST call. `allowed` decides what the RPC returns per call. */
function fakeCall(handler: (_body: Record<string, unknown>) => boolean) {
  const seen: Record<string, unknown>[] = [];
  let count = 0;
  const call = (async (_url: string, init?: RequestInit) => {
    const body = JSON.parse(String(init?.body ?? '{}'));
    seen.push(body);
    count++;
    return { ok: true, json: async () => handler(body) };
  }) as unknown as typeof fetch;
  return { call, seen, get calls() { return count; } };
}

describe('rateLimited', () => {
  it('allows a few attempts then blocks when no shared counter exists', async () => {
    resetRateLimits();
    const key = 'test-login-' + Math.random();
    for (let i = 0; i < 5; i++) assert.equal(await rateLimited(key), false);
    assert.equal(await rateLimited(key), true);
  });

  it('tracks keys independently', async () => {
    resetRateLimits();
    const a = 'test-a-' + Math.random();
    const b = 'test-b-' + Math.random();
    for (let i = 0; i < 5; i++) await rateLimited(a);
    assert.equal(await rateLimited(a), true);
    assert.equal(await rateLimited(b), false);
  });

  it('does not let repeated blocked calls extend the window', async () => {
    resetRateLimits();
    const key = 'test-spam-' + Math.random();
    for (let i = 0; i < 5; i++) await rateLimited(key);
    assert.equal(await rateLimited(key), true);
    assert.equal(await rateLimited(key), true);
  });
});

describe('shared counter (credential paths)', () => {
  it('is consulted on every attempt, not just after the local budget', async () => {
    resetRateLimits();
    const key = 'cred-' + Math.random();
    const fake = fakeCall(() => true);

    for (let i = 0; i < 5; i++) {
      assert.equal(await rateLimited(key, { shared: SHARED, call: fake.call }), false);
    }
    // Per-isolate windows proved useless in production, so the shared counter
    // must see every credential attempt.
    assert.equal(fake.calls, 5);
  });

  it('blocks as soon as the shared counter denies, even with local budget left', async () => {
    resetRateLimits();
    const key = 'cred-deny-' + Math.random();
    const fake = fakeCall(() => false);
    assert.equal(await rateLimited(key, { shared: SHARED, call: fake.call }), true);
  });

  it('sends a hashed bucket, never the raw key', async () => {
    resetRateLimits();
    const key = '203.0.113.7:admin';
    const fake = fakeCall(() => true);
    await rateLimited(key, { shared: SHARED, call: fake.call });

    const body = fake.seen[0];
    assert.match(String(body.p_bucket), /^[0-9a-f]{64}$/);
    assert.ok(!JSON.stringify(body).includes('203.0.113.7'));
    assert.equal(body.p_window_seconds, 60);
    assert.equal(body.p_max, 5);
  });

  it('falls back to the local verdict when the shared counter errors', async () => {
    resetRateLimits();
    const key = 'cred-down-' + Math.random();
    const call = (async () => {
      throw new Error('supabase down');
    }) as unknown as typeof fetch;

    // Under the local budget: stay available.
    for (let i = 0; i < 5; i++) {
      assert.equal(await rateLimited(key, { shared: SHARED, call }), false);
    }
    // Over the local budget with no shared verdict: block, do not 500.
    assert.equal(await rateLimited(key, { shared: SHARED, call }), true);
  });

  it('falls back to the local verdict on a non-2xx response', async () => {
    resetRateLimits();
    const key = 'cred-500-' + Math.random();
    const call = (async () => ({ ok: false, json: async () => true })) as unknown as typeof fetch;
    assert.equal(await rateLimited(key, { shared: SHARED, call }), false);
  });
});

describe('rateLimitedSearch', () => {  it('allows a high budget before blocking', async () => {
    resetRateLimits();
    const key = 'test-search-' + Math.random();
    for (let i = 0; i < 120; i++) assert.equal(await rateLimitedSearch(key), false);
    assert.equal(await rateLimitedSearch(key), true);
  });

  it('does not touch the shared counter for ordinary traffic', async () => {
    resetRateLimits();
    const key = 'search-quiet-' + Math.random();
    const fake = fakeCall(() => true);
    for (let i = 0; i < 120; i++) {
      await rateLimitedSearch(key, { shared: SHARED, call: fake.call });
    }
    // 120 searches must cost zero SQL: the shared tier is the abuse path only.
    assert.equal(fake.calls, 0);

    await rateLimitedSearch(key, { shared: SHARED, call: fake.call });
    assert.equal(fake.calls, 1);
  });

  it('lets the shared counter allow a burst the local window would refuse', async () => {
    resetRateLimits();
    const key = 'search-burst-' + Math.random();
    const fake = fakeCall(() => true);
    for (let i = 0; i < 120; i++) await rateLimitedSearch(key, { shared: SHARED, call: fake.call });
    assert.equal(await rateLimitedSearch(key, { shared: SHARED, call: fake.call }), false);
  });
});

describe('rateLimitedStrict', () => {
  it('consults the shared counter on every call and honours its budget', async () => {
    resetRateLimits();
    const key = 'strict-' + Math.random();
    const fake = fakeCall((body) => Number(body.p_max) > 2);

    // The fake RPC allows everything while p_max is 3, so every call must be
    // allowed and every call must reach the counter (no local-only fast path).
    for (let i = 0; i < 5; i++) {
      assert.equal(await rateLimitedStrict(key, 3, { shared: SHARED, call: fake.call }), false);
    }
    assert.equal(fake.calls, 5);
    assert.equal(fake.seen[0].p_max, 3);
  });

  it('blocks as soon as the shared counter denies, even with local budget left', async () => {
    resetRateLimits();
    const key = 'strict-deny-' + Math.random();
    const fake = fakeCall(() => false);
    assert.equal(await rateLimitedStrict(key, 60, { shared: SHARED, call: fake.call }), true);
  });

  it('falls back to the local budget when the shared counter errors', async () => {
    resetRateLimits();
    const key = 'strict-down-' + Math.random();
    const call = (async () => {
      throw new Error('supabase down');
    }) as unknown as typeof fetch;

    for (let i = 0; i < 3; i++) {
      assert.equal(await rateLimitedStrict(key, 3, { shared: SHARED, call }), false);
    }
    // Local window (max 3) is spent and the shared verdict is unavailable:
    // block rather than fail open or 500.
    assert.equal(await rateLimitedStrict(key, 3, { shared: SHARED, call }), true);
  });
});

describe('memory bound', () => {
  it('caps the number of tracked keys', async () => {
    resetRateLimits();
    for (let i = 0; i < 6_000; i++) await rateLimited('flood-' + i);
    assert.ok(trackedKeyCount() <= 5_000, `map grew to ${trackedKeyCount()} keys`);
  });
});
