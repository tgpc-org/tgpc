/**
 * Env-aware wrapper around the pure limiter in `rateLimit.ts`.
 *
 * Route handlers import from here, never from `rateLimit.ts` directly: this
 * module resolves the shared-counter credentials through `envVal`, which
 * statically imports `$app/env/*` and therefore cannot be loaded by
 * `node:test`. Keeping the split means the limiter logic stays unit-testable
 * while production still reaches the real Supabase credentials.
 *
 * Note on bindings: `event.platform` is undefined in production with
 * adapter-cloudflare v8 (see appEnv.ts and audit F13), so a Workers KV or R2
 * binding is not readable from here. The Supabase service key is, which is why
 * the shared counter is a Postgres RPC.
 */
import { envVal } from './appEnv.js';
import {
	rateLimited as rateLimitedCore,
	rateLimitedSearch as rateLimitedSearchCore,
	rateLimitedStrict as rateLimitedStrictCore,
	type SharedCounter
} from './rateLimit.js';

/** Resolve the shared counter, or null when Supabase is unconfigured. */
export function sharedCounter(
	platform: App.Platform | undefined
): SharedCounter | null {
	const url = envVal(platform, 'SUPABASE_URL');
	const secret = envVal(platform, 'SUPABASE_SECRET_KEY');
	return url && secret ? { url, secret } : null;
}

export function rateLimited(
	key: string,
	platform?: App.Platform
): Promise<boolean> {
	return rateLimitedCore(key, { shared: sharedCounter(platform) });
}

export function rateLimitedSearch(
	key: string,
	platform?: App.Platform
): Promise<boolean> {
	return rateLimitedSearchCore(key, { shared: sharedCounter(platform) });
}

/**
 * Read-endpoint limiter whose budget the shared counter enforces on every
 * call (see `rateLimitedStrict` in rateLimit.ts for why the local window is
 * not enough on this deployment).
 */
export function rateLimitedStrict(
	key: string,
	platform: App.Platform | undefined,
	max: number
): Promise<boolean> {
	return rateLimitedStrictCore(key, max, { shared: sharedCounter(platform) });
}
