<script lang="ts">
  import type { PharmacistRecord } from '$lib/types';
  import { CATEGORY_COLORS } from '$lib/colors';
  import { PUBLIC_R2_PHOTO_BASE } from '$env/static/public';

  let {
    rows,
    onopen
  }: {
    rows: PharmacistRecord[];
    onopen: (_reg: string) => void;
  } = $props();

  function photoUrl(r: PharmacistRecord): string {
    return r.photo_url || `${PUBLIC_R2_PHOTO_BASE}/${r.registration_number}.webp`;
  }

  function statusTint(status: string): string {
    return status === 'Active' ? 'rgba(0,204,102,0.14)' : 'rgba(239,68,68,0.12)';
  }

  function statusDot(status: string): string {
    return status === 'Active' ? '#00cc66' : '#ef4444';
  }

  /** Category colour as a dot: the hues themselves fail AA as small text. */
  function categoryDot(cat: string): string {
    const c = CATEGORY_COLORS[cat as keyof typeof CATEGORY_COLORS];
    if (!c || c === '#111827') return 'var(--t-ink)';
    return c;
  }
</script>

{#snippet statusBadge(status: string)}
  <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[0.65rem] font-semibold uppercase tracking-wider" style="background:{statusTint(status)};color:var(--t-ink)">
    <span class="h-1.5 w-1.5 rounded-full shrink-0" style="background:{statusDot(status)}"></span>{status}
  </span>
{/snippet}

{#snippet categoryBadge(cat: string)}
  <span class="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 font-semibold uppercase tracking-wider text-[0.65rem]" style="background:color-mix(in srgb, {categoryDot(cat)} 16%, transparent);color:var(--t-ink)">
    <span class="h-1.5 w-1.5 rounded-full shrink-0" style="background:{categoryDot(cat)}"></span>{cat}
  </span>
{/snippet}

<div class="space-y-2" data-testid="mobile-results">
  {#each rows as r (r.registration_number)}
    <!-- 'relative' + the link's stretched ::after make the whole card the tap
         target: the bare RPC text link was only ~21px tall, under the 24px
         WCAG 2.5.8 minimum and far under the 44px app guideline. No other
         interactive element lives inside a card, so the overlay is safe. -->
    <div class="relative flex gap-3 p-3 rounded-xl border border-[var(--t-surface)] bg-[var(--t-surface-3)] text-[0.875rem]" style="content-visibility:auto;contain-intrinsic-size:150px">
      <img src={photoUrl(r)} alt="" loading="lazy" decoding="async" width="48" height="58" class="w-12 h-14 rounded-md object-cover bg-[var(--t-surface)] flex-shrink-0" />
      <div class="min-w-0 flex-1">
        <div class="flex items-center justify-between gap-2">
          <a href="/rph/{r.registration_number}" onclick={(e) => { e.preventDefault(); onopen(r.registration_number); }} class="inline-flex items-center h-6 text-[var(--t-link)] hover:underline no-underline cursor-pointer tabular-nums font-semibold after:content-[''] after:absolute after:inset-0 after:rounded-xl" aria-label="View profile for {r.registration_number}">{r.registration_number}</a>
          {#if r.status}
            <span class="flex-shrink-0">{@render statusBadge(r.status)}</span>
          {/if}
        </div>
        <div class="mt-1 text-[var(--t-ink)] font-medium leading-snug">{r.name}</div>
        <div class="mt-0.5 text-[var(--t-muted)] text-[0.8rem] leading-snug">{r.father_name || '—'}</div>
        <div class="flex flex-wrap items-center gap-x-2.5 gap-y-1 mt-1.5 text-[0.75rem]">
          {@render categoryBadge(r.category)}
          {#if r.gender}<span class="text-[var(--t-muted)]">{r.gender}</span>{/if}
          {#if r.validity_date}<span class="text-[var(--t-muted)]">Valid till <span class="tabular-nums">{r.validity_date}</span></span>{/if}
        </div>
      </div>
    </div>
  {/each}
</div>
