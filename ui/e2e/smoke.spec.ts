import { expect, test } from '@playwright/test';

test('homepage shell: title, landmarks, search box', async ({ page }) => {
	await page.goto('/');
	await expect(page).toHaveTitle('TGPC RPh Index');
	await expect(page.locator('h1')).toHaveCount(1);
	await expect(page.getByRole('navigation', { name: 'Primary' })).toBeVisible();
	await expect(page.getByRole('link', { name: 'Skip to main content' })).toBeAttached();
	const search = page.locator('#tgpc-search');
	await expect(search).toBeVisible();
	await expect(page.locator('label[for="tgpc-search"]')).toBeAttached();
});

test('search flow resolves to results or empty state', async ({ page }) => {
	await page.goto('/');
	await page.locator('#tgpc-search').fill('ram');
	await page.getByRole('button', { name: 'SEARCH' }).click();
	const table = page.locator('table');
	const empty = page.getByText('No results', { exact: true });
	await expect(table.or(empty)).toBeVisible({ timeout: 30_000 });
});

test('notice page renders index', async ({ page }) => {
	await page.goto('/notice');
	await expect(page).toHaveTitle(/Notices/);
	await expect(page.locator('h1')).toHaveCount(1);
	await expect(page.locator('#notice-search')).toBeVisible();
});

test('dispatch page renders index', async ({ page }) => {
	await page.goto('/dispatch');
	await expect(page).toHaveTitle(/Dispatch List/);
	await expect(page.locator('h1')).toHaveCount(1);
	await expect(page.locator('#dispatch-search')).toBeVisible();
});

test('unknown RPC degrades to 404, not 500', async ({ request }) => {
	const res = await request.get('/rph/TEST123');
	expect(res.status()).toBe(404);
});

test('usage API stays locked', async ({ request }) => {
	const res = await request.get('/api/usage');
	expect(res.status()).toBe(403);
});

test('health API contract', async ({ request }) => {
	const res = await request.get('/api/health');
	expect(res.ok()).toBeTruthy();
	const body = await res.json();
	expect(['ok', 'degraded']).toContain(body.status);
	expect(body.checks?.supabase?.status).toBe('ok');
	expect(body.checks?.last_sync?.hours_ago).toBeLessThan(48);
});
