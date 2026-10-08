import { expect, test, type Page } from '@playwright/test';
import { skipIfGatedUnauthed } from './helpers.ts';

test.beforeEach(() => {
	skipIfGatedUnauthed();
});

// Guards the URL-backed search state (lib/searchUrl.ts): typed searches land
// in the URL, shared links rehydrate without typing, reload keeps state,
// chips add their param, and reset empties the query string.

// Desktop renders the table; the mobile project renders the card list.
function resultsList(page: Page) {
	return page.locator('table').or(page.locator('[data-testid="mobile-results"]'));
}

async function search(page: Page, q: string) {
	await page.goto('/');
	await page.locator('#tgpc-search').fill(q);
	await page.getByRole('button', { name: 'SEARCH' }).click();
	await expect(resultsList(page)).toBeVisible({ timeout: 30_000 });
}

test('typing a search writes ?q into the URL', async ({ page }) => {
	await search(page, 'ram');
	await expect(page).toHaveURL(/\?q=ram$/);
});

test('a shared ?q= link rehydrates results without typing', async ({ page }) => {
	await page.goto('/?q=ram');
	await expect(resultsList(page)).toBeVisible({ timeout: 30_000 });
	await expect(page).toHaveURL(/\?q=ram$/);
});

test('reload keeps the search state', async ({ page }) => {
	await search(page, 'ram');
	await page.reload();
	await expect(resultsList(page)).toBeVisible({ timeout: 30_000 });
	await expect(page.locator('#tgpc-search')).toHaveValue('ram');
});

test('category chip adds its param to the URL', async ({ page }) => {
	await search(page, 'ram');
	await page.getByRole('button', { name: /^BPharm \(/ }).click();
	await expect(page).toHaveURL(/\?q=ram&cat=BPharm/);
});

test('clear button empties the URL', async ({ page }) => {
	await search(page, 'ram');
	await page.getByRole('button', { name: 'Clear' }).click();
	await expect(page).not.toHaveURL(/\?q=/);
	await expect(page.locator('#tgpc-search')).toHaveValue('');
});
