/**
 * Tests for the list-sizing arithmetic behind `fitToViewport` (the shell
 * supplies the footer position; this is the part that can be wrong silently).
 *
 * Uses only `node:test`, so it needs no extra dependencies. Run with:
 *
 *   npm run test:unit
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { FIT_GAP, availableHeight } from './fitToViewport.ts';

describe('availableHeight', () => {
  it('returns the space between the list top and the footer, minus the gap', () => {
    assert.equal(availableHeight(200, 600), 390);
    assert.equal(availableHeight(68.4, 754.6), 676);
  });

  it('uses the default gap but honours an explicit one', () => {
    assert.equal(FIT_GAP, 10);
    assert.equal(availableHeight(0, 100, 0), 100);
    assert.equal(availableHeight(0, 100, 24), 76);
  });

  it('clamps to 0 instead of returning a negative max-height', () => {
    // Footer above the list: a short viewport, not a layout to honour.
    assert.equal(availableHeight(600, 500), 0);
    assert.equal(availableHeight(10, 15), 0);
    // Exactly touching the footer leaves no legal height either.
    assert.equal(availableHeight(100, 110), 0);
    assert.equal(availableHeight(100, 111), 1);
  });

  it('treats unmeasurable geometry as unconstrained-by-zero', () => {
    assert.equal(availableHeight(Number.NaN, 600), 0);
    assert.equal(availableHeight(200, Number.NaN), 0);
    assert.equal(availableHeight(Number.POSITIVE_INFINITY, 600), 0);
  });
});
