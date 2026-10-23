import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
	SITEMAP_CHUNK_SIZE,
	SITEMAP_MAX_CHUNKS,
	SITE_ORIGIN,
	chunkCount,
	chunkPath,
	escapeXml,
	isValidChunkNumber,
	sitemapIndexXml,
	urlsetXml
} from './sitemap.ts';

test('chunkCount divides the registry into 10k-URL chunks', () => {
	assert.equal(chunkCount(0), 0);
	assert.equal(chunkCount(-5), 0);
	assert.equal(chunkCount(1), 1);
	assert.equal(chunkCount(SITEMAP_CHUNK_SIZE), 1);
	assert.equal(chunkCount(SITEMAP_CHUNK_SIZE + 1), 2);
	assert.equal(chunkCount(77_049), 8);
});

test('chunkCount never exceeds the sitemap cap', () => {
	assert.equal(chunkCount(SITEMAP_MAX_CHUNKS * SITEMAP_CHUNK_SIZE * 2), SITEMAP_MAX_CHUNKS);
});

test('chunk numbers are 1-based positive integers within the cap', () => {
	for (const n of [1, 8, SITEMAP_MAX_CHUNKS]) assert.equal(isValidChunkNumber(n), true, String(n));
	for (const n of [0, -1, 1.5, NaN, SITEMAP_MAX_CHUNKS + 1, '1', null, undefined]) {
		assert.equal(isValidChunkNumber(n), false, String(n));
	}
	assert.equal(chunkPath(3), '/sitemap/3.xml');
});

test('index lists every chunk with absolute URLs', () => {
	const xml = sitemapIndexXml(3);
	assert.ok(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>'));
	assert.ok(xml.includes('<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'));
	for (const n of [1, 2, 3]) {
		assert.ok(xml.includes(`<loc>${SITE_ORIGIN}/sitemap/${n}.xml</loc>`), String(n));
	}
	assert.equal(xml.match(/<sitemap>/g)?.length, 3);
});

test('urlset lists profile URLs and nothing else', () => {
	const xml = urlsetXml(['TG083814', 'TG061012']);
	assert.ok(xml.includes(`<loc>${SITE_ORIGIN}/rph/TG083814</loc>`));
	assert.ok(xml.includes(`<loc>${SITE_ORIGIN}/rph/TG061012</loc>`));
	assert.equal(xml.match(/<url>/g)?.length, 2);
	assert.ok(xml.trimEnd().endsWith('</urlset>'));
});

test('escapeXml keeps a stray character from invalidating the document', () => {
	assert.equal(escapeXml('A&B<C>"D"\'E\''), 'A&amp;B&lt;C&gt;&quot;D&quot;&apos;E&apos;');
	const xml = urlsetXml(['A&B']);
	assert.ok(xml.includes('rph/A&amp;B'));
	assert.ok(!xml.includes('rph/A&B'));
});
