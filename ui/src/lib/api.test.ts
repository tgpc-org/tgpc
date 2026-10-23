/**
 * Tests for the browser API client's outcome mapping (`api.ts`).
 *
 * The bug this pins: `getRecord` used to answer `null` for a 404, a 429, a
 * 5xx and a dead network alike, so the drawer told users "No record found"
 * for a record that exists but could not be fetched. Every case now has its
 * own tag, and the search path distinguishes "no matches" from "no answer".
 *
 * `fetch` is stubbed at the global — the functions are thin wrappers, so the
 * status/header/throw mapping is the whole contract worth testing.
 *
 * Run with: npm run test:unit
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { getRecord, searchRecords, searchWithRefiners } from './api.ts';
import type { PharmacistRecord } from './types.ts';

const realFetch = globalThis.fetch;

/** Replace `fetch` for one test and record the URLs it was asked for. */
function stubFetch(handler: (_url: string) => Response | Promise<Response>): string[] {
  const calls: string[] = [];
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const target = typeof input === 'string' ? input : input instanceof URL ? input.toString() : input.url;
    calls.push(target);
    return handler(target);
  }) as typeof fetch;
  return calls;
}

const RECORD = {
  registration_number: 'TG083814',
  name: 'A SAMPLE PHARMACIST',
  category: 'Pharmacist',
  status: 'Active'
} as unknown as PharmacistRecord;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

test('getRecord: 200 returns the record', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  const calls = stubFetch(() => json(RECORD));
  const outcome = await getRecord('tg083814');
  assert.deepEqual(outcome, { kind: 'ok', record: RECORD });
  assert.equal(calls.length, 1);
  assert.match(calls[0], /reg=TG083814/, 'registration number is upper-cased before the request');
});

test('getRecord: 404 is not-found, never a transport failure', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  stubFetch(() => new Response('Not found', { status: 404 }));
  assert.deepEqual(await getRecord('TG000000'), { kind: 'not-found' });
});

test('getRecord: 400 is invalid input', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  stubFetch(() => new Response('Bad registration number', { status: 400 }));
  assert.deepEqual(await getRecord('??'), { kind: 'invalid' });
});

test('getRecord: blank input makes no request', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  const calls = stubFetch(() => json(RECORD));
  assert.deepEqual(await getRecord('   '), { kind: 'invalid' });
  assert.equal(calls.length, 0);
});

test('getRecord: 429 carries the Retry-After countdown', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  stubFetch(() => new Response('Too Many Requests', { status: 429, headers: { 'Retry-After': '37' } }));
  assert.deepEqual(await getRecord('TG083814'), { kind: 'rate-limited', retryAfterSeconds: 37 });
});

test('getRecord: 429 without a usable Retry-After still rate-limits', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  for (const header of [null, 'later', '0']) {
    stubFetch(() => new Response('Too Many Requests', {
      status: 429,
      headers: header === null ? undefined : { 'Retry-After': header }
    }));
    assert.deepEqual(await getRecord('TG083814'), { kind: 'rate-limited' }, `header ${header}`);
  }
});

test('getRecord: 500 and 502 are unavailable, not not-found', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  for (const status of [500, 502, 503]) {
    stubFetch(() => new Response('nope', { status }));
    assert.deepEqual(await getRecord('TG083814'), { kind: 'unavailable' }, `status ${status}`);
  }
});

test('getRecord: a network throw or a broken body is unavailable', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  stubFetch(() => { throw new TypeError('Failed to fetch'); });
  assert.deepEqual(await getRecord('TG083814'), { kind: 'unavailable' });

  stubFetch(() => new Response('<html>oops</html>', { status: 200 }));
  assert.deepEqual(await getRecord('TG083814'), { kind: 'unavailable' });
});

test('searchRecords: short queries never hit the network', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  const calls = stubFetch(() => json([]));
  assert.deepEqual(await searchRecords('ab'), { kind: 'ok', records: [] });
  assert.equal(calls.length, 0);
});

test('searchRecords: 200 returns ok with rows', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  stubFetch(() => json([RECORD]));
  const outcome = await searchRecords('tg083814');
  assert.equal(outcome.kind, 'ok');
  assert.equal(outcome.kind === 'ok' && outcome.records.length, 1);
});

test('searchRecords: an empty 200 is ok, a 429 is not', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  stubFetch(() => json([]));
  assert.deepEqual(await searchRecords('zzzzzz'), { kind: 'ok', records: [] });

  stubFetch(() => new Response('', { status: 429, headers: { 'Retry-After': '60' } }));
  assert.deepEqual(await searchRecords('zzzzzz'), { kind: 'rate-limited', retryAfterSeconds: 60 });

  stubFetch(() => { throw new TypeError('Failed to fetch'); });
  assert.deepEqual(await searchRecords('zzzzzz'), { kind: 'unavailable' });

  stubFetch(() => new Response('boom', { status: 500 }));
  assert.deepEqual(await searchRecords('zzzzzz'), { kind: 'unavailable' });
});

test('searchWithRefiners: refiner-only searches report failures too', async (t) => {
  t.after(() => { globalThis.fetch = realFetch; });
  const calls = stubFetch(() => new Response('boom', { status: 502 }));
  assert.deepEqual(await searchWithRefiners('', { status: 'Active' }), { kind: 'unavailable' });
  assert.equal(calls.length, 1);
  assert.match(calls[0], /^\/api\/refine\?/);

  stubFetch(() => json([RECORD]));
  const ok = await searchWithRefiners('', { status: 'Active' });
  assert.equal(ok.kind, 'ok');
  assert.equal(ok.kind === 'ok' && ok.records.length, 1);
});
