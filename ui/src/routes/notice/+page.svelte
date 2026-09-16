<script lang="ts">
  import { cachedOrNull, setCache } from '$lib/cache';
  import { R2_NOTICES } from '$lib/r2';
  import type { Notice } from '$lib/types';
  import { fetchNotices } from '$lib/api';
  import { browser } from '$app/environment';

  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  let { data } = $props();

  let notices = $state<Notice[]>([]);
  let years = $state<string[]>([]);
  let tab = $state<string | null>(null);
  let query = $state('');
  let loading = $state(true);

  function fmtDate(s: string) {
    const d = new Date(s + 'T00:00:00');
    return `${String(d.getDate()).padStart(2,'0')} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
  }

  function getYr(s: string) { return s.slice(0, 4); }

  function linkType(url: string): string {
    const e = url.match(/\.([a-z0-9]+)(?:\?.*)?$/i)?.[1]?.toLowerCase() || '';
    if (e === 'pdf') return '#ef4444';
    // Blue links use the theme token (lightened for contrast at night).
    return 'var(--t-link)';
  }

  // Badge tint that works with both hex colors and var() tokens
  // (the old `{color}10` suffix trick is only valid for hex).
  function linkBg(color: string, alpha: number): string {
    return `color-mix(in srgb, ${color} ${alpha}%, transparent)`;
  }

  function resolve(url: string) {
    return url.startsWith('http') ? url : `${R2_NOTICES}${url}`;
  }

  let filtered = $derived.by(() => notices.filter(n => {
    if (tab && getYr(n.date) !== tab) return false;
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
    if (!tab || !years.includes(tab)) tab = years[0] || null;
  }

</script>

<svelte:head>
  <title>Notices — TGPC RPh Index</title>
</svelte:head>

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

  <div class="-mx-1 px-1 flex-nowrap overflow-x-auto sm:flex-wrap gap-1 text-[0.75rem]" style="scrollbar-width:thin;scrollbar-color:var(--t-border) transparent;-webkit-overflow-scrolling:touch">
    {#each years as y (y)}
      <button onclick={() => tab = y}
        class="px-2.5 py-1 rounded text-[0.7rem] font-medium transition-all cursor-pointer border-none whitespace-nowrap"
        style={y === tab ? 'background:#00cc66;color:#fff' : 'background:var(--t-surface);color:var(--t-muted)'}>
        {y} <span class="opacity-50">({notices.filter(n => getYr(n.date) === y).length})</span>
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
    <p class="text-[0.85rem] text-[#9ca3af] py-8 text-center">No notices</p>
  {:else}
    <div style="max-height:calc(100vh - 240px);overflow-x:hidden;overflow-y:auto">
      <div class="hidden md:block">
        <div style="display:grid;grid-template-columns:96px 1fr 160px;gap:12px;align-items:center;justify-items:center;padding:6px 0;border-bottom:1px solid var(--t-border-soft);font-size:0.65rem;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:0.5px">
          <span>Date</span>
          <span>Title / Description</span>
          <span style="justify-self:start">Links</span>
        </div>
        {#if tab === null}
          {#each years as y (y)}
            {@const fy = filtered.filter(n => getYr(n.date) === y)}
            {#if fy.length > 0}
              <div class="text-[0.65rem] font-semibold text-[#9ca3af] uppercase tracking-wider py-2 px-1">{y} — {fy.length}</div>
              {#each fy as n (n.title)}
                <div style="display:grid;grid-template-columns:96px 1fr 160px;gap:12px;padding:10px 0;border-bottom:1px solid var(--t-surface);font-size:0.875rem">
                  <span class="text-[var(--t-muted)] tabular-nums">{fmtDate(n.date)}</span>
                  <span style="min-width:0">{n.title}</span>
                  <span class="flex gap-1 flex-wrap" style="min-width:0">
                    {#if n.links?.length}
                      {#each n.links as link (link.url)}
                        <a href={resolve(link.url)} target="_blank" rel="noopener"
                          class="px-2 py-0.5 rounded text-[0.7rem] font-medium no-underline transition-colors"
                          style="color:{linkType(link.url)};background:{linkBg(linkType(link.url), 8)}"
                          onmouseenter={(e) => e.currentTarget.style.background = linkBg(linkType(link.url), 16)}
                          onmouseleave={(e) => e.currentTarget.style.background = linkBg(linkType(link.url), 8)}>
                          {link.label}
                        </a>
                      {/each}
                    {:else}
                      <span class="text-[var(--t-border-soft)]">—</span>
                    {/if}
                  </span>
                </div>
              {/each}
            {/if}
          {/each}
        {:else}
          {#each filtered as n (n.title)}
            <div style="display:grid;grid-template-columns:96px 1fr 160px;gap:12px;padding:10px 0;border-bottom:1px solid var(--t-surface);font-size:0.875rem">
              <span class="text-[var(--t-muted)] tabular-nums">{fmtDate(n.date)}</span>
              <span style="min-width:0">{n.title}</span>
              <span class="flex gap-1 flex-wrap" style="min-width:0">
                {#if n.links?.length}
                  {#each n.links as link (link.url)}
                    <a href={resolve(link.url)} target="_blank" rel="noopener"
                      class="px-2 py-0.5 rounded text-[0.7rem] font-medium no-underline transition-colors"
                      style="color:{linkType(link.url)};background:{linkBg(linkType(link.url), 8)}"
                      onmouseenter={(e) => e.currentTarget.style.background = linkBg(linkType(link.url), 16)}
                      onmouseleave={(e) => e.currentTarget.style.background = linkBg(linkType(link.url), 8)}>
                      {link.label}
                    </a>
                  {/each}
                {:else}
                  <span class="text-[var(--t-border-soft)]">—</span>
                {/if}
              </span>
            </div>
          {/each}
        {/if}
      </div>

      <div class="md:hidden space-y-1">
        {#if tab === null}
          {#each years as y (y)}
            {@const fy = filtered.filter(n => getYr(n.date) === y)}
            {#if fy.length > 0}
              <div class="text-[0.65rem] font-semibold text-[#9ca3af] uppercase tracking-wider py-2">{y} — {fy.length}</div>
              {#each fy as n (n.title)}
                <div class="py-2.5 border-b border-[var(--t-surface)]">
                  <div class="text-[0.75rem] text-[var(--t-muted)] tabular-nums">{fmtDate(n.date)}</div>
                  <div class="text-[0.875rem] mt-0.5">{n.title}</div>
                  {#if n.links?.length}
                    <div class="flex gap-1.5 mt-1">
                      {#each n.links as link (link.url)}
                        <a href={resolve(link.url)} target="_blank" rel="noopener"
                          class="px-2 py-0.5 rounded text-[0.7rem] font-medium no-underline"
                          style="color:{linkType(link.url)};background:{linkBg(linkType(link.url), 8)}">
                          {link.label}
                        </a>
                      {/each}
                    </div>
                  {/if}
                </div>
              {/each}
            {/if}
          {/each}
        {:else}
          {#each filtered as n (n.title)}
            <div class="py-2.5 border-b border-[var(--t-surface)]">
              <div class="text-[0.75rem] text-[var(--t-muted)] tabular-nums">{fmtDate(n.date)}</div>
              <div class="text-[0.875rem] mt-0.5">{n.title}</div>
              {#if n.links?.length}
                <div class="flex gap-1.5 mt-1">
                  {#each n.links as link (link.url)}
                    <a href={resolve(link.url)} target="_blank" rel="noopener"
                      class="px-2 py-0.5 rounded text-[0.7rem] font-medium no-underline"
                      style="color:{linkType(link.url)};background:{linkBg(linkType(link.url), 8)}">
                      {link.label}
                    </a>
                  {/each}
                </div>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
    </div>
  {/if}
</div>
