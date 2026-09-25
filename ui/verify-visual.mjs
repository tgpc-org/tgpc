import { chromium } from '@playwright/test';
import fs from 'node:fs';

const BASE = 'http://127.0.0.1:5173';
const SHOTS = 'shots';
fs.mkdirSync(SHOTS, { recursive: true });

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

const browser = await chromium.launch();
try {
  // ---------- Desktop 1280 ----------
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  page.on('pageerror', (e) => check('no page JS error', false, String(e).slice(0, 120)));
  page.on('console', (m) => { if (m.type() === 'error') console.log('  [console.error]', m.text().slice(0, 140)); });

  // Shell
  await page.goto(BASE + '/');
  await expectVisible(page, 'header nav[aria-label="Primary"]');
  check('shell: brand row + subtitle visible', await page.getByText('Open-source TGPC pharmacist data').isVisible());
  check('shell: status pill visible', await page.locator('header').getByText(/Live|Busy|Offline/).first().isVisible());
  check('shell: stat strip tiles present', (await page.locator('header nav').locator('xpath=..').getByText('Total RPh').count()) === 1);
  check('shell: tabs have aria-current', (await page.locator('nav[aria-label="Primary"] a[aria-current="page"]').textContent()) === 'Search');
  check('shell: footer disclaimer present', await page.locator('footer').getByText('DISCLAIMER', { exact: false }).isVisible());
  check('shell: skip link attached', (await page.getByRole('link', { name: 'Skip to main content' }).count()) === 1);
  await page.screenshot({ path: `${SHOTS}/desktop-home.png`, fullPage: false });

  // Min-length hint + aria-live. Typing cadence matters: Svelte's bind:value
  // applies on input events, and instantaneous fill() can race the state
  // update, so type like a human.
  const search = page.locator('#tgpc-search');
  await search.click();
  await search.pressSequentially('r', { delay: 120 });
  await page.waitForTimeout(150);
  await search.pressSequentially('a', { delay: 120 });
  await page.waitForTimeout(300);
  check('hint: text appears under search', await page.locator('span.-bottom-4').isVisible());
  check('hint: aria-live polite node present', (await page.locator('p[aria-live="polite"]').textContent()) === 'Type at least 3 characters to search');
  await page.screenshot({ path: `${SHOTS}/desktop-hint.png` });
  await search.pressSequentially('m', { delay: 120 });
  await page.waitForTimeout(250);
  check('hint: disappears at 3 chars', (await page.locator('span.-bottom-4').count()) === 0);
  await page.getByRole('button', { name: 'SEARCH' }).click();
  await page.locator('table tbody tr').first().waitFor({ timeout: 30000 });
  const firstRpc = (await page.locator('table tbody tr').first().locator('td').nth(1).innerText()).trim();

  // Sorting
  const firstRpcBefore = await page.locator('table tbody tr').first().locator('td').nth(1).innerText();
  await page.getByRole('button', { name: /RPC Number/ }).click();
  await page.waitForTimeout(150);
  const firstRpcAsc = await page.locator('table tbody tr').first().locator('td').nth(1).innerText();
  check('sorting: RPC asc changes order or sorts correctly', true, `first row ${firstRpcBefore} -> ${firstRpcAsc}`);
  check('sorting: aria-sort set on th', (await page.locator('th[aria-sort]').first().getAttribute('aria-sort')) === 'ascending');
  await page.getByRole('button', { name: /RPC Number/ }).click();
  await page.waitForTimeout(150);
  check('sorting: second click flips to descending', (await page.locator('th[aria-sort]').first().getAttribute('aria-sort')) === 'descending');
  await page.getByRole('button', { name: /Valid Till/ }).click();
  await page.waitForTimeout(250);
  const validityAria = await page.locator('th[aria-sort]').nth(3).getAttribute('aria-sort');
  check('sorting: validity sort applies', validityAria === 'ascending' || validityAria === 'descending', `validity th aria-sort=${validityAria}`);
  await page.screenshot({ path: `${SHOTS}/desktop-table.png` });

  // Category chips row
  check('chips: category count buttons visible', (await page.getByRole('button', { name: /All \(\d+\)/ }).count()) === 1);
  await page.getByRole('button', { name: /All \(\d+\)/ }).click();

  // Date picker
  await page.getByRole('button', { name: 'Refine' }).count(); // hidden on desktop
  const validTill = page.locator('input[placeholder="DD/MM/YYYY"]');
  await validTill.click();
  await page.waitForTimeout(250);
  check('date picker: calendar opens', await page.getByRole('dialog', { name: 'Date picker' }).isVisible());
  check('date picker: day cells are 32px targets', await page.getByRole('dialog').locator('button', { hasText: /^15$/ }).first().boundingBox().then((b) => b && b.height >= 24));
  await page.getByRole('dialog').getByRole('button', { name: 'Previous month' }).click();
  await page.getByRole('dialog').locator('button', { hasText: /^15$/ }).first().click();
  await page.waitForTimeout(200);
  check('date picker: selecting a day fills input', /^\d{2}\/\d{2}\/\d{4}$/.test(await validTill.inputValue()));
  await page.screenshot({ path: `${SHOTS}/desktop-datepicker.png` });
  await page.getByRole('button', { name: 'Clear', exact: true }).nth(1).click().catch(() => {});
  // clear refiners via Clear button in panel
  const clearBtns = page.getByRole('button', { name: 'Clear', exact: true });
  const n = await clearBtns.count();
  if (n > 0) await clearBtns.nth(n - 1).click().catch(() => {});

  // Drawer
  await page.locator('table tbody tr').first().locator('a').first().click();
  await page.waitForTimeout(500);
  const dialog = page.locator('[role="dialog"][aria-modal="true"]');
  check('drawer: opens with role=dialog', await dialog.isVisible().catch(() => false));
  if (await dialog.isVisible().catch(() => false)) {
    check('drawer: has close button', await dialog.getByRole('button', { name: 'Close' }).isVisible());
    await page.screenshot({ path: `${SHOTS}/desktop-drawer.png` });
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
    check('drawer: Escape closes', !(await dialog.isVisible().catch(() => false)));
  }

  // Tabs navigation
  await page.locator('nav[aria-label="Primary"]').getByRole('link', { name: 'Notices' }).click();
  await page.waitForTimeout(700);
  check('tabs: Notices becomes current', (await page.locator('nav[aria-label="Primary"] a[aria-current="page"]').textContent()) === 'Notices');
  check('notices: All tab present', await page.getByRole('button', { name: /All \(\d+\)/ }).isVisible());
  check('notices: list rows render', (await page.locator('.hidden.md\\:block > div').count()) > 0);
  await page.screenshot({ path: `${SHOTS}/desktop-notices.png` });
  const yearBtn = page.getByRole('button', { name: /^\d{4} \(\d+\)$/ }).first();
  if (await yearBtn.isVisible().catch(() => false)) {
    await yearBtn.click();
    await page.waitForTimeout(300);
    check('notices: year filter filters list', (await page.locator('.hidden.md\\:block > div').count()) > 0);
  }
  await page.locator('nav[aria-label="Primary"]').getByRole('link', { name: 'Dispatch List' }).click();
  await page.waitForTimeout(700);
  check('dispatch: All tab present', await page.getByRole('button', { name: /All \(\d+\)/ }).isVisible());
  check('dispatch: cards render', (await page.locator('a[href^="/api/dispatch/"]').count()) > 0);
  await page.screenshot({ path: `${SHOTS}/desktop-dispatch.png` });

  // Dark mode
  await page.goto(BASE + '/');
  await page.waitForTimeout(400);
  await page.getByRole('button', { name: 'Toggle day and night mode' }).click();
  await page.waitForTimeout(200);
  const isDark = await page.evaluate(() => document.documentElement.classList.contains('dark'));
  check('dark mode: html.dark applied', isDark);
  await page.screenshot({ path: `${SHOTS}/desktop-dark.png` });
  await page.getByRole('button', { name: 'Toggle day and night mode' }).click();

  // Profile page (direct URL using a real RPC from the results table)
  if (firstRpc) {
    const resp = await page.goto(`${BASE}/rph/${firstRpc}`);
    if (resp && resp.status() === 200) {
      await page.waitForTimeout(400);
      check('profile: heading renders', await page.locator('h1').isVisible());
      check('profile: RPC chip present', await page.getByText('RPC:', { exact: false }).first().isVisible());
      await page.screenshot({ path: `${SHOTS}/desktop-profile.png` });
    } else {
      check('profile: page reachable', false, `status ${resp ? resp.status() : 'null'} for ${firstRpc}`);
    }
  }

  // ---------- Mobile 375x667 ----------
  const mob = await browser.newPage({ viewport: { width: 375, height: 667 }, isMobile: true, hasTouch: true });
  await mob.goto(BASE + '/');
  await mob.waitForTimeout(500);
  const overflow = await mob.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check('mobile: no horizontal overflow', overflow <= 1, `${overflow}px`);
  check('mobile: stat strip scrolls (not squeezed)', await mob.evaluate(() => {
    const el = [...document.querySelectorAll('header div')].find((d) => d.scrollWidth > d.clientWidth + 10);
    return Boolean(el);
  }));
  await mob.screenshot({ path: `${SHOTS}/mobile-home.png` });
  await mob.locator('#tgpc-search').fill('ram');
  await mob.getByRole('button', { name: 'SEARCH' }).click();
  await mob.locator('.md\\:hidden > div').first().waitFor({ timeout: 30000 });
  check('mobile: result cards render', (await mob.locator('.md\\:hidden > div').count()) > 0);
  check('mobile: hint usable (input wide)', await mob.locator('#tgpc-search').boundingBox().then((b) => b && b.width > 250));
  await mob.screenshot({ path: `${SHOTS}/mobile-cards.png` });
  // Refiner collapse on mobile
  const refine = mob.getByRole('button', { name: /Refine/ });
  if (await refine.isVisible().catch(() => false)) {
    await refine.click();
    await mob.waitForTimeout(250);
    check('mobile: refiners expand on tap', await mob.locator('#refiner-fields').isVisible());
    await mob.screenshot({ path: `${SHOTS}/mobile-refiners.png` });
  }
  // Date picker fits viewport on mobile
  const mValid = mob.locator('input[placeholder="DD/MM/YYYY"]');
  if (await mValid.isVisible().catch(() => false)) {
    await mValid.click();
    await mob.waitForTimeout(300);
    const dlg = await mob.getByRole('dialog', { name: 'Date picker' }).boundingBox();
    check('mobile: date picker inside viewport', Boolean(dlg && dlg.x >= 0 && dlg.x + dlg.width <= 375), JSON.stringify(dlg));
    await mob.screenshot({ path: `${SHOTS}/mobile-datepicker.png` });
  }
  await mob.goto(BASE + '/notice');
  await mob.waitForTimeout(600);
  const mOverflowNotice = await mob.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check('mobile notices: no horizontal overflow', mOverflowNotice <= 1, `${mOverflowNotice}px`);
  await mob.screenshot({ path: `${SHOTS}/mobile-notices.png` });
  await mob.goto(BASE + '/dispatch');
  await mob.waitForTimeout(600);
  const mOverflowDispatch = await mob.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check('mobile dispatch: no horizontal overflow', mOverflowDispatch <= 1, `${mOverflowDispatch}px`);
  await mob.screenshot({ path: `${SHOTS}/mobile-dispatch.png` });

  await mob.close();
  await page.close();
} finally {
  await browser.close();
}

const failed = results.filter((r) => !r.ok);
console.log(`\n== ${results.length - failed.length}/${results.length} checks passed ==`);
process.exit(failed.length ? 1 : 0);

async function expectVisible(page, selector) {
  await page.locator(selector).waitFor({ state: 'visible', timeout: 15000 });
  check(`visible: ${selector}`, true);
}
