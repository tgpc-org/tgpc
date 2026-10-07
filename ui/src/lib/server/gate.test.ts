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

  it('denies API routes and redirects pages', () => {
    assert.equal(gatePath('/api/usage').kind, 'api-deny');
    // Session endpoints re-check auth themselves (defence in depth);
    // the hook denies strangers before they get there.
    assert.equal(gatePath('/api/admin/contacts').kind, 'api-deny');
    assert.equal(gatePath('/').kind, 'page-login');
    assert.equal(gatePath('/notice').kind, 'page-login');
    assert.equal(gatePath('/rph/TS000097').kind, 'page-login');
  });

  it('does not treat lookalike paths as open', () => {
    assert.equal(gatePath('/administrator').kind, 'page-login');
    assert.equal(gatePath('/api/admin-evil').kind, 'api-deny');
    assert.equal(gatePath('/notice.json').kind, 'page-login');
  });
});
