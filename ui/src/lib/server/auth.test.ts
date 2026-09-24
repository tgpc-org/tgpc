/**
 * Tests for the admin session auth (CODE_REVIEW.md findings C2, C3).
 *
 * Uses only `node:test` and WebCrypto, so it needs no extra dependencies.
 * Run with:
 *
 *   npm run test:unit
 *
 * WebCrypto and `btoa` behave identically here and in the Cloudflare Workers
 * runtime, so these results carry over to production.
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  SESSION_COOKIE,
  createSession,
  getAdminSecret,
  isAuthed,
  safeEqual,
  verifySession
} from './auth.ts';

const SECRET = 'correct-horse-battery-staple';

const cookieJar = (value: string | undefined) => ({
  get: (name: string) => (name === SESSION_COOKIE ? value : undefined)
});

const platformWith = (env: Record<string, string>) => ({ env }) as unknown as App.Platform;

describe('safeEqual', () => {
  it('matches identical strings', async () => {
    assert.equal(await safeEqual('abc', 'abc'), true);
  });

  it('rejects strings differing by one character', async () => {
    assert.equal(await safeEqual('abc', 'abd'), false);
  });

  it('rejects strings of different length without leaking length', async () => {
    assert.equal(await safeEqual('abc', 'abcdef'), false);
  });

  it('matches two empty strings', async () => {
    assert.equal(await safeEqual('', ''), true);
  });
});

describe('session tokens', () => {
  it('accepts a freshly minted token', async () => {
    const token = await createSession(SECRET);
    assert.equal(await verifySession(token, SECRET), true);
  });

  it('rejects a token signed with a different secret', async () => {
    const token = await createSession(SECRET);
    assert.equal(await verifySession(token, 'some-other-secret'), false);
  });

  it('rejects a tampered signature', async () => {
    const token = await createSession(SECRET);
    const [exp, sig] = token.split('.');		// Flip rather than overwrite with a fixed character: the signature is
		// base64url, so its first character is already 'A' about 1 time in 64.
		// Overwriting made the "tampered" token byte-identical to the valid one,
		// so this assertion failed intermittently in CI (~1.8% of runs).
		const flipped = sig[0] === 'A' ? 'B' : 'A';
		assert.equal(await verifySession(`${exp}.${flipped}${sig.slice(1)}`, SECRET), false);
  });

  it('rejects an expiry extended by the client', async () => {
    const token = await createSession(SECRET);
    const [exp, sig] = token.split('.');
    assert.equal(await verifySession(`${Number(exp) + 99999}.${sig}`, SECRET), false);
  });

  it('rejects an already-expired token', async () => {
    const past = Math.floor(Date.now() / 1000) - 10;
    const sig = (await createSession(SECRET)).split('.')[1];
    assert.equal(await verifySession(`${past}.${sig}`, SECRET), false);
  });

  it('rejects missing and malformed tokens', async () => {
    for (const bad of [undefined, '', 'nonsense', '12345', '.', '.abc']) {
      assert.equal(await verifySession(bad, SECRET), false, `should reject ${JSON.stringify(bad)}`);
    }
  });
});

describe('getAdminSecret', () => {
  it('returns null when nothing is configured', () => {
    assert.equal(getAdminSecret(platformWith({})), null);
  });

  it('returns null when there is no platform at all', () => {
    assert.equal(getAdminSecret(undefined), null);
  });

  it('prefers ADMIN_SECRET over QUOTA_SECRET', () => {
    assert.equal(getAdminSecret(platformWith({ ADMIN_SECRET: 'a', QUOTA_SECRET: 'q' })), 'a');
  });

  it('falls back to QUOTA_SECRET', () => {
    assert.equal(getAdminSecret(platformWith({ QUOTA_SECRET: 'q' })), 'q');
  });
});

describe('isAuthed', () => {
  it('fails closed when no secret is configured', async () => {
    // Regression guard for C3: an unconfigured secret must deny everyone,
    // including a caller holding an otherwise-valid token.
    const token = await createSession(SECRET);
    assert.equal(await isAuthed(cookieJar(token), platformWith({})), false);
  });

  it('accepts a valid session cookie', async () => {
    const token = await createSession(SECRET);
    assert.equal(await isAuthed(cookieJar(token), platformWith({ ADMIN_SECRET: SECRET })), true);
  });

  it('rejects a request with no cookie', async () => {
    assert.equal(await isAuthed(cookieJar(undefined), platformWith({ ADMIN_SECRET: SECRET })), false);
  });

  it('invalidates existing sessions when the secret is rotated', async () => {
    const token = await createSession(SECRET);
    assert.equal(await isAuthed(cookieJar(token), platformWith({ ADMIN_SECRET: 'rotated' })), false);
  });
});
