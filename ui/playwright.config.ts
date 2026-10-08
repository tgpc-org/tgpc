import { defineConfig } from '@playwright/test';

// Read-only E2E against the live site (default prod). Override with
// PROD_URL to target a preview deployment. The site is login-first: flow
// specs run authenticated via the storageState written by auth.setup.ts
// when E2E_ADMIN_SECRET is set (CI provides it as a repo secret), open
// against local `vite dev`, and skip on gated targets without a secret.
// gate.spec.ts asserts the unauthenticated contract with a fresh context.
const baseURL = process.env.PROD_URL ?? 'https://tgpc.pages.dev';
const storageState = process.env.E2E_ADMIN_SECRET ? './e2e/.auth.json' : undefined;

export default defineConfig({
	testDir: './e2e',
	timeout: 60_000,
	retries: process.env.CI ? 2 : 0,
	reporter: [['list'], ['html', { open: 'never' }]],
	projects: [
		{ name: 'setup', testMatch: /auth\.setup\.ts/ },
		{
			name: 'desktop',
			dependencies: ['setup'],
			testIgnore: [/mobile\.spec\.ts/, /auth\.setup\.ts/],
			use: { baseURL, storageState, trace: 'retain-on-failure' }
		},
		{
			// Smallest realistic phone (iPhone SE class) — the layout we had
			// never exercised: header stats, refiners, cards, footer. Uses
			// Chromium mobile emulation so CI only needs the chromium browser
			// (no webkit install).
			name: 'mobile',
			dependencies: ['setup'],
			testMatch: /mobile\.spec\.ts/,
			use: {
				baseURL,
				storageState,
				trace: 'retain-on-failure',
				browserName: 'chromium',
				viewport: { width: 320, height: 568 },
				isMobile: true,
				hasTouch: true,
				deviceScaleFactor: 2,
				userAgent:
					'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
			}
		}
	]
});
