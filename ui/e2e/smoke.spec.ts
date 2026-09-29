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

test('input typed before hydration survives and enables SEARCH', async ({ page }) => {
	// Regression guard: a template or dependency change once wiped any input
	// typed during boot (hydration re-rendered the field from empty state),
	// silently killing every test that fills before the app is ready — and
	// every real user who beats hydration to the keyboard.
	//
	// The race is load-dependent (on an idle machine hydration usually wins),
	// so it is forced here: JS modules get 400ms of artificial latency and
	// goto resolves at 'commit' (HTML streamed, modules still in flight),
	// making fill() land pre-hydration deterministically. Hydration must
	// preserve the user's typing; the SEARCH button existing afterwards
	// proves hydrated state holds the query.
	await page.route(/\.js(\?|$)/, async (route) => {
		await new Promise((r) => setTimeout(r, 400));
		await route.continue();
	});
	await page.goto('/', { waitUntil: 'commit' });
	await page.locator('#tgpc-search').fill('ram');
	await expect(page.locator('#tgpc-search')).toHaveValue('ram', { timeout: 30_000 });
	await expect(page.getByRole('button', { name: 'SEARCH' })).toBeVisible({ timeout: 30_000 });
});

test('homepage empty state: starter chips render and run a search', async ({ page }) => {
	await page.goto('/');
	// First visit must not be a blank page: the empty state offers a way in.
	await expect(page.getByRole('heading', { name: 'Find a registered pharmacist' })).toBeVisible();
	const chip = page.getByRole('button', { name: 'reddy', exact: true });
	await expect(chip).toBeVisible();
	await expect(page.getByRole('link', { name: 'Browse the latest council notices' })).toBeVisible();
	// A starter chip runs the real search flow: results replace the state.
	// The empty state is in the SSR HTML, so the first click can land before
	// hydration attaches the handler — retry the click until the app reacts.
	const reacted = page.locator('table').or(page.getByText('No results'));
	await expect(async () => {
		await chip.click();
		await expect(reacted).toBeVisible({ timeout: 5_000 });
	}).toPass({ timeout: 30_000 });
	await expect(page.locator('table')).toBeVisible();
	expect(new URL(page.url()).searchParams.get('q')).toBe('reddy');
	await expect(page.getByRole('heading', { name: 'Find a registered pharmacist' })).toBeHidden();
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

	// Freshness is deliberately NOT asserted here. The sync pipeline is
	// workflow_dispatch-only (there is no cron in .github/workflows), so the age
	// of the data is an operational condition, not a property of the code being
	// pushed — asserting it made every ui/ change fail whenever the operator had
	// not run the scraper for two days. The signal is still produced: the
	// endpoint reports last_sync.status 'stale' past 48h and overall 'degraded',
	// and .github/workflows/health.yml polls exactly that every 6 hours and
	// fails the run when the data is stale.
	expect(['ok', 'stale']).toContain(body.checks?.last_sync?.status);
	expect(typeof body.checks?.last_sync?.hours_ago).toBe('number');
});
