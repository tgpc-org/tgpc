<script lang="ts">
  import { cachedOrNull, setCache } from '$lib/cache';
  import { R2_NOTICES } from '$lib/r2';
  import type { Notice } from '$lib/types';
  import { fetchNotices } from '$lib/api';
  import { fitToViewport } from '$lib/fitToViewport';
  import { browser } from '$app/environment';
  import { MONTHS } from '$lib/dates';

  let { data } = $props();

  let notices = $state<Notice[]>([]);
  let years = $state<string[]>([]);
  let tab = $state<string>('all');
  let query = $state('');
  let loading = $state(true);

  function fmtDate(s: string) {
    const d = new Date(s + 'T00:00:00');
    return `${String(d.getDate()).padStart(2,'0')} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  }

  function getYr(s: string) { return s.slice(0, 4); }

  /** PDFs are marked by a red-tinted chip plus a dot (see the style block). */
  function isPdf(url: string): boolean {
    return /\.pdf(?:\?.*)?$/i.test(url);
  }

  function resolve(url: string) {
    return url.startsWith('http') ? url : `${R2_NOTICES}${url}`;
  }

  // Rows are keyed by index: notice titles and link URLs are not guaranteed
  // unique, and a keyed each with a duplicate key throws each_key_duplicate,
  // which would blank the whole page. Index keys are safe here because the
  // list is replaced wholesale whenever the data reloads.
  let filtered = $derived.by(() => notices.filter(n => {
    // 'all' is the unfiltered tab, not a year — comparing a year to the
    // literal 'all' emptied the list on the default tab.
    if (tab !== 'all' && getYr(n.date) !== tab) return false;
    if (!query) return true;
    const q = query.toLowerCase();
    return n.title.toLowerCase().includes(q) || fmtDate(n.date).toLowerCase().includes(q);
  }));

  const cached = browser && cachedOrNull<Notice[]>('tgpc_notices');
  // An empty cached array means a past failed fetch — treat as absent so the
  // page retries instead of skeleton-locking (and never cache empties below).
  const cachedFresh = cached && cached.length > 0 ? cached : null;
  // svelte-ignore state_referenced_locally
  const initial = cachedFresh || data.notices;
  if (initial.length > 0) {
    notices = initial;
    buildYears();
    loading = false;
  } else if (browser) {
    // Nothing to show (SSR empty too) — this is the only case that fetches,
    // so good SSR data is never wiped by a failed client request.
    fetchNotices().then(raw => {
      if (!raw || raw.length === 0) { loading = false; return; }
      setCache('tgpc_notices', raw);
      notices = raw;
      buildYears();
      loading = false;
    });
  } else {
    loading = false;
  }

  function buildYears() {
    years = [...new Set(notices.map(n => getYr(n.date)))].sort((a, b) => +b - +a);
    // Preserve the user's tab across background refetches.
    if (tab !== 'all' && !years.includes(tab)) tab = 'all';
  }

</script>

<svelte:head>
  <title>Notices — TGPC RPh Index</title>
</svelte:head>

{#snippet linkChips(links: Notice['links'])}
  {#if links?.length}
    {#each links as link, li (li)}
      <a href={resolve(link.url)} target="_blank" rel="noopener" class="notice-link px-2.5 py-1 rounded text-[0.75rem] font-semibold no-underline {isPdf(link.url) ? 'is-pdf' : ''}">
        {#if isPdf(link.url)}<span class="notice-dot" aria-hidden="true"></span>{/if}{link.label}
      </a>
    {/each}
  {:else}
    <span class="text-[var(--t-border-soft)]">—</span>
  {/if}
{/snippet}

{#snippet noticeRows(list: Notice[])}
  {#each list as n, i (i)}
    <div style="display:grid;grid-template-columns:96px 1fr 160px;gap:12px;padding:12px 0;border-bottom:1px solid var(--t-surface);font-size:0.875rem">
      <span class="text-[var(--t-muted)] tabular-nums">{fmtDate(n.date)}</span>
      <span style="min-width:0">{n.title}</span>
      <span class="flex gap-1 flex-wrap" style="min-width:0">
        {@render linkChips(n.links)}
      </span>
    </div>
  {/each}
{/snippet}

{#snippet noticeCards(list: Notice[])}
  {#each list as n, i (i)}
    <div class="py-2.5 border-b border-[var(--t-surface)]">
      <div class="text-[0.75rem] text-[var(--t-muted)] tabular-nums">{fmtDate(n.date)}</div>
      <div class="text-[0.875rem] mt-0.5">{n.title}</div>
      {#if n.links?.length}
        <div class="flex gap-1.5 mt-1 flex-wrap">
          {@render linkChips(n.links)}
        </div>
      {/if}
    </div>
  {/each}
{/snippet}

<div class="space-y-4">
  <h1 class="sr-only">TGPC notices and circulars</h1>
  <div class="flex items-center gap-2">
    <div class="relative flex-1">
      <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9ca3af] pointer-events-none" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
      <label for="notice-search" class="sr-only">Search notices</label>
      <input id="notice-search" type="text" bind:value={query} placeholder="Search notices"
        aria-label="Search notices"
        class="w-full pl-9 pr-4 py-1.5 border-b-2 border-[var(--t-border)] text-[0.95rem] bg-transparent outline-none transition-colors focus:border-[#00cc66] max-sm:text-base" />
    </div>
  </div>

  <div class="-mx-1 px-1 flex-nowrap overflow-x-auto sm:flex-wrap gap-1.5 text-[0.75rem]" style="scrollbar-width:thin;scrollbar-color:var(--t-border) transparent;-webkit-overflow-scrolling:touch">
    <button onclick={() => tab = 'all'}
      class="px-2.5 py-1.5 rounded text-[0.75rem] font-semibold transition-colors cursor-pointer border-none whitespace-nowrap"
      style={tab === 'all' ? 'background:#00cc66;color:var(--t-ink)' : 'background:var(--t-surface);color:var(--t-ink-soft)'}>
      All ({notices.length})
    </button>
    {#each years as y (y)}
      <button onclick={() => tab = y}
        class="px-2.5 py-1.5 rounded text-[0.75rem] font-semibold transition-colors cursor-pointer border-none whitespace-nowrap"
        style={y === tab ? 'background:#00cc66;color:var(--t-ink)' : 'background:var(--t-surface);color:var(--t-ink-soft)'}>
        {y} ({notices.filter(n => getYr(n.date) === y).length})
      </button>
    {/each}
  </div>

  {#if loading}
    <div class="space-y-3 py-4">
      {#each Array(4) as _, i (i)}
        <div class="h-4 bg-[var(--t-surface)] rounded" style="width:{50 + Math.random() * 40}%"></div>
      {/each}
    </div>
  {:else if filtered.length === 0}
    <p class="text-[0.85rem] py-8 text-center" style="color:var(--t-muted)">No notices</p>
  {:else}
    <div use:fitToViewport class="overflow-x-hidden overflow-y-auto">
      <div class="hidden md:block">
        <div style="display:grid;grid-template-columns:96px 1fr 160px;gap:12px;align-items:center;justify-items:center;padding:6px 0;border-bottom:1px solid var(--t-border-soft);font-size:0.7rem;font-weight:600;color:var(--t-muted);text-transform:uppercase;letter-spacing:0.5px">
          <span>Date</span>
          <span>Title / Description</span>
          <span style="justify-self:start">Links</span>
        </div>
        {#if tab === 'all'}
          {#each years as y (y)}
            {@const fy = filtered.filter(n => getYr(n.date) === y)}
            {#if fy.length > 0}
              <div class="text-[0.7rem] font-semibold text-[var(--t-muted)] uppercase tracking-wider py-2 px-1">{y} — {fy.length}</div>
              {@render noticeRows(fy)}
            {/if}
          {/each}
        {:else}
          {@render noticeRows(filtered)}
        {/if}
      </div>

      <div class="md:hidden space-y-1">
        {#if tab === 'all'}
          {#each years as y (y)}
            {@const fy = filtered.filter(n => getYr(n.date) === y)}
            {#if fy.length > 0}
              <div class="text-[0.7rem] font-semibold text-[var(--t-muted)] uppercase tracking-wider py-2">{y} — {fy.length}</div>
              {@render noticeCards(fy)}
            {/if}
          {/each}
        {:else}
          {@render noticeCards(filtered)}
        {/if}
      </div>
    </div>
  {/if}
</div>

<style>
  /* Link chips keep their label on the ink token: brand red (#ef4444) and
     blue-on-light-tint both failed AA as small text, so the *type* is carried
     by the chip tint and a dot instead of by the text colour. */
  .notice-link {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    color: var(--t-ink);
    /* color-mix instead of a literal tint: it tracks --t-link's night value,
       and only brand red/green may appear as rgba() triplets. */
    background: color-mix(in srgb, var(--t-link) 12%, transparent);
  }
  .notice-link:hover {
    background: color-mix(in srgb, var(--t-link) 20%, transparent);
  }
  .notice-link.is-pdf {
    background: rgba(239, 68, 68, 0.1);
  }
  .notice-link.is-pdf:hover {
    background: rgba(239, 68, 68, 0.18);
  }
  .notice-dot {
    width: 0.375rem;
    height: 0.375rem;
    border-radius: 9999px;
    background: #ef4444;
    flex-shrink: 0;
  }
</style>
