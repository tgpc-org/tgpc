// Regenerates e2e/contrast-baseline.json from the live site.
// Review the git diff before committing — a shrinking baseline is good news,
// a growing one must be a deliberate design decision.
import { chromium } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { writeFileSync } from 'node:fs';
import { join } from 'node:path';

const baseURL = process.env.PROD_URL ?? 'https://tgpc.pages.dev';
const browser = await chromium.launch();
const page = await (await browser.newContext()).newPage();
const baseline = {};
for (const path of ['/', '/notice', '/dispatch']) {
	await page.goto(baseURL + path);
	const results = await new AxeBuilder({ page }).withRules(['color-contrast']).analyze();
	const fps = new Set();
	for (const v of results.violations) {
		for (const n of v.nodes) {
			for (const t of n.target.flatMap(String)) fps.add(`${v.id}::${t}`);
		}
	}
	baseline[path] = [...fps].sort();
	console.log(`${path}: ${baseline[path].length} fingerprints`);
}
writeFileSync(join(import.meta.dirname, 'contrast-baseline.json'), JSON.stringify(baseline, null, 2) + '\n');
await browser.close();
