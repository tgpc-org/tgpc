/**
 * Server-only environment resolution (SvelteKit 3 path).
 *
 * adapter-cloudflare v8 no longer passes `platform` to `server.respond`
 * (its worker template only forwards getClientAddress), so `event.platform`
 * is undefined in production and every `platform.env` read misses. The
 * supported Kit 3 path is `$app/env`, which the adapter wires to the worker
 * bindings — declared in `src/env.ts`.
 *
 * Resolution order per key: platform.env first (works in local preview via
 * platformProxy and if a future adapter restores it), then $app/env.
 * First non-empty value wins; null when unconfigured (callers fail closed).
 *
 * Native bindings are not strings, so they do not go through `envVal`:
 * `dispatchBucket()` performs the same lookup for the `DISPATCH` R2 bucket,
 * which is declared private and non-static in `src/env.ts` precisely so the
 * object is exported by reference instead of serialised.
 *
 * NOTE: this module statically imports `$app/env/*`, so it must never be
 * imported by `node:test` unit tests (no Vite resolver there) — same rule as
 * `health/+server.ts`. `$lib/server/auth.ts` carries its own dynamic-import
 * fallback for this reason.
 */

import * as priv from '$app/env/private';
import * as pub from '$app/env/public';

const pubMap = pub as Record<string, string | undefined>;
const privMap = priv as Record<string, string | undefined>;

/** First non-empty value for any of `names`: platform.env, then $app/env. */
export function envVal(
	platform: App.Platform | undefined,
	...names: string[]
): string | null {
	for (const name of names) {
		const v = platform?.env?.[name];
		if (v) return v;
	}
	for (const name of names) {
		const v = privMap[name] ?? pubMap[name];
		if (v) return v;
	}
	return null;
}

/**
 * The `DISPATCH` R2 bucket, or null when the binding is not attached.
 *
 * Bindings are NOT readable through `event.platform` in production (the
 * adapter passes only `getClientAddress` to `server.respond`), so the binding
 * is declared in `src/env.ts` and reached through `$app/env/private`. The
 * platform read stays first so local `wrangler pages dev` keeps working if a
 * future adapter restores it.
 */
export function dispatchBucket(
	platform: App.Platform | undefined
): App.DispatchBucket | null {
	const fromPlatform = platform?.env?.DISPATCH;
	if (fromPlatform && typeof fromPlatform.list === 'function') return fromPlatform;

	const fromEnv = privMap.DISPATCH as unknown as App.DispatchBucket | undefined;
	if (fromEnv && typeof fromEnv.list === 'function') return fromEnv;

	return null;
}
