import type { LayoutLoad } from './$types';
import type { Stats } from '$lib/types';
import { supabase } from '$lib/supabase';

export const load: LayoutLoad = async () => {
  let stats: Stats | null = null;
  let lastSync = '';

  // Parallel — both must resolve before first paint, so don't await sequentially.
  const [statsRes, syncRes] = await Promise.all([
    supabase.rpc('get_rph_stats').then(
      (r) => r,
      () => ({ data: null, error: true })
    ),
    supabase.from('metadata').select('value').eq('key', 'last_sync').single().then(
      (r) => r,
      () => ({ data: null, error: true })
    )
  ]);

  try {
    const { data, error } = statsRes as { data: unknown; error: unknown };
    if (!error && data && typeof data === 'object') {
      const d = data as { total: number; active: number; inactive: number; categories: Record<string, number> };
      stats = {
        total: d.total ?? 0,
        active: d.active ?? 0,
        inactive: d.inactive ?? 0,
        BPharm: d.categories?.BPharm ?? 0,
        DPharm: d.categories?.DPharm ?? 0,
        MPharm: d.categories?.MPharm ?? 0,
        PharmD: d.categories?.PharmD ?? 0,
        QC: d.categories?.QC ?? 0,
        QP: d.categories?.QP ?? 0
      };
    }
  } catch {}

  try {
    const { data, error } = syncRes as { data: { value?: string } | null; error: unknown };
    if (!error && data?.value) {
      const d = new Date(data.value);
      lastSync = d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', weekday: 'short', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).toUpperCase().replace(/,/g, '');
    }
  } catch {}

  return { stats, lastSync };
};
