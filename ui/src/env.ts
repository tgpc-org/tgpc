import { defineEnvVars } from '@sveltejs/kit/env';

// Every server-read variable must be declared here or `$app/env/*` imports
// fail the build. Empty-string fallbacks preserve the old
// $env/dynamic-missing semantics: every consumer treats '' as missing and
// fails closed.
const str = { schema: (input: string | undefined) => input ?? '' };

export const variables = defineEnvVars({
	PUBLIC_R2_PHOTO_BASE: { public: true, static: true },
	PUBLIC_SUPABASE_URL: { public: true, ...str },
	SUPABASE_URL: str,
	SUPABASE_SECRET_KEY: str,
	SUPABASE_PAT: str,
	ADMIN_SECRET: str,
	QUOTA_SECRET: str,
	ADMIN_LINK_PHARMACIST_URL: str,
	ADMIN_LINK_EMAIL_VERIFY_URL: str,
	CLOUDFLARE_ACCOUNT_ID: str,
	CLOUDFLARE_API_TOKEN: str,
	R2_ACCESS_KEY_ID: str,
	R2_SECRET_ACCESS_KEY: str,
	TGPC_R2_DG_BUCKET: str,
	GITHUB_TOKEN: str,
	GITHUB_PAT: str
});
