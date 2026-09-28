import assert from 'node:assert/strict';
import { test } from 'node:test';

import { buildSearchQuery, parseSearchUrl, DEFAULT_SEARCH_STATE } from './searchUrl.ts';

function parse(qs: string) {
  return parseSearchUrl(new URLSearchParams(qs));
}

test('parseSearchUrl: empty params yield defaults', () => {
  assert.deepEqual(parse(''), DEFAULT_SEARCH_STATE);
});

test('parseSearchUrl: reads all whitelisted keys', () => {
  const s = parse('?q=ram&cat=BPharm&sort=validity_date&dir=desc&rpc=TG1&name=kumar&father=reddy&gender=Male&status=Active&valid=2026-12-31');
  assert.equal(s.q, 'ram');
  assert.equal(s.cat, 'BPharm');
  assert.equal(s.sort, 'validity_date');
  assert.equal(s.dir, 'desc');
  assert.equal(s.rpc, 'TG1');
  assert.equal(s.name, 'kumar');
  assert.equal(s.father, 'reddy');
  assert.equal(s.gender, 'Male');
  assert.equal(s.status, 'Active');
  assert.equal(s.valid, '2026-12-31');
});

test('parseSearchUrl: unknown cat/sort fall back to defaults', () => {
  const s = parse('?cat=Hacker&sort=DROP_TABLE&gender=X&status=Maybe&valid=31/12/2026');
  assert.equal(s.cat, 'all');
  assert.equal(s.sort, 'rank');
  assert.equal(s.gender, '');
  assert.equal(s.status, '');
  assert.equal(s.valid, ''); // only ISO yyyy-mm-dd accepted
});

test('parseSearchUrl: dir anything but desc is asc', () => {
  assert.equal(parse('?dir=DESC').dir, 'asc'); // case-sensitive whitelist
  assert.equal(parse('?dir=desc').dir, 'desc');
});

test('parseSearchUrl: values are trimmed and length-capped', () => {
  const s = parse(`?q=${'x'.repeat(200)}&rpc=${'y'.repeat(200)}`);
  assert.equal(s.q.length, 80);
  assert.equal(s.rpc.length, 24);
  const padded = parse('?q=%20ram%20');
  assert.equal(padded.q, 'ram');
});

test('parseSearchUrl: crafted params never inject unknown keys', () => {
  const s = parse('?q=a&__proto__=x&constructor=y');
  assert.equal(Object.keys(s).length, Object.keys(DEFAULT_SEARCH_STATE).length);
  assert.deepEqual(Object.keys(s).sort(), Object.keys(DEFAULT_SEARCH_STATE).sort());
});

test('buildSearchQuery: round-trips a full state', () => {
  const sp = new URLSearchParams('?q=ram&cat=MPharm&sort=name&dir=desc&gender=Female&status=Inactive&valid=2027-01-05');
  const out = buildSearchQuery(parseSearchUrl(sp));
  assert.equal(out.toString(), sp.toString());
});

test('buildSearchQuery: omits defaults and keeps fixed key order', () => {
  const out = buildSearchQuery({ ...DEFAULT_SEARCH_STATE, q: 'sai', dir: 'desc' });
  // dir=desc with sort=rank is still dropped (dir only meaningful with sort)
  assert.equal(out.toString(), 'q=sai');
});

test('buildSearchQuery: empty state serializes to nothing', () => {
  assert.equal(buildSearchQuery(DEFAULT_SEARCH_STATE).toString(), '');
});
