import { test } from '@playwright/test';

// Shared gate helpers for the login-first site.
//
// Production (and preview) deny every data-bearing route without the admin
// session; local `vite dev` stays open. Flow specs run authenticated via the
// storageState written by auth.setup.ts when E2E_ADMIN_SECRET is set, open
// against a local dev server, and skip otherwise (a gated target without a
// secret can only assert the gate contract — see gate.spec.ts).

export function targetBaseURL(): string {
	return process.env.PROD_URL ?? 'https://tgpc.pages.dev';
}

export function isLocalTarget(): boolean {
	const u = targetBaseURL();
	return u.includes('localhost') || u.includes('127.0.0.1');
}

export function hasSecret(): boolean {
	return !!process.env.E2E_ADMIN_SECRET;
}

/** Skip flow specs that need a session when they cannot get one. */
export function skipIfGatedUnauthed(): void {
	test.skip(
		!isLocalTarget() && !hasSecret(),
		'gated target needs E2E_ADMIN_SECRET (repo secret) — see ARCHITECTURE.md'
	);
}
