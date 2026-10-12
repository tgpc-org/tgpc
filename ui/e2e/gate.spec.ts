import { expect, request, test } from '@playwright/test';
import { isLocalTarget, targetBaseURL } from './helpers.ts';

// Public-portal gate contract. Always uses a fresh context (no session
// cookies) so it holds whether or not E2E_ADMIN_SECRET is set. Skipped
// against local `vite dev`, where the gate is deliberately open
// (hooks.server.ts: no platform secret there).
test.describe('site gate (no session)', () => {
	test.skip(isLocalTarget(), 'gate only active outside vite dev');

	let fresh: Awaited<ReturnType<typeof request.newContext>>;
	test.beforeAll(async () => {
		fresh = await request.newContext({ baseURL: targetBaseURL() });
	});
	test.afterAll(async () => {
		await fresh.dispose();
	});

	test('public pages render without a session', async () => {
		for (const path of ['/', '/notice', '/dispatch']) {
			expect((await fresh.get(path)).ok()).toBeTruthy();
		}
	});

	test('public data APIs answer without a session', async () => {
		// Deterministic shapes (no upstream call): a missing query is a 400,
		// which still proves the request was not gated with a 403.
		for (const path of ['/api/search', '/api/record', '/api/refine']) {
			const res = await fresh.get(path);
			expect(res.status()).toBe(400);
		}
		// Live shapes (need upstream Supabase): reachable means anything
		// other than the gate's 403.
		for (const path of ['/api/stats', '/api/health']) {
			const res = await fresh.get(path);
			expect(res.status()).not.toBe(403);
		}
	});

	test('admin surface stays gated', async () => {
		for (const path of ['/api/usage', '/api/admin/contacts?reg=TG000001']) {
			const res = await fresh.get(path);
			expect(res.status()).toBe(403);
		}
	});

	test('login flow stays reachable', async () => {
		expect((await fresh.get('/admin')).ok()).toBeTruthy();
	});
});
