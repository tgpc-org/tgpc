import { expect, test, type Page } from '@playwright/test';

// Mobile layout gate — runs in the `mobile` project (iPhone SE viewport).
// Guards the responsive behaviour: no horizontal page overflow, search stays
// usable full-width, refiners collapse, and the footer no longer overlays
// results. Fingerprints are structural, not pixel-based, so they survive
// copy/stat changes.

// On mobile the results render as cards (the desktop <table> is hidden).
// This locator targets the card list only; the desktop table is hidden here.
function resultsLocator(page: Page) {
	return page.locator('.md\\:hidden a[href^="/rph/"]').first().or(page.getByText('No results', { exact: true }));
}

test('no horizontal overflow on key pages', async ({ page }) => {
	for (const path of ['/', '/notice', '/dispatch']) {
		await page.goto(path);
		const overflow = await page.evaluate(() => {
			const el = document.documentElement;
			return el.scrollWidth - el.clientWidth;
		});
		expect(overflow, `horizontal overflow on ${path}: ${overflow}px`).toBeLessThanOrEqual(1);
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
		const rows = document.querySelectorAll('table tbody tr, .md\\:hidden > div');
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
