import { test } from 'node:test';
import assert from 'node:assert/strict';
import { RECORD_COLUMNS, firstRecord, recordQueryUrl } from './recordQuery.ts';

test('recordQueryUrl targets the rph table with every advertised column', () => {
	const url = recordQueryUrl('https://proj.supabase.co', 'TG083814');
	assert.equal(
		url,
		`https://proj.supabase.co/rest/v1/rph?registration_number=eq.TG083814&select=${RECORD_COLUMNS}`
	);
	for (const col of RECORD_COLUMNS.split(',')) {
		assert.ok(url.includes(col), col);
	}
});

test('recordQueryUrl tolerates trailing slashes and encodes the registration number', () => {
	assert.equal(
		recordQueryUrl('https://proj.supabase.co//', 'TG083814'),
		`https://proj.supabase.co/rest/v1/rph?registration_number=eq.TG083814&select=${RECORD_COLUMNS}`
	);
	const url = recordQueryUrl('https://proj.supabase.co', 'A&B=1');
	assert.ok(url.includes('registration_number=eq.A%26B%3D1'), url);
});

test('firstRecord returns the row when PostgREST answers with one', () => {
	const row = { registration_number: 'TG083814', name: 'SANTHOSH M' };
	const got = firstRecord([row]);
	assert.ok(got);
	assert.equal(got.name, 'SANTHOSH M');
});

test('firstRecord returns null for empty, malformed or non-array bodies', () => {
	for (const body of [[], null, undefined, {}, 'oops', [null], ['x']]) {
		assert.equal(firstRecord(body), null, JSON.stringify(body));
	}
});
