<script lang="ts">
  import type { Stats } from '$lib/types';
  import { CATEGORIES, CATEGORY_COLORS, CATEGORY_KEYS } from '$lib/colors';

  let { stats = null as Stats | null, lastSync = '' as string }: { stats?: Stats | null; lastSync?: string } = $props();

  function val(key: keyof Stats): string {
    return stats ? stats[key].toLocaleString() : '0';
  }

  let sortedCategories = $derived.by(() => {
    if (!stats) return CATEGORIES;
    const s = stats;
    return [...CATEGORIES].sort((a, b) => {
      const ka = CATEGORY_KEYS[CATEGORIES.indexOf(a)] as keyof Stats;
      const kb = CATEGORY_KEYS[CATEGORIES.indexOf(b)] as keyof Stats;
      return (s[kb] ?? 0) - (s[ka] ?? 0);
    });
  });
</script>

<div
  style="background:var(--t-surface-2);border:1px solid var(--t-border);border-radius:var(--radius-sm);padding:6px 10px 4px 10px;display:flex;flex-direction:column;gap:0;min-width:0;width:100%;max-width:100%;box-shadow:var(--shadow-sm)"
>
  <div
    class="tgpc-stats flex-nowrap overflow-x-auto sm:flex-wrap"
    style="display:flex;gap:10px 12px;align-items:center;justify-content:center;padding-bottom:4px;scrollbar-width:thin;scrollbar-color:var(--t-border) transparent;-webkit-overflow-scrolling:touch"
  >
    <div style="border-right:1px solid var(--t-border);padding-right:12px">
      <div style="display:flex;flex-direction:column;gap:4px;text-align:center">
        <div style="font-size:0.8rem;font-weight:500;letter-spacing:0.5px;color:#9ca3af">
          TOTAL <span style="color:#ef4444">RPh</span>
        </div>
        <div class="tabular" style="font-size:1.25rem;font-weight:700;color:var(--t-ink);line-height:1">{val('total')}</div>
      </div>
    </div>
    {#each sortedCategories as cat, i (cat)}
      <div style="border-right:{i < 5 ? '1px solid var(--t-border)' : 'none'};padding-right:{i < 5 ? '12px' : '0'}">
        <div style="display:flex;flex-direction:column;gap:4px;text-align:center">
          <div style="font-size:0.8rem;font-weight:500;letter-spacing:0.5px;color:{CATEGORY_COLORS[cat] === '#111827' ? 'var(--t-ink)' : CATEGORY_COLORS[cat]}">{cat}</div>
          <div class="tabular" style="font-size:1.25rem;font-weight:700;color:var(--t-ink);line-height:1">
            {val(CATEGORY_KEYS[CATEGORIES.indexOf(cat)] as keyof Stats)}
          </div>
        </div>
      </div>
    {/each}
  </div>
  <div
    style="font-size:0.5rem;color:#9ca3af;font-weight:500;letter-spacing:0.3px;text-transform:uppercase;margin-top:4px;padding-top:4px;border-top:1px solid var(--t-border);display:flex;align-items:center;gap:6px;flex-wrap:wrap"
  >
    <span style="display:inline-flex;align-items:center;gap:3px;background:rgba(0,204,102,0.1);padding:1px 6px 1px 4px;border-radius:10px">
      <span style="display:inline-flex;align-items:center;justify-content:center;width:10px;height:10px;background:#00cc66;border-radius:50%;color:white;font-size:6px;font-weight:bold">&#10003;</span>
      <span style="color:#00cc66;font-size:0.45rem;font-weight:600;text-transform:uppercase;letter-spacing:0.3px">Synced</span>
    </span>
    <span style="color:var(--t-link);font-weight:600">{lastSync || '—'}</span>
    <span style="opacity:0.4">|</span>
    <span>Active: <span style="color:#00cc66;font-weight:600">{val('active')}</span></span>
    <span style="opacity:0.4">|</span>
    <span>Inactive: <span style="color:#ef4444;font-weight:600">{val('inactive')}</span></span>
    <span style="opacity:0.4">|</span>
    <span style="color:#9ca3af">Unofficial data — Not for legal use</span>
  </div>
</div>
