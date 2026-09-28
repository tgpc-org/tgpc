import assert from 'node:assert/strict';
import { test } from 'node:test';

import { buildCsv, safeFilename } from './exporters.ts';
import type { ExportContext } from './exporters';

function ctx(overrides: Partial<ExportContext> = {}): ExportContext {
  return {
    query: 'ram',
    rows: [
      {
        registration_number: 'TG063036',
        name: 'RAM KUMAR MOOD KISHAN',
        father_name: 'MOOD KISHAN',
        gender: 'Male',
        category: 'BPharm',
        validity_date: '31-Dec-2026',
        status: 'Active'
      }
    ],
    filteredCount: 1,
    totalCount: 200,
    isFiltered: true,
    filters: ['Category: BPharm'],
    ...overrides
  };
}

test('buildCsv: header, count and filter comment lines', () => {
  const lines = buildCsv(ctx(), new Date(2026, 8, 28, 10, 30)).split('\n');
  assert.match(lines[0], /^# TGPC RPh Index - Search: ram - /);
  assert.match(lines[1], /^# Results: 1 of 200 \(filtered\) \| Filters: Category: BPharm$/);
  assert.equal(lines[2], 'RPC NUMBER,NAME,FATHER NAME,GENDER,CATEGORY,VALID TILL,STATUS');
  assert.match(lines[3], /^"TG063036","RAM KUMAR MOOD KISHAN",/);
});

test('buildCsv: no filters line when unfiltered', () => {
  const lines = buildCsv(ctx({ isFiltered: false, filters: [] }), new Date()).split('\n');
  assert.equal(lines[1], '# Results: 1 of 200 | Filters: none');
});

test('buildCsv: CSV special chars are escaped by doubling quotes', () => {
  const lines = buildCsv(
    ctx({ rows: [{ registration_number: 'TG1', name: 'A "QUOTED" NAME', father_name: null, gender: null, category: 'BPharm', validity_date: null, status: null }] }),
    new Date()
  ).split('\n');
  assert.match(lines[3], /^"TG1","A ""QUOTED"" NAME",/);
});

test('buildCsv: formula-leading cells get apostrophe prefix', () => {
  const lines = buildCsv(
    ctx({ rows: [{ registration_number: '=SUM(A1)', name: '-DASH', father_name: null, gender: null, category: 'BPharm', validity_date: null, status: null }] }),
    new Date()
  ).split('\n');
  assert.match(lines[3], /^"'=SUM\(A1\)","'-DASH",/);
});

test('buildCsv: CRLF in query cannot split comment lines', () => {
  const lines = buildCsv(ctx({ query: 'ram\r\n# injected' }), new Date()).split('\n');
  // comment, counts, column header, single row — no injected line of its own
  assert.equal(lines.length, 4);
  assert.ok(!lines.some((l) => l.startsWith('# injected')));
});

test('safeFilename: strips hostile chars, caps length, defaults to all', () => {
  assert.equal(safeFilename('../../etc/passwd'), '....etcpasswd');
  assert.equal(safeFilename('a'.repeat(100)).length, 60);
  assert.equal(safeFilename(''), 'all');
});
