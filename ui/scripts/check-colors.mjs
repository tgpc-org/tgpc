/**
 * Brand-color gate (AGENTS.md / BrandColors.md, CODE_REVIEW.md M8).
 *
 * Fails when any hex or rgb()/rgba() literal in ui/src is outside the approved
 * TGPC palette. Brand red/green are additionally allowed as rgb triplets with
 * any alpha (e.g. `rgba(0,204,102,0.08)` for soft tints).
 *
 * Run: npm run check:colors
 */

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

const ROOT = new URL('../src', import.meta.url).pathname;

// Approved hex values (lowercase, 6-digit form; 3-digit shorthand normalized).
const ALLOWED_HEX = new Set([
  // brand
  '00cc66', 'ef4444', '9ca3af', '2563eb',
  // neutrals
  '111827', '6b7280', 'e5e7eb', 'f4f4f5', 'ffffff', '00b359',
  // neutrals approved 2026-08-24 (standard Tailwind grays in consistent use)
  'f3f4f6', 'f8f9fa', '374151', 'd1d5db', 'f9fafb',
  // night page background approved 2026-10-06 (dark-mode --t-bg)
  '2a2a2a'
]);

// Allowed rgb() triplets: brand red and brand green, any alpha.
const ALLOWED_RGB = new Set(['239,68,68', '0,204,102']);

// AGENTS.md exception: CATEGORY_COLORS are brand-derived data-viz hues.
const EXEMPT_FILES = new Set(['colors.ts']);

const HEX_RE = /(?<!&)#[0-9a-fA-F]{3,8}\b/g; // lookbehind skips HTML entities like &#10003;
const RGB_RE = /\brgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,[^)]*)?\)/g;

function normalizeHex(raw) {
  let h = raw.slice(1).toLowerCase();
  if (h.length === 3) h = h.split('').map((c) => c + c).join('');
  if (h.length === 8) h = h.slice(0, 6); // ignore alpha suffix on 8-digit hex
  return h;
}

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) yield* walk(p);
    else if (/\.(svelte|ts|js|css|html)$/.test(name)) yield p;
  }
}

const offenders = [];

for (const file of walk(ROOT)) {
  if (EXEMPT_FILES.has(file.split('/').pop())) continue;
  const lines = readFileSync(file, 'utf8').split('\n');
  lines.forEach((line, i) => {
    for (const m of line.matchAll(HEX_RE)) {
      if (!ALLOWED_HEX.has(normalizeHex(m[0]))) {
        offenders.push(`${file}:${i + 1}: ${m[0]}`);
      }
    }
    for (const m of line.matchAll(RGB_RE)) {
      const triplet = `${parseInt(m[1])},${parseInt(m[2])},${parseInt(m[3])}`;
      if (!ALLOWED_RGB.has(triplet)) {
        offenders.push(`${file}:${i + 1}: rgb(${triplet},...)`);
      }
    }
  });
}

if (offenders.length) {
  console.error(`✖ ${offenders.length} off-palette color(s) found:\n`);
  for (const o of offenders) console.error(`  ${o}`);
  console.error('\nAllowed values live in scripts/check-colors.mjs and BrandColors.md.');
  process.exit(1);
}

console.log('✓ all colors on-palette');
