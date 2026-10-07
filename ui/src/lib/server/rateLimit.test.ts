/**
 * Tests for the rate limiters.
 * Run with:
 *
 *   npm run test:unit
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { rateLimited, rateLimitedSearch } from './rateLimit.ts';

describe('rateLimited', () => {
  it('allows a few attempts then blocks within the window', () => {
    const key = 'test-login-' + Math.random();
    for (let i = 0; i < 5; i++) assert.equal(rateLimited(key), false);
    assert.equal(rateLimited(key), true);
  });

  it('tracks keys independently', () => {
    const a = 'test-a-' + Math.random();
    const b = 'test-b-' + Math.random();
    for (let i = 0; i < 5; i++) rateLimited(a);
    assert.equal(rateLimited(a), true);
    assert.equal(rateLimited(b), false);
  });
});

describe('rateLimitedSearch', () => {
  it('allows a high budget before blocking', () => {
    const key = 'test-search-' + Math.random();
    for (let i = 0; i < 120; i++) assert.equal(rateLimitedSearch(key), false);
    assert.equal(rateLimitedSearch(key), true);
  });
});
