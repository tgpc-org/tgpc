import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
	DISPATCH_PREFIX,
	dispatchNameFromKey,
	isDispatchName
} from './dispatchNames.ts';

test('accepts real dispatch file names', () => {
	for (const name of ['DL01052023.pdf', 'DL03062020C.pdf', 'DL31122025.pdf', 'dl01052023.pdf']) {
		assert.equal(isDispatchName(name), true, name);
	}
});

test('rejects names that are not dispatch PDFs', () => {
	for (const name of [
		'',
		'rph.json',
		'DL01052023.txt',
		'DL01052023',
		'DL0105202.pdf', // 7 digits
		'DL010520233.pdf', // 9 digits
		'XX01052023.pdf',
		'../DL01052023.pdf',
		'dispatch/DL01052023.pdf',
		'DL01052023.pdf.exe',
		'DL01052023 .pdf',
		`DL${'0'.repeat(30)}.pdf` // over the length cap
	]) {
		assert.equal(isDispatchName(name), false, name);
	}
});

test('maps only keys under the dispatch prefix', () => {
	assert.equal(DISPATCH_PREFIX, 'dispatch/');
	assert.equal(dispatchNameFromKey('dispatch/DL01052023.pdf'), 'DL01052023.pdf');
	assert.equal(dispatchNameFromKey('dispatch/DL03062020C.pdf'), 'DL03062020C.pdf');
});

test('ignores keys outside the prefix or in nested folders', () => {
	for (const key of [
		'DL01052023.pdf',
		'photos/abc.jpg',
		'rph.json',
		'dispatch/nested/DL01052023.pdf',
		'dispatch/notes.txt',
		'notices/dispatch/DL01052023.pdf'
	]) {
		assert.equal(dispatchNameFromKey(key), null, key);
	}
});
