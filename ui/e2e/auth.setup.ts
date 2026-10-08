import { expect, test as setup } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';

// Playwright setup project (see playwright.config.ts): logs into the
// session-gated site once and persists the cookie for every flow spec.
// Without E2E_ADMIN_SECRET it writes an empty state — flow specs then skip
// on gated targets (helpers.ts) while gate.spec.ts still asserts the
// unauthenticated contract.
setup('authenticate', async ({ request }) => {
	const out = join(import.meta.dirname, '.auth.json');
	const secret = process.env.E2E_ADMIN_SECRET;
	if (!secret) {
		writeFileSync(out, '{}');
		return;
	}
	const res = await request.post('/api/admin', { data: { secret } });
	expect(res.ok(), 'E2E login failed — check E2E_ADMIN_SECRET').toBeTruthy();
	await request.storageState({ path: out });
});
