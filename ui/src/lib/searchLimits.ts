/**
 * Row ceiling for a single public search (CODE_REVIEW.md H5).
 *
 * Deliberately its own module with no SvelteKit or Supabase imports: `api.ts`
 * pulls in `$env/static/public`, so it cannot be imported by the `node:test`
 * suite — this file can.
 *
 * Why cap at all: the previous code asked the `search_pharmacists` RPC for
 * `lim: 100000` and the page rendered every returned row with no pagination.
 * One broad query therefore dragged most of the ~87k-row registry over the
 * wire and into the DOM — freezing low-end phones, and giving anyone a cheap
 * way to run up Supabase egress on a public endpoint.
 *
 * 200 is far more than anyone reads while typing a query, and the results
 * header asks for a narrower query once the ceiling is reached.
 */
export const MAX_SEARCH_RESULTS = 200;

/**
 * True when a result set came back at the ceiling — the search was cut short,
 * so matching rows may exist beyond what is displayed.
 */
export function isTruncated(rowCount: number): boolean {
  return rowCount >= MAX_SEARCH_RESULTS;
}
