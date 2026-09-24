/**
 * Tests for the public-search row ceiling (CODE_REVIEW.md H5).
 *
 * The behavioural half pins `isTruncated`, which drives the "narrow your
 * search" hint. The source half is the real regression guard: it reads
 * `api.ts` and fails if any row-fetching query loses its limit, or asks for
 * more rows than the cap — the exact mistake H5 describes (`lim: 100000`).
 *
 * Uses only `node:test`, so it needs no extra dependencies. Run with:
 *
 *   npm run test:unit
 */

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { describe, it } from 'node:test';

import { MAX_SEARCH_RESULTS, isTruncated } from './searchLimits.ts';

const API_SOURCE = readFileSync(new URL('./api.ts', import.meta.url), 'utf8');

/**
 * Comments are stripped before scanning: this file's own history note
 * ("previously `lim: 100000`") must not be mistaken for a live call site.
 */
const API_CODE = API_SOURCE.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ');

/**
 * The builder steps of each `.from('rph')` query.
 *
 * Slicing to the next `.from(`/`await` is deliberate: filters and the limit
 * are applied as separate statements (`query = query.limit(...)`) before the
 * query is awaited, so a semicolon-bounded slice would miss them.
 */
function rphQueryWindows(): string[] {
  const windows: string[] = [];
  let cursor = 0;
  while (true) {
    const at = API_CODE.indexOf(`.from('rph')`, cursor);
    if (at === -1) break;
    const stops = [API_CODE.indexOf('await', at), API_CODE.indexOf(`.from('rph')`, at + 1)].filter(
      (i) => i !== -1
    );
    windows.push(API_CODE.slice(at, stops.length ? Math.min(...stops) : API_CODE.length));
    cursor = at + 1;
  }
  return windows;
}

describe('MAX_SEARCH_RESULTS', () => {
  it('is a small positive integer', () => {
    assert.equal(Number.isInteger(MAX_SEARCH_RESULTS), true);
    assert.ok(MAX_SEARCH_RESULTS > 0);
    // A ceiling in the thousands is no ceiling for a public endpoint.
    assert.ok(MAX_SEARCH_RESULTS <= 1000, `cap is ${MAX_SEARCH_RESULTS}`);
  });
});

describe('isTruncated', () => {
  it('is false below the ceiling', () => {
    assert.equal(isTruncated(0), false);
    assert.equal(isTruncated(1), false);
    assert.equal(isTruncated(MAX_SEARCH_RESULTS - 1), false);
  });

  it('is true at and above the ceiling', () => {
    assert.equal(isTruncated(MAX_SEARCH_RESULTS), true);
    assert.equal(isTruncated(MAX_SEARCH_RESULTS + 1), true);
    assert.equal(isTruncated(100000), true);
  });
});

describe('api.ts row limits', () => {
  it('passes the shared cap to search_pharmacists rather than a literal', () => {
    assert.match(
      API_CODE,
      /rpc\('search_pharmacists',\s*\{\s*q,\s*lim:\s*MAX_SEARCH_RESULTS\s*\}\)/,
      'the ranked RPC path must be capped via MAX_SEARCH_RESULTS'
    );
  });

  it('caps every lim: / limit() call site', () => {
    const sites = [
      ...API_CODE.matchAll(/\blim:\s*([A-Za-z_$\w]+|\d+)|\.limit\(([A-Za-z_$\w]+|\d+)\)/g)
    ].map((m) => m[1] ?? m[2]);

    // Ranked RPC, its PostgREST fallback, refiners, advanced search.
    assert.ok(sites.length >= 4, `expected 4 capped call sites, found ${sites.length}`);
    for (const arg of sites) {
      if (/^\d+$/.test(arg)) {
        assert.ok(Number(arg) <= MAX_SEARCH_RESULTS, `requested ${arg} rows, cap is ${MAX_SEARCH_RESULTS}`);
      } else {
        assert.equal(arg, 'MAX_SEARCH_RESULTS', `call site should use the shared cap, got ${arg}`);
      }
    }
  });

  it('limits every row-fetching rph query', () => {
    const windows = rphQueryWindows();
    assert.ok(windows.length > 0, 'expected rph queries to be found');
    for (const window of windows) {
      // Single-row and count-only queries are bounded by construction.
      if (
        window.includes('.single(') ||
        window.includes('.maybeSingle(') ||
        window.includes('head: true')
      ) {
        continue;
      }
      assert.ok(
        window.includes('.limit('),
        `unbounded rph query: ${window.replace(/\s+/g, ' ').slice(0, 90)}`
      );
    }
  });
});
