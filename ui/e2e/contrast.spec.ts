import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// Contrast regression gate. The absolute color-contrast debt (brand-palette
// text on light surfaces) is tracked, not zero-gated — see a11y.spec.ts.
// This test fails when a color-contrast violation appears that is NOT in the
// checked-in baseline, i.e. new debt. Fingerprint is rule + CSS target only
// (never text content, which includes live stats/clock values).
//
// To update the baseline after an intentional design change, run:
//   npm run test:e2e:update-baseline
// and review the diff before committing.
const BASELINE: Record<string, string[]> = JSON.parse(
	readFileSync(join(import.meta.dirname, 'contrast-baseline.json'), 'utf8')
);

const PATHS = ['/', '/notice', '/dispatch'];

for (const path of PATHS) {
	test(`contrast: ${path} introduces no new violations`, async ({ page }) => {
		await page.goto(path);
		const results = await new AxeBuilder({ page }).withRules(['color-contrast']).analyze();
		const current = new Set<string>();
		for (const v of results.violations) {
			for (const n of v.nodes) {
				for (const t of n.target.flatMap(String)) current.add(`${v.id}::${t}`);
			}
		}
		const known = new Set(BASELINE[path] ?? []);
		const added = [...current].filter((f) => !known.has(f));
		const resolved = [...known].filter((f) => !current.has(f));
		for (const f of resolved) console.log(`[contrast:${path}] resolved since baseline: ${f}`);
		expect(added, `New color-contrast violations on ${path}:\n${added.join('\n')}`).toEqual([]);
	});
}
