import type { PageLoad } from './$types';
import { fetchDispatchFiles } from '#lib/api.js';

export const load: PageLoad = async () => {
  let files: { name: string; size?: number }[] = [];
  try {
    files = await fetchDispatchFiles();
  } catch {}
  return { files };
};
