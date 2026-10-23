/**
 * Guards the SSR wiring of the universal loaders that fetch from our own
 * `/api/*` proxies.
 *
 * A relative URL in the **global** fetch throws inside the Workers runtime,
 * and the loaders swallow that error into an empty array — so using the
 * global fetch does not crash the page, it silently serves an empty one.
 * That is exactly what happened to `/notice` and `/dispatch` (SSR "No
 * notices" for every crawler and no-JS visitor while hydration filled the
 * page in), and neither svelte-check nor the browser-only e2e suite can see
 * the difference.
 *
 * Source-level, like the query-limit half of `searchLimits.test.ts`: the
 * regression is invisible at runtime on a dev machine (the global fetch
 * resolves relative URLs fine in Node and in the browser).
 */

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const LOADERS = [
  { path: '../routes/notice/+page.ts', call: 'fetchNotices' },
  { path: '../routes/dispatch/+page.ts', call: 'fetchDispatchFiles' }
];

for (const { path, call } of LOADERS) {
  test(`${call}: the loader passes SvelteKit's fetch, not the global one`, () => {
    const source = readFileSync(new URL(path, import.meta.url), 'utf8');
    assert.match(
      source,
      /\(\{\s*fetch\s*\}\)/,
      `${path} must destructure { fetch } from the load event`
    );
    assert.match(
      source,
      new RegExp(`${call}\\(\\s*fetch\\s*\\)`),
      `${path} must call ${call}(fetch)`
    );
  });
}
