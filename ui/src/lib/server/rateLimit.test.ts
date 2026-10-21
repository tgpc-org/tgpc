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
  resetRateLimits,
  trackedKeyCount
} from './rateLimit.ts';

/** Minimal KV stand-in that records operations. */
function fakeKV(seed: Record<string, string> = {}) {
  const store = new Map(Object.entries(seed));
  const ops = { gets: 0, puts: 0 };
  return {
    ops,
    store,
    async get(key: string) {
      ops.gets++;
      return store.get(key) ?? null;
    },
    async put(key: string, value: string) {
      ops.puts++;
      store.set(key, value);
    }
  };
}

function platformWith(kv: ReturnType<typeof fakeKV>) {
  return { env: { RATE_LIMIT: kv } } as unknown as App.Platform;
}

describe('rateLimited', () => {
  it('allows a few attempts then blocks within the window', async () => {
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
    // Still blocked: a blocked hit must not refresh the budget.
    assert.equal(await rateLimited(key), true);
  });
});

describe('rateLimitedSearch', () => {
  it('allows a high budget before blocking', async () => {
    resetRateLimits();
    const key = 'test-search-' + Math.random();
    for (let i = 0; i < 120; i++) assert.equal(await rateLimitedSearch(key), false);
    assert.equal(await rateLimitedSearch(key), true);
  });
});

describe('shared KV tier', () => {
  it('is not consulted while the local budget lasts', async () => {
    resetRateLimits();
    const kv = fakeKV();
    const key = 'test-kv-idle-' + Math.random();
    for (let i = 0; i < 5; i++) await rateLimited(key, platformWith(kv));
    // Exactly five hits fill the budget without exceeding it, so ordinary
    // traffic must not touch KV at all.
    assert.equal(kv.ops.gets, 0);
    assert.equal(kv.ops.puts, 0);

    // The sixth hit exceeds the local budget and is the first global check.
    await rateLimited(key, platformWith(kv));
    assert.equal(kv.ops.gets, 1);
    assert.equal(kv.ops.puts, 1);
  });

  it('allows past the local budget while the shared counter has room', async () => {
    resetRateLimits();
    const kv = fakeKV();
    const key = 'test-kv-room-' + Math.random();
    for (let i = 0; i < 5; i++) await rateLimited(key, platformWith(kv));
    // Local window is spent, but the shared counter is well under the limit.
    assert.equal(await rateLimited(key, platformWith(kv)), false);
    assert.equal(await rateLimited(key, platformWith(kv)), false);
  });

  it('blocks when the shared counter is already at the limit', async () => {
    resetRateLimits();
    const window = Math.floor(Date.now() / 60_000);
    const key = 'test-kv-full-' + Math.random();
    const kv = fakeKV({ [`rl:${key}:${window}`]: '5' });
    for (let i = 0; i < 5; i++) await rateLimited(key, platformWith(kv));
    assert.equal(await rateLimited(key, platformWith(kv)), true);
  });

  it('falls back to the local verdict when KV throws', async () => {
    resetRateLimits();
    const key = 'test-kv-down-' + Math.random();
    const broken = {
      async get() {
        throw new Error('kv down');
      },
      async put() {
        throw new Error('kv down');
      }
    };
    const platform = { env: { RATE_LIMIT: broken } } as unknown as App.Platform;

    // Under budget: local verdict stands, KV is never reached.
    for (let i = 0; i < 5; i++) assert.equal(await rateLimited(key, platform), false);
    // Over budget and KV is broken: stay blocked rather than 500 the request.
    assert.equal(await rateLimited(key, platform), true);
  });
});

describe('memory bound', () => {
  it('caps the number of tracked keys', async () => {
    resetRateLimits();
    for (let i = 0; i < 6_000; i++) await rateLimited('flood-' + i);
    assert.ok(
      trackedKeyCount() <= 5_000,
      `map grew to ${trackedKeyCount()} keys`
    );
  });
});
