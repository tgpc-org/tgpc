import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

// Zero tolerance for serious/critical impacts, EXCEPT color-contrast.
//
// Why the exception: every color-contrast hit on this site is brand-palette
// text (tgpc green/red/grey on light surfaces) — an intentional design
// tradeoff, not a code bug. Fixing it means either muting the category
// color-coding or extending the brand palette with darker text tones, which
// is a human design decision. Contrast regressions are still blocked by
// contrast.spec.ts (baseline fingerprints); this gate covers everything else.
for (const path of ['/', '/notice', '/dispatch']) {
	test(`axe: ${path} has no serious/critical violations (excl. contrast)`, async ({ page }) => {
		await page.goto(path);
		const results = await new AxeBuilder({ page }).disableRules(['color-contrast']).analyze();
		const blocking = results.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
		const rest = results.violations.filter((v) => v.impact !== 'serious' && v.impact !== 'critical');
		for (const v of rest) {
			console.log(`[axe:${path}] ${v.impact ?? 'unknown'}: ${v.id} — ${v.help} (${v.nodes.length} nodes)`);
		}
		expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
	});
}
