import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';
import { skipIfGatedUnauthed } from './helpers.ts';

test.beforeEach(() => {
	skipIfGatedUnauthed();
});

// Mobile layout gate — runs in the `mobile` project (iPhone SE viewport).
// Guards the responsive behaviour: no horizontal page overflow, search stays
// usable full-width, refiners collapse, and the footer no longer overlays
// results. Fingerprints are structural, not pixel-based, so they survive
// copy/stat changes.

// On mobile the results render as cards (the desktop table isn't mounted at
// all). This locator targets the card list only.
function resultsLocator(page: Page) {
	// The empty state may append the query ("No results for “xyz”"), so match
	// by substring, not exact text.
	return page.locator('[data-testid="mobile-results"] a[href^="/rph/"]').first().or(page.getByText('No results'));
}

// The two record pages below carry some of the longest names in the registry
// (33 and 25 characters). They used to push the whole document ~60-108px wide
// on phones: the record header is a column with `items-start`, so the name
// column sized to max-content instead of wrapping. Keep at least one long name
// in this list — short names hide the regression.
const OVERFLOW_PATHS = ['/', '/notice', '/dispatch', '/rph/TG074218', '/rph/TG072355'];

test('no horizontal overflow on key pages', async ({ page }) => {
	for (const path of OVERFLOW_PATHS) {
		await page.goto(path);
		const overflow = await page.evaluate(() => {
			const el = document.documentElement;
			return el.scrollWidth - el.clientWidth;
		});
		expect(overflow, `horizontal overflow on ${path}: ${overflow}px`).toBeLessThanOrEqual(1);
	}
});

test('axe: no serious/critical violations at phone width', async ({ page }) => {
	// This project is the only one that exercises phone widths, and the record
	// page is not in a11y.spec.ts — without this, mobile-only violations (e.g.
	// the header stats strip being unscrollable by keyboard) ship unnoticed.
	for (const path of OVERFLOW_PATHS) {
		await page.goto(path);
		const results = await new AxeBuilder({ page }).disableRules(['color-contrast']).analyze();
		const blocking = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
		expect(blocking, `${path}: ${JSON.stringify(blocking.map((v) => v.id))}`).toEqual([]);
	}
});

test('search input keeps a usable width when results are shown', async ({ page }) => {
	await page.goto('/');
	const search = page.locator('#tgpc-search');
	await expect(search).toBeVisible();
	search.fill('ram');
	await page.getByRole('button', { name: 'SEARCH' }).click();
	await expect(resultsLocator(page)).toBeVisible({ timeout: 30_000 });
	const box = await search.boundingBox();
	const viewport = page.viewportSize();
	expect(box).not.toBeNull();
	expect(viewport).not.toBeNull();
	// Previously capped at max-w-[25vw]; must now use most of the row.
	expect(box!.width).toBeGreaterThan(viewport!.width * 0.6);
});

test('refiners collapse behind the toggle and expand on tap', async ({ page }) => {
	await page.goto('/');
	await page.locator('#tgpc-search').fill('ram');
	await page.getByRole('button', { name: 'SEARCH' }).click();
	await expect(resultsLocator(page)).toBeVisible({ timeout: 30_000 });
	if (await page.getByText('No results', { exact: true }).isVisible()) test.skip(true, 'no results to refine');

	const toggle = page.getByRole('button', { name: /Refine/ });
	await expect(toggle).toBeVisible();
	await expect(toggle).toHaveAttribute('aria-expanded', 'false');
	await expect(page.locator('#refiner-fields')).toBeHidden();
	await toggle.click();
	await expect(toggle).toHaveAttribute('aria-expanded', 'true');
	await expect(page.locator('#refiner-fields')).toBeVisible();
});

test('profile drawer is a full-width bottom sheet', async ({ page }) => {
	await page.goto('/');
	await page.locator('#tgpc-search').fill('ram');
	await page.getByRole('button', { name: 'SEARCH' }).click();
	const link = page.locator('[data-testid="mobile-results"] a[href^="/rph/"]').first().or(page.getByText('No results'));
	await expect(link).toBeVisible({ timeout: 30_000 });
	if (await page.getByText('No results', { exact: true }).isVisible()) test.skip(true, 'no results to open');
	const profileLink = page.locator('[data-testid="mobile-results"] a[aria-label^="View profile"]').first();
	// Retry: hydration may not have attached the click handler yet.
	await expect(async () => {
		await profileLink.click();
		await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 5_000 });
	}).toPass({ timeout: 30_000 });
	const dlg = page.locator('[role="dialog"]');
	const vp = page.viewportSize()!;
	// Final resting geometry: pinned to the bottom edge, full width, capped
	// at 88dvh — the native-style bottom sheet. (A parked fly-in would fail
	// the bottom-edge assertion, since the sheet starts 1500px below.)
	await expect(async () => {
		const box = await dlg.boundingBox();
		expect(box).not.toBeNull();
		expect(Math.abs(box!.y + box!.height - vp.height)).toBeLessThanOrEqual(2);
		expect(box!.x).toBeLessThanOrEqual(1);
		expect(box!.width).toBeGreaterThanOrEqual(vp.width - 1);
	}).toPass({ timeout: 10_000 });
});

test('footer does not overlay the results list', async ({ page }) => {
	await page.goto('/');
	await page.locator('#tgpc-search').fill('ram');
	await page.getByRole('button', { name: 'SEARCH' }).click();
	await expect(resultsLocator(page)).toBeVisible({ timeout: 30_000 });
	const footer = page.locator('footer');
	await footer.scrollIntoViewIfNeeded();
	// Mobile: footer is in-flow, so it sits below the content, not on top of it.
	await expect(footer).toBeVisible();
	const overlap = await page.evaluate(() => {
		const f = document.querySelector('footer');
		const rows = document.querySelectorAll('[data-testid="mobile-results"] > div');
		if (!f || rows.length === 0) return 0;
		const fr = f.getBoundingClientRect();
		let worst = 0;
		for (const r of rows) {
			const rr = r.getBoundingClientRect();
			if (rr.bottom > fr.top && rr.top < fr.bottom) {
				worst = Math.max(worst, Math.min(rr.bottom, fr.bottom) - Math.max(rr.top, fr.top));
			}
		}
		return worst;
	});
	expect(overlap, `results hidden behind footer: ${overlap}px`).toBeLessThanOrEqual(1);
});
