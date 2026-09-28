// CSV/PDF export for search results. The page owns state; this module only
// turns an ExportContext into a downloaded file. All header/filename input is
// sanitized: raw query/refiner text must not be able to split `#`-comment rows
// (CR/LF) or produce hostile filenames, and CSV cells starting with formula
// characters get a `'` prefix so spreadsheet apps treat them as text.

import { CATEGORY_COLORS } from './colors.ts';
import type { PharmacistRecord } from './types';

export type ExportRow = Pick<
  PharmacistRecord,
  'registration_number' | 'name' | 'father_name' | 'gender' | 'category' | 'validity_date' | 'status'
>;

export interface ExportContext {
  /** Sorted rows — what actually gets exported. */
  rows: ExportRow[];
  filteredCount: number;
  totalCount: number;
  /** True when category chips or refiners narrow the result set. */
  isFiltered: boolean;
  query: string;
  /** Prebuilt "Key: value" strings describing active filters. */
  filters: string[];
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const FORMULA_CHARS = ['=', '+', '-', '@', '\t', '\r'];

function fmtDate(d: Date) {
  return `${DAYS[d.getDay()]}, ${String(d.getDate()).padStart(2, '0')} ${MONTHS[d.getMonth()]} ${d.getFullYear()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

function fileDateStr(d: Date) {
  return `${String(d.getDate()).padStart(2, '0')}${String(d.getMonth() + 1).padStart(2, '0')}${d.getFullYear()}`;
}

function cleanHeader(s: string): string {
  return s.replace(/[\r\n]+/g, ' ');
}

export function safeFilename(s: string): string {
  const flat = s.replace(/[\r\n]+/g, ' ').replace(/[/\\?%*:|"< >]/g, '').trim();
  return (flat || 'all').slice(0, 60);
}

function csvCell(value: unknown): string {
  const str = String(value ?? '');
  const escaped = str.replace(/"/g, '""');
  const prefix = FORMULA_CHARS.includes(str.trimStart().charAt(0)) ? "'" : '';
  return `"${prefix}${escaped}"`;
}

/** Pure CSV string builder (no DOM) — exportCSV downloads what this returns. */
export function buildCsv(ctx: ExportContext, now = new Date()): string {
  const kw = cleanHeader(ctx.query.trim() || '(all)');
  const countLine = `# Results: ${ctx.filteredCount.toLocaleString()} of ${ctx.totalCount.toLocaleString()}${ctx.isFiltered ? ' (filtered)' : ''}`;
  const filterLine = ctx.filters.length ? `# Filters: ${ctx.filters.join(' | ')}` : '# Filters: none';
  const header = ['RPC NUMBER', 'NAME', 'FATHER NAME', 'GENDER', 'CATEGORY', 'VALID TILL', 'STATUS'];
  const rows = ctx.rows.map((r) => [
    csvCell(r.registration_number),
    csvCell(r.name),
    csvCell(r.father_name),
    csvCell(r.gender),
    csvCell(r.category),
    csvCell(r.validity_date),
    csvCell(r.status)
  ]);
  const combined = `${countLine} | ${filterLine.replace('# Filters:', 'Filters:')}`;
  return [`# TGPC RPh Index - Search: ${kw} - ${fmtDate(now)}`, combined, header.join(','), ...rows.map((r) => r.join(','))].join('\n');
}

export function exportCSV(ctx: ExportContext): void {
  if (ctx.rows.length === 0) return;
  const csv = buildCsv(ctx);
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = `TGPC-RPH-SEARCH-${safeFilename(ctx.query.trim())}-${fileDateStr(new Date())}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

export async function exportPDF(ctx: ExportContext): Promise<void> {
  if (ctx.rows.length === 0) return;
  // jspdf + autotable (~650KB) load on demand, not in the homepage bundle.
  const [{ jsPDF }, { default: autoTable }] = await Promise.all([
    import('jspdf'),
    import('jspdf-autotable')
  ]);
  const doc = new jsPDF({ format: 'a4', unit: 'mm' });
  const now = new Date();
  const kw = cleanHeader(ctx.query.trim() || '(all)');
  const title = `TGPC RPh Index - Search: ${kw} - ${fmtDate(now)}`;
  const countLine = `Results: ${ctx.filteredCount.toLocaleString()} of ${ctx.totalCount.toLocaleString()}${ctx.isFiltered ? ' (filtered)' : ''}`;
  const filterLine = ctx.filters.length ? `Filters: ${ctx.filters.join(' | ')}` : 'Filters: none';
  const body = ctx.rows.map((r) => [r.registration_number, r.name, r.father_name || '—', r.gender || '—', r.category, r.validity_date || '—', r.status || '—']);

  autoTable(doc, {
    startY: 18,
    head: [['RPC NUMBER', 'NAME', 'FATHER NAME', 'GENDER', 'CATEGORY', 'VALID TILL', 'STATUS']],
    body,
    theme: 'striped',
    headStyles: { fillColor: [0, 204, 102], textColor: [255, 255, 255], fontStyle: 'bold', fontSize: 9 },
    bodyStyles: { fontSize: 8, cellPadding: 2 },
    alternateRowStyles: { fillColor: [247, 247, 247] },
    margin: { top: 16, left: 10, right: 10, bottom: 12 },
    tableWidth: 'auto',
    didParseCell: (data) => {
      if (data.section === 'body' && data.column.index === 4) {
        const cat = data.cell.raw as string;
        const rgb = CATEGORY_RGB[cat];
        if (rgb) data.cell.styles.textColor = rgb;
      }
    },
    didDrawPage: (_data) => {
      doc.setFontSize(11);
      doc.setTextColor(0, 204, 102);
      doc.text(title, 10, 10);
      doc.setFontSize(7);
      doc.setTextColor(100, 100, 100);
      doc.text(`${countLine} | ${filterLine}`, 10, 14);
    }
  });
  const total = doc.getNumberOfPages();
  for (let i = 1; i <= total; i++) {
    doc.setPage(i);
    doc.setFontSize(8);
    doc.setTextColor(150, 150, 150);
    const ph = doc.internal.pageSize.height;
    doc.text('TGPC RPh Index - Open-Source Pharmacist Data', 10, ph - 10);
    doc.text('tgpc.pages.dev', doc.internal.pageSize.width / 2, ph - 10, { align: 'center' });
    doc.text(`Page ${i} / ${total}`, doc.internal.pageSize.width - 10, ph - 10, { align: 'right' });
  }
  doc.save(`TGPC-RPH-SEARCH-${safeFilename(ctx.query.trim())}-${fileDateStr(now)}.pdf`);
}

/** Brand-derived category colours (from the single palette export) as
 *  PDF-ready RGB triples — no second hardcoded colour table. */
const CATEGORY_RGB: Record<string, [number, number, number]> = Object.fromEntries(
  Object.entries(CATEGORY_COLORS).map(([k, hex]) => [
    k,
    [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)] as [number, number, number]
  ])
);
