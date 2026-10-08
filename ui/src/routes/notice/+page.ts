import type { PageLoad } from './$types';
import type { Notice } from '#lib/types.js';
import { fetchNotices } from '#lib/api.js';

export const load: PageLoad = async () => {
  let notices: Notice[] = [];
  try {
    notices = await fetchNotices();
  } catch {}
  return { notices };
};
