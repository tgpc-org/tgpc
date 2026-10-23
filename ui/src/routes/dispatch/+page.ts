import type { PageLoad } from './$types';
import { fetchDispatchFiles } from '#lib/api.js';

export const load: PageLoad = async ({ fetch }) => {
  let files: { name: string; size?: number }[] = [];
  try {
    // Same reason as /notice: only the load `fetch` can resolve a relative
    // URL during SSR on Workers. The route's per-IP limit still applies, so
    // an SSR render cannot be used to bypass it.
    files = await fetchDispatchFiles(fetch);
  } catch {}
  return { files };
};
