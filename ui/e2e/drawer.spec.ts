import { expect, test, type Page } from '@playwright/test';

// Profile drawer — desktop side sheet. (The mobile bottom-sheet variant
// lives in mobile.spec.ts, which is the only spec the `mobile` project
// runs.) These assertions pin the dialog's final resting geometry so a
// broken fly-in (element parked at its animation start offset) cannot
// ship: the drawer must dock right at full height once visible.

async function openProfile(page: Page) {
	await page.goto('/');
	await page.locator('#tgpc-search').fill('ram');
	await page.getByRole('button', { name: 'SEARCH' }).click();
	const link = page.locator('a[aria-label^="View profile"]').first();
	// Retry the click: the row renders from client search state, and the
	// handler attaches at hydration — a fast fetch can beat it.
	await expect(async () => {
		await link.click();
		await expect(page.locator('[role="dialog"]')).toBeVisible({ timeout: 5_000 });
	}).toPass({ timeout: 30_000 });
}

test('drawer docks right at full height', async ({ page }) => {
	await openProfile(page);
	const dlg = page.locator('[role="dialog"]');
	const vw = page.viewportSize()!.width;
	await expect(async () => {
		const box = await dlg.boundingBox();
		expect(box).not.toBeNull();
		expect(Math.abs(box!.x + box!.width - vw)).toBeLessThanOrEqual(2);
		expect(box!.y).toBeLessThanOrEqual(1);
	}).toPass({ timeout: 10_000 });
});
