import { defineConfig } from '@playwright/test';

// Read-only E2E against the live site (default prod). Override with
// PROD_URL to target a preview deployment. No secrets needed — every
// assertion is a public GET or a public UI flow.
const baseURL = process.env.PROD_URL ?? 'https://tgpc.pages.dev';

export default defineConfig({
	testDir: './e2e',
	timeout: 60_000,
	retries: process.env.CI ? 2 : 0,
	reporter: [['list'], ['html', { open: 'never' }]],
	use: {
		baseURL,
		trace: 'retain-on-failure'
	}
});
