import type { PageLoad } from './$types';

// The blocked BH-P ID list is a versioned static asset. Bump the filename
// when PCI publishes a new annexure, and update this loader.
const DATA_URL = '/data/blocked-bhp-2026-11-19.json';

export const load: PageLoad = async ({ fetch }) => {
  try {
    const resp = await fetch(DATA_URL);
    if (!resp.ok) throw new Error(`blocked data ${resp.status}`);
    const ids = (await resp.json()) as string[];
    return { ids, error: null as string | null };
  } catch {
    return { ids: [] as string[], error: 'Could not load the blocked ID list.' };
  }
};
