import { expect, request, test } from '@playwright/test';
import { isLocalTarget, targetBaseURL } from './helpers.ts';

// Unauthenticated gate contract for the login-first site. Always uses a
// fresh context (no session cookies) so it holds whether or not
// E2E_ADMIN_SECRET is set. Skipped against local `vite dev`, where the gate
// is deliberately open (hooks.server.ts: no platform secret there).
test.describe('site gate (no session)', () => {
	test.skip(isLocalTarget(), 'gate only active outside vite dev');

	let fresh: Awaited<ReturnType<typeof request.newContext>>;
	test.beforeAll(async () => {
		fresh = await request.newContext({ baseURL: targetBaseURL() });
	});
	test.afterAll(async () => {
		await fresh.dispose();
	});

	test('pages redirect to /admin', async () => {
		for (const path of ['/', '/notice', '/dispatch']) {
			const res = await fresh.get(path, { maxRedirects: 0 });
			expect(res.status()).toBe(302);
			expect(res.headers()['location']).toContain('/admin');
		}
	});

	test('data APIs answer 403', async () => {
		for (const path of ['/api/usage', '/api/health', '/api/admin/contacts?reg=TG000001']) {
			const res = await fresh.get(path);
			expect(res.status()).toBe(403);
		}
	});

	test('login flow stays reachable', async () => {
		expect((await fresh.get('/admin')).ok()).toBeTruthy();
	});
});
