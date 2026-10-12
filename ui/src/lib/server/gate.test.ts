/**
 * Tests for the site-wide gate path classification.
 * Run with:
 *
 *   npm run test:unit
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import { gatePath } from './gate.ts';

describe('gatePath', () => {
	it('leaves the login flow open', () => {
		assert.equal(gatePath('/admin').kind, 'open');
		assert.equal(gatePath('/api/admin').kind, 'open');
	});

	it('leaves inert login assets open', () => {
		assert.equal(gatePath('/_app/immutable/x.js').kind, 'open');
		assert.equal(gatePath('/favicon.svg').kind, 'open');
		assert.equal(gatePath('/manifest.json').kind, 'open');
	});

	it('leaves the public portal open without a session', () => {
		for (const p of ['/', '/notice', '/dispatch', '/sitemap.xml', '/rph/TS000097']) {
			assert.equal(gatePath(p).kind, 'open');
		}
		for (const p of [
			'/api/stats',
			'/api/search',
			'/api/record',
			'/api/refine',
			'/api/notice',
			'/api/dispatch',
			'/api/dispatch/DL14072026.pdf',
			'/api/health'
		]) {
			assert.equal(gatePath(p).kind, 'open');
		}
	});

	it('denies admin APIs and redirects gated pages', () => {
		assert.equal(gatePath('/api/usage').kind, 'api-deny');
		// Session endpoints re-check auth themselves (defence in depth);
		// the hook denies strangers before they get there.
		assert.equal(gatePath('/api/admin/contacts').kind, 'api-deny');
		assert.equal(gatePath('/sandbox').kind, 'page-login');
	});

	it('does not treat lookalike paths as open', () => {
		assert.equal(gatePath('/administrator').kind, 'page-login');
		assert.equal(gatePath('/api/admin-evil').kind, 'api-deny');
		assert.equal(gatePath('/api/stats-evil').kind, 'api-deny');
		assert.equal(gatePath('/notice.json').kind, 'page-login');
	});
});
