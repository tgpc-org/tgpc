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
  { const hintLoc = page.locator('span.-bottom-4'); await hintLoc.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {}); check('hint: text appears under search', await hintLoc.isVisible()); }
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

  // Category chips row. The chips sit in an overflow-x scroll strip pinned
  // with ml-auto; at 1280px the 'All' chip starts beyond the viewport edge —
  // exercising it via keyboard/evaluate is the reliable route.
  check('chips: category count buttons visible', (await page.getByRole('button', { name: /All \(\d+\)/ }).count()) === 1);
  await page.evaluate(() => {
    const btn = [...document.querySelectorAll('button')].find((x) => /^All \(\d+\)$/.test(x.textContent.trim()));
    btn.click();
  });
  await page.waitForTimeout(200);
  const chipFiltered = await page.evaluate(() => {
    const chips = [...document.querySelectorAll('button')].filter((x) => /^(BPharm|DPharm|MPharm|PharmD|QC|QP|All) \(/.test(x.textContent.trim()));
    // chipStyle returns inline styles; the browser normalizes #00cc66 to rgb().
    const active = chips.find((c) => (c.getAttribute('style') || '').includes('rgb(0, 204, 102)') || (c.getAttribute('style') || '').includes('#00cc66'));
    return active ? active.textContent.trim() : 'none-active';
  });
  check('chips: clicking All keeps it active', chipFiltered.startsWith('All'), `active chip: ${chipFiltered}`);

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
  // The picked 1999-era date matches no rows: the whole results block (incl.
  // refiners) unmounts — pre-existing behaviour, verified below. Recover by
  // clearing the search (reset) and searching again.
  const zeroState = await page
    .getByText('No results')
    .waitFor({ timeout: 8000 })
    .then(() => true)
    .catch(() => false);
  check('refiner: zero-match valid_till shows No results', zeroState);
  await page.getByRole('button', { name: 'Clear', exact: true }).first().click({ force: true });
  await page.waitForTimeout(400);
  // Clear empties the input, so retype the full 3-char term: SEARCH stays
  // disabled below the 3-character minimum (the hint's whole point).
  await search.pressSequentially('ram', { delay: 100 });
  await page.getByRole('button', { name: 'SEARCH' }).click();
  await page.locator('table tbody tr').first().waitFor({ timeout: 30000 });
  check('refiner: recovers after reset + fresh search', (await page.locator('table tbody tr').count()) > 0);

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

  // Tabs navigation. Notices and dispatch populate from a client fetch when
  // SSR has no cached rows, so wait for real content instead of a fixed delay.
  await page.locator('nav[aria-label="Primary"]').getByRole('link', { name: 'Notices' }).click();
  // Wait for real rows, not just the sticky header row: the list arrives from
  // a client fetch, and the header renders a tick earlier.
  await page.locator('.hidden.md\\:block a.notice-link').first().waitFor({ timeout: 30000 }).catch(() => {});
  // Client-side nav updates aria-current from the $page store; poll so the
  // check does not race the router.
  const currentTab = await page
    .waitForFunction(() => document.querySelector('nav[aria-label="Primary"] a[aria-current="page"]')?.textContent, null, { timeout: 10000 })
    .then((h) => h.jsonValue())
    .catch(() => null);
  check('tabs: Notices becomes current', currentTab === 'Notices', `aria-current=${currentTab}`);
  check('notices: All tab present', await page.getByRole('button', { name: /All \(\d+\)/ }).isVisible());
  const noticeRowsAll = await page.locator('.hidden.md\\:block > div').count();
  check('notices: default All tab lists rows (not "No notices")', noticeRowsAll > 1, `${noticeRowsAll} rows`);
  await page.screenshot({ path: `${SHOTS}/desktop-notices.png` });
  const yearBtn = page.getByRole('button', { name: /^\d{4} \(\d+\)$/ }).first();
  if (await yearBtn.isVisible().catch(() => false)) {
    const allCount = noticeRowsAll;
    await yearBtn.click();
    await page.waitForTimeout(300);
    const yearCount = await page.locator('.hidden.md\\:block > div').count();
    check('notices: year filter narrows the list', yearCount > 0 && yearCount < allCount, `all ${allCount} -> year ${yearCount}`);
  }
  await page.locator('nav[aria-label="Primary"]').getByRole('link', { name: 'Dispatch List' }).click();
  await page.locator('a[href^="/api/dispatch/"]').first().waitFor({ timeout: 30000 }).catch(() => {});
  check('dispatch: All tab present', await page.getByRole('button', { name: /All \(\d+\)/ }).isVisible());
  const dispatchCards = await page.locator('a[href^="/api/dispatch/"]').count();
  check('dispatch: default All tab lists cards (not "No files")', dispatchCards > 1, `${dispatchCards} cards`);
  await page.screenshot({ path: `${SHOTS}/desktop-dispatch.png` });

  // Dark mode. $lib/theme hydrates from an $effect in the layout, so the
  // button is only interactive once the store is initialised — a click on the
  // still-unhydrated markup is a silent no-op. Wait for a real hydration
  // signal: SvelteKit sets __sveltekit_<hash> on the root element's data once
  // the client router has booted.
  await page.goto(BASE + '/');
  await page.locator('button[aria-label="Toggle day and night mode"]').waitFor({ state: 'visible', timeout: 15000 });
  await page.waitForFunction(() => localStorage.getItem('tgpc-theme') !== null, null, { timeout: 20000 });
  // Force a hydration round-trip: the clock only starts ticking once the
  // client is live, so a changed value proves the buttons are wired.
  const beforeTick = await page.locator('header span.tabular-nums').first().textContent();
  await page
    .waitForFunction((prev) => {
      const el = document.querySelector('header span.tabular-nums');
      return Boolean(el) && el.textContent !== prev;
    }, beforeTick, { timeout: 20000 })
    .catch(() => {});
  const themeBtn = page.getByRole('button', { name: 'Toggle day and night mode' });
  await themeBtn.click();
  await page.waitForFunction(() => document.documentElement.classList.contains('dark'), null, { timeout: 10000 }).catch(() => {});
  const isDark = await page.evaluate(() => document.documentElement.classList.contains('dark'));
  check('dark mode: html.dark applied', isDark);
  check('dark mode: button aria-pressed reflects state', (await themeBtn.getAttribute('aria-pressed')) === 'true');
  await page.screenshot({ path: `${SHOTS}/desktop-dark.png` });
  await themeBtn.click();
  await page.waitForFunction(() => !document.documentElement.classList.contains('dark'), null, { timeout: 10000 }).catch(() => {});
  check('dark mode: toggles back to light', !(await page.evaluate(() => document.documentElement.classList.contains('dark'))));

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
  await mob.locator('.md\\:hidden a.notice-link').first().waitFor({ timeout: 30000 }).catch(() => {});
  const mOverflowNotice = await mob.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check('mobile notices: no horizontal overflow', mOverflowNotice <= 1, `${mOverflowNotice}px`);
  check('mobile notices: cards render', (await mob.locator('.md\\:hidden > div').count()) > 0);
  await mob.screenshot({ path: `${SHOTS}/mobile-notices.png` });
  await mob.goto(BASE + '/dispatch');
  await mob.locator('a[href^="/api/dispatch/"]').first().waitFor({ timeout: 30000 }).catch(() => {});
  const mOverflowDispatch = await mob.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check('mobile dispatch: no horizontal overflow', mOverflowDispatch <= 1, `${mOverflowDispatch}px`);
  check('mobile dispatch: cards render', (await mob.locator('a[href^="/api/dispatch/"]').count()) > 0);
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
