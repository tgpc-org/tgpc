<script lang="ts">
  import type { PharmacistRecord } from '$lib/types';
  import { fitToViewport } from '$lib/fitToViewport';
  import { CATEGORY_COLORS } from '$lib/colors';
  import { PUBLIC_R2_PHOTO_BASE } from '$env/static/public';

  let {
    rows,
    sortKey,
    sortDir,
    onsort,
    onopen
  }: {
    rows: PharmacistRecord[];
    sortKey: string;
    sortDir: 1 | -1;
    onsort: (_key: 'registration_number' | 'name' | 'category' | 'validity_date' | 'status') => void;
    onopen: (_reg: string) => void;
  } = $props();

  function photoUrl(r: PharmacistRecord): string {
    return r.photo_url || `${PUBLIC_R2_PHOTO_BASE}/${r.registration_number}.webp`;
  }

  // Sortable column header button: the colour lives on the button so the
  // label stays AA without depending on inherited <th> styling.
  // py-1 lifts the sort targets from 17px to 25px (the WCAG 2.5.8 minimum is
  // 24px); the matching -my-1 keeps the header row exactly as tall as before,
  // since the padding stays inside the <th>'s own py-2.
  const TH_BTN = 'inline-flex items-center gap-1 py-1 -my-1 cursor-pointer border-none bg-transparent uppercase tracking-wider text-[0.7rem] font-semibold transition-colors';
  const TH_HEAD = 'font-inherit text-left py-2 border-b-2 border-[var(--t-border)] uppercase tracking-wider text-[0.7rem] font-semibold text-[var(--t-muted)]';

  function ariaSort(key: 'registration_number' | 'name' | 'category' | 'validity_date' | 'status'): 'ascending' | 'descending' | 'none' {
    if (sortKey !== key) return 'none';
    return sortDir === 1 ? 'ascending' : 'descending';
  }

  function sortGlyph(key: 'registration_number' | 'name' | 'category' | 'validity_date' | 'status'): string {
    if (sortKey !== key) return '↕';
    return sortDir === 1 ? '↑' : '↓';
  }

  /** Category colour as a dot: the hues themselves fail AA as small text. */
  function categoryDot(cat: string): string {
    const c = CATEGORY_COLORS[cat as keyof typeof CATEGORY_COLORS];
    if (!c || c === '#111827') return 'var(--t-ink)';
    return c;
  }

  function statusTint(status: string): string {
    return status === 'Active' ? 'rgba(0,204,102,0.14)' : 'rgba(239,68,68,0.12)';
  }

  function statusDot(status: string): string {
    return status === 'Active' ? '#00cc66' : '#ef4444';
  }
</script>

{#snippet statusBadge(status: string)}
  <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wider" style="background:{statusTint(status)};color:var(--t-ink)">
    <span class="h-1.5 w-1.5 rounded-full shrink-0" style="background:{statusDot(status)}"></span>{status}
  </span>
{/snippet}

<div use:fitToViewport class="overflow-y-auto overflow-x-auto">
  <table class="w-full" style="table-layout:auto">
    <thead class="sticky top-0 bg-[var(--t-bg)] z-10">
      <tr>
        <th class="font-inherit text-left py-2 border-b-2 border-[var(--t-border)] w-[52px]"><span class="sr-only">Photo</span></th>
        <th aria-sort={ariaSort('registration_number')} class={TH_HEAD}>
          <button type="button" class={TH_BTN} style="color:inherit" onclick={() => onsort('registration_number')}>RPC Number <span aria-hidden="true">{sortGlyph('registration_number')}</span></button>
        </th>
        <th aria-sort={ariaSort('name')} class={TH_HEAD}>
          <button type="button" class={TH_BTN} style="color:inherit" onclick={() => onsort('name')}>Name <span aria-hidden="true">{sortGlyph('name')}</span></button>
        </th>
        <th class="{TH_HEAD} hidden lg:table-cell">Father Name</th>
        <th class="{TH_HEAD} hidden xl:table-cell">Gender</th>
        <th aria-sort={ariaSort('category')} class={TH_HEAD}>
          <button type="button" class={TH_BTN} style="color:inherit" onclick={() => onsort('category')}>Category <span aria-hidden="true">{sortGlyph('category')}</span></button>
        </th>
        <th aria-sort={ariaSort('validity_date')} class="{TH_HEAD} hidden lg:table-cell">
          <button type="button" class={TH_BTN} style="color:inherit" onclick={() => onsort('validity_date')}>Valid Till <span aria-hidden="true">{sortGlyph('validity_date')}</span></button>
        </th>
        <th aria-sort={ariaSort('status')} class="font-inherit text-right py-2 border-b-2 border-[var(--t-border)] pr-6 uppercase tracking-wider text-[0.7rem] font-semibold text-[var(--t-muted)]">
          <button type="button" class={TH_BTN} style="color:inherit" onclick={() => onsort('status')}>Status <span aria-hidden="true">{sortGlyph('status')}</span></button>
        </th>
      </tr>
    </thead>
    <tbody>
      {#each rows as r (r.registration_number)}
        <tr class="text-[0.875rem] text-[var(--t-ink-soft)] border-b border-[var(--t-surface)] transition-colors hover:bg-[var(--t-surface-3)]" style="content-visibility:auto;contain-intrinsic-size:56px">
          <td class="py-1.5 align-top">
            <img src={photoUrl(r)} alt="" loading="lazy" decoding="async" width="36" height="44" class="w-9 h-11 rounded object-cover bg-[var(--t-surface)]" />
          </td>
          <td class="py-2.5 align-top text-[var(--t-link)]" style="font-weight:600">
            <!-- min-h-6 mirrors MobileCards: the bare link was 17px tall, under
                 the 24px WCAG 2.5.8 target minimum. -->
            <a href="/rph/{r.registration_number}" onclick={(e) => { e.preventDefault(); onopen(r.registration_number); }} class="inline-flex items-center min-h-6 hover:underline no-underline cursor-pointer tabular-nums" aria-label="View profile for {r.registration_number}">
              {r.registration_number}
            </a>
          </td>
          <td class="py-2.5 align-top" style="color:var(--t-ink)">{r.name}</td>
          <td class="py-2.5 align-top hidden lg:table-cell">{r.father_name || '—'}</td>
          <td class="py-2.5 align-top hidden xl:table-cell">{r.gender || '—'}</td>
          <td class="py-2.5 align-top">
            <span class="inline-flex items-center gap-1.5" style="color:var(--t-ink)">
              <span class="h-2 w-2 rounded-full shrink-0" style="background:{categoryDot(r.category)}"></span>
              {r.category}
            </span>
          </td>
          <td class="py-2.5 align-top hidden lg:table-cell tabular-nums">{r.validity_date || '—'}</td>
          <td class="py-2.5 align-top text-right pr-6">
            {#if r.status}
              {@render statusBadge(r.status)}
            {:else}
              <span class="text-[var(--t-ink-soft)]">—</span>
            {/if}
          </td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>
