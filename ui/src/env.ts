import { defineEnvVars } from '@sveltejs/kit/env';

// Empty-string fallbacks preserve the old $env/dynamic-missing semantics:
// every consumer treats '' as missing and fails closed.
export const variables = defineEnvVars({
	PUBLIC_R2_PHOTO_BASE: { public: true, static: true },
	PUBLIC_SUPABASE_URL: { public: true, schema: (input) => input ?? '' },
	SUPABASE_SECRET_KEY: { schema: (input) => input ?? '' }
});
