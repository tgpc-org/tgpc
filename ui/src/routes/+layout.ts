import type { LayoutLoad } from './$types';
import type { Stats } from '$lib/types';

export const load: LayoutLoad = async ({ fetch }) => {
  let stats: Stats | null = null;
  let lastSync = '';

  // Layer 2: stats come from the session-gated server proxy (service key
  // server-side). Relative fetch works in SSR and in the browser; the
  // session cookie travels with it in both cases.
  try {
    const r = await fetch('/api/stats');
    if (r.ok) {
      const d = await r.json();
      if (d?.stats && typeof d.stats === 'object') {
        stats = {
          total: d.stats.total ?? 0,
          active: d.stats.active ?? 0,
          inactive: d.stats.inactive ?? 0,
          BPharm: d.stats.BPharm ?? 0,
          DPharm: d.stats.DPharm ?? 0,
          MPharm: d.stats.MPharm ?? 0,
          PharmD: d.stats.PharmD ?? 0,
          QC: d.stats.QC ?? 0,
          QP: d.stats.QP ?? 0
        };
      }
      if (typeof d?.lastSync === 'string') lastSync = d.lastSync;
    }
  } catch {}

  return { stats, lastSync };
};
