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
	projects: [
		{ name: 'desktop', testIgnore: /mobile\.spec\.ts/, use: { baseURL, trace: 'retain-on-failure' } },
		{
			// Smallest realistic phone (iPhone SE class) — the layout we had
			// never exercised: header stats, refiners, cards, footer. Uses
			// Chromium mobile emulation so CI only needs the chromium browser
			// (no webkit install).
			name: 'mobile',
			testMatch: /mobile\.spec\.ts/,
			use: {
				baseURL,
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
