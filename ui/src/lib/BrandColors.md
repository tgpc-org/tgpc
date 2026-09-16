# TGPC Brand Colors — Mandatory Guideline

Every screen, component, export, and new code must use ONLY these in-house colors.

## The Four Brand Colors

| Name       | Hex      | Usage                                        |
|------------|----------|----------------------------------------------|
| tgpc green | `#00cc66`| Primary actions, active states, sync, "TGPC" |
| tgpc red   | `#ef4444`| Destructive, inactive, "RPh", disclaimer     |
| tgpc grey  | `#9ca3af`| Secondary text, "Index", meta, placeholders |
| tgpc blue  | `#2563eb`| Links, RPC numbers, monospace values         |

## Supporting Neutrals (allowed)

| Token      | Hex       | Usage                          |
|------------|-----------|--------------------------------|
| text       | `#111827` | Primary text                   |
| muted      | `#6b7280` | Labels, secondary text         |
| border     | `#e5e7eb` | Borders, dividers              |
| row        | `#f4f4f5` | Table/row background           |
| bg         | `#ffffff` | Page background                |
| greenDark  | `#00b359` | Hover/dark variant of green    |
| surface    | `#f3f4f6` | Chips, hover fills             |
| surfaceAlt | `#f8f9fa` | Panel headers, hover fills     |
| inkSoft    | `#374151` | Body text on white             |
| borderSoft | `#d1d5db` | Subtle dividers                |
| surfaceHi  | `#f9fafb` | Hover backgrounds              |

Soft tints of brand red/green are allowed as alpha variants:
`rgba(239,68,68,α)` and `rgba(0,204,102,α)` (e.g. tinted button/badge
backgrounds and soft borders).

## Rules (MANDATORY)

1. Never invent new hex values. All colors come from the tables above.
2. Prefer the `TGPC` export in `colors.ts`: `import { TGPC } from '$lib/colors'`.
   - `TGPC.green`, `TGPC.red`, `TGPC.grey`, `TGPC.blue`
3. Do NOT use off-brand reds like `#dc2626`, off-brand greens like `#16a34a`,
   ad-hoc greys, or third-party palettes (Bootstrap amber, Tailwind green/purple
   accents). If you find one, replace it with the brand value.
4. Category colors (`CATEGORY_COLORS`) use brand-derived values (blue, green,
   text, red, greenDark, grey) for data visualization — still on-palette.
5. New files: reference these constants; no hardcoded hex in markup.
6. The palette is machine-enforced: `npm run check:colors`
   (`ui/scripts/check-colors.mjs`) fails CI/pre-commit on any off-palette
   literal. Add new approved values to both that script and this file.

## Day/Night Mode

The site ships a theme toggle (header, `$lib/theme.ts`). Implementation notes:

- Semantic tokens live in `ui/src/app.css` (`:root` light, `.dark` night).
  Dark values reuse the approved neutrals inverted — **no new hex was
  added for night mode**, so the color gate covers both modes unchanged.
- Night mapping: page `#111827`, surfaces/rows `#374151`, borders
  `#6b7280`/`#374151`, text `#ffffff`/`#f9fafb`/`#d1d5db`, faint `#9ca3af`.
  Brand accents are identical day and night.
- Components reference tokens as `var(--t-bg)` (inline styles) or
  `bg-[var(--t-bg)]` (Tailwind arbitrary values) — never raw neutrals.
- Preference persists in `localStorage` (`tgpc-theme`), defaulting to the OS
  scheme; a CSP-nonce'd guard in `app.html` applies it pre-paint (no flash).
- Exports (CSV/PDF/email) intentionally stay light — they are print artifacts.
