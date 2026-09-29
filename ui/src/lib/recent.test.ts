/**
 * Tests for the recently-viewed list (src/lib/recent.ts).
 *
 * The list is pure localStorage bookkeeping, so browser storage is
 * simulated with an in-memory store installed on globalThis — the same
 * shape the components see. Node 26 runs this directly via
 * `node --experimental-strip-types` (npm run test:unit).
 */

import assert from 'node:assert/strict';
import { describe, it, beforeEach } from 'node:test';

import { clearRecent, recentRecords, recordViewed } from './recent.ts';

type Store = Record<string, string>;

function installStorage(): { store: Store; thrown: { on: string | null } } {
  const store: Store = {};
  const thrown = { on: null as string | null };
  const storage = {
    getItem: (k: string) => {
      if (thrown.on === k) throw new Error('simulated private-mode failure');
      return k in store ? store[k] : null;
    },
    setItem: (k: string, v: string) => {
      if (thrown.on === k) throw new Error('simulated quota failure');
      store[k] = v;
    },
    removeItem: (k: string) => {
      if (thrown.on === k) throw new Error('simulated removal failure');
      delete store[k];
    }
  };
  (globalThis as { localStorage?: unknown }).localStorage = storage;
  return { store, thrown };
}

describe('recent records', () => {
  beforeEach(() => {
    installStorage();
  });

  it('returns empty with nothing stored', () => {
    assert.deepEqual(recentRecords(), []);
  });

  it('stores a view and returns it newest-first', () => {
    recordViewed({ registration_number: 'tg061874', name: 'Reddy Rajkiran Reddy', category: 'BPharm' });
    const list = recentRecords();
    assert.equal(list.length, 1);
    assert.equal(list[0].registration_number, 'TG061874');
    assert.equal(list[0].name, 'Reddy Rajkiran Reddy');
  });

  it('moves a repeat view to the front instead of duplicating', () => {
    recordViewed({ registration_number: 'TG061874', name: 'A', category: 'BPharm' });
    recordViewed({ registration_number: 'TS000911', name: 'B', category: 'DPharm' });
    recordViewed({ registration_number: 'TG061874', name: 'A', category: 'BPharm' });
    const list = recentRecords();
    assert.deepEqual(list.map((r) => r.registration_number), ['TG061874', 'TS000911']);
  });

  it('caps the list at four entries', () => {
    for (const [i, reg] of ['TG1', 'TG2', 'TG3', 'TG4', 'TG5'].entries()) {
      recordViewed({ registration_number: reg, name: `N${i}`, category: 'BPharm' });
    }
    const list = recentRecords();
    assert.deepEqual(list.map((r) => r.registration_number), ['TG5', 'TG4', 'TG3', 'TG2']);
  });

  it('survives corrupt stored JSON', () => {
    localStorage.setItem('tgpc_recent_rph', '{not json');
    assert.deepEqual(recentRecords(), []);
  });

  it('drops malformed entries but keeps valid ones', () => {
    localStorage.setItem(
      'tgpc_recent_rph',
      JSON.stringify([{ registration_number: 'TG1', name: 'A', category: 'BPharm', viewedAt: 1 }, null, { name: 'no rpc' }, 42])
    );
    const list = recentRecords();
    assert.equal(list.length, 1);
    assert.equal(list[0].registration_number, 'TG1');
  });

  it('swallows storage failures (private mode / full quota)', () => {
    const { thrown } = installStorage();
    thrown.on = 'tgpc_recent_rph';
    assert.doesNotThrow(() => recordViewed({ registration_number: 'TG1', name: 'A', category: 'BPharm' }));
    assert.doesNotThrow(() => recentRecords());
    assert.doesNotThrow(() => clearRecent());
    assert.deepEqual(recentRecords(), []);
  });

  it('clear removes all entries', () => {
    recordViewed({ registration_number: 'TG1', name: 'A', category: 'BPharm' });
    clearRecent();
    assert.deepEqual(recentRecords(), []);
  });
});
