// Shared date formatting helpers.
//
// The registry stores validity dates verbatim from the source in
// "DD-Mon-YYYY" form ("07-Mar-2028"), while every picker/export path uses ISO
// "YYYY-MM-DD". These helpers are the single conversion point between the two;
// before this module each consumer kept its own copy of the month table, which
// is how the same string format came to be formatted in four separate files.

/** Three-letter month abbreviations, indexable by `Date#getMonth()`. */
export const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'] as const;

const MONTH_INDEX: Record<string, number> = {
  Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5,
  Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11
};

/** "DD-Mon-YYYY" — the registry's verbatim validity_date format. */
const DDMONYYYY_RE = /^(\d{2})-([A-Za-z]{3})-(\d{4})$/;

/** ISO "YYYY-MM-DD" → "DD-Mon-YYYY" (what the DB stores), or null if unusable. */
export function formatDDMonYYYY(iso: string): string | null {
  const d = new Date(iso + 'T00:00:00');
  if (isNaN(d.getTime())) return null;
  return `${String(d.getDate()).padStart(2, '0')}-${MONTHS[d.getMonth()]}-${d.getFullYear()}`;
}

/** "DD-Mon-YYYY" → { day, monthIndex, year }, or null if it doesn't parse. */
export function parseDDMonYYYY(v: string): { day: number; monthIndex: number; year: number } | null {
  const m = v.trim().match(DDMONYYYY_RE);
  if (!m) return null;
  const monthIndex = MONTH_INDEX[m[2][0].toUpperCase() + m[2].slice(1).toLowerCase()];
  if (monthIndex === undefined) return null;
  return { day: Number(m[1]), monthIndex, year: Number(m[3]) };
}
