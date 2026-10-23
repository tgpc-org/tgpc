import type { PageLoad } from './$types';
import type { Notice } from '#lib/types.js';
import { fetchNotices } from '#lib/api.js';

export const load: PageLoad = async ({ fetch }) => {
  let notices: Notice[] = [];
  try {
    // SvelteKit's load `fetch`, not the global one: a relative URL in the
    // global fetch throws in the Workers runtime, which made SSR render
    // "No notices" for every request (the client quietly filled it in).
    notices = await fetchNotices(fetch);
  } catch {}
  return { notices };
};
