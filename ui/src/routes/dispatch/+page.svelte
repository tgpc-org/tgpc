<script lang="ts">
  import type { DispatchFile } from '#lib/types.js';
  import { fetchDispatchFiles } from '#lib/api.js';
  import { browser } from '$app/env';

  import { cachedOrNull, setCache } from '#lib/cache.js';
  import { MONTHS } from '#lib/dates.js';

  let { data } = $props();

  let files = $state<DispatchFile[]>([]);
  let years = $state<string[]>([]);
  // Visitors browse dispatch lists by year, not across all of them at once,
  // so there is deliberately no "All" tab: null means "auto" and resolves
  // to the latest year as soon as the year list is known.
  let tab = $state<string | null>(null);
  let query = $state('');
  let loading = $state(true);

  // Render the 200+ cards in chunks: the list grows on demand instead of
  // mounting every card at once on low-end phones.
  const PAGE_SIZE = 40;
  let visibleCount = $state(PAGE_SIZE);
  // A tab or query change invalidates what is visible — back to one page.
  $effect(() => { void tab; void query; visibleCount = PAGE_SIZE; });

  function parse(n: string) {
    const m = n.match(/DL(\d{2})(\d{2})(\d{4})[A-Z]*\.pdf/i);
    return m ? { d: m[1], mo: m[2], y: m[3], date: new Date(+m[3], +m[2]-1, +m[1]) } : null;
  }

  function fmt(f: { d: string; mo: string; y: string }) {
    return `${f.d} ${MONTHS[+f.mo-1]} ${f.y}`;
  }

  let sizes = $state<Record<string, number>>({});

  function build(raw: { name: string; size?: number; stale?: boolean }[]) {
    // Reset each time: stale sizes from a previous fetch must not survive.
    sizes = {};

    raw.forEach((f) => {
      if (f.size) sizes[f.name] = f.size;
    });

    files = raw.map((f) => ({
      name: f.name,
      parsed: parse(f.name),
      size: f.size,
      stale: f.stale
    })).filter((f) => f.parsed).sort((a, b) => (b.parsed!).date.getTime() - (a.parsed!).date.getTime());

    years = [...new Set(files.map((f) => (f.parsed!).y))].sort((a, b) => +b - +a);

    // Preserve the user's tab across background refetches; auto/latest when
    // unset or gone (e.g. a refetch that lost the newest year's files).
    if (tab === null || !years.includes(tab)) tab = years[0] ?? null;
  }

  let filtered = $derived.by(() => files.filter((f) => {
    if (!f.parsed) return false;
    // tab is null only before the first build(); the loading gate keeps
    // that state off-screen.
    if (tab === null || f.parsed.y !== tab) return false;
    if (!query) return true;
    const q = query.toLowerCase();
    return f.name.toLowerCase().includes(q) || fmt(f.parsed).toLowerCase().includes(q);
  }));

  // Chunked view of `filtered`: renders PAGE_SIZE cards and grows on demand.
  let limited = $derived(filtered.slice(0, visibleCount));
  let remainingCount = $derived(Math.max(0, filtered.length - visibleCount));

  const cached = browser && cachedOrNull<{ name: string; size?: number; stale?: boolean }[]>("tgpc_dispatch");
  // An empty cached array means a past failed fetch — treat as absent so the
  // page retries instead of skeleton-locking (and never cache empties below).
  const cachedFresh = cached && cached.length > 0 ? cached : null;
  // svelte-ignore state_referenced_locally
  const initial = cachedFresh || data.files;
  if (initial.length > 0) {
    build(initial);
    loading = false;
  } else if (browser) {
    // Nothing to show (SSR empty too) — this is the only case that fetches,
    // so good SSR data is never wiped by a failed client request.
    fetchDispatchFiles().then((raw) => {
      if (!raw || raw.length === 0) {
        loading = false;

        return;
      }

      setCache('tgpc_dispatch', raw);
      build(raw);
      loading = false;
    });
  } else {
    loading = false;
  }
</script>

<svelte:head>
  <title>Dispatch List — TGPC RPh Index</title>
</svelte:head>

{#snippet dispatchCard(f: DispatchFile)}
  <a href={`/api/dispatch/${f.name}`} target="_blank" rel="noopener"
    class="flex items-center gap-2.5 p-3 border border-[var(--t-border)] rounded-xl no-underline text-[var(--t-ink)] hover:bg-[var(--t-surface-3)] transition-colors">
    <img src="/pdf.svg" alt="" width="24" height="24" class="block flex-shrink-0" />
    <div class="min-w-0">
      <div class="text-[0.65rem] font-semibold uppercase tracking-widest text-[var(--t-muted)]">Dispatch List</div>
      <div class="text-[0.85rem] font-semibold truncate">{fmt(f.parsed!)}</div>
      <div class="text-[0.7rem] text-[var(--t-muted)]">{sizes[f.name] ? Math.round(sizes[f.name] / 1024) + ' KB' : ''}{#if f.stale}<span class="ml-1 inline-block rounded px-1.5 py-0.5 text-[0.65rem] font-semibold" style="background:rgba(239,68,68,0.12);color:var(--t-ink)">may be stale</span>{/if}</div>
    </div>
  </a>
{/snippet}

<div class="space-y-4">
  <h1 class="sr-only">TGPC dispatch list</h1>
  <div class="flex items-center gap-2">
    <div class="relative flex-1">
      <svg
        class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9ca3af] pointer-events-none"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="2"
        aria-hidden="true"
      >
        <circle cx="11" cy="11" r="8"></circle>
        <path d="m21 21-4.35-4.35"></path>
      </svg>
      <label for="dispatch-search" class="sr-only">Search dispatch files</label>
      <input id="dispatch-search" type="text" bind:value={query} placeholder="Search files"
        aria-label="Search dispatch files"
        class="w-full pl-9 pr-9 py-1.5 border-b-2 border-[var(--t-border)] text-[0.95rem] bg-transparent outline-none transition-colors focus:border-[#00cc66] max-sm:text-base" />
      {#if query}
        <button
          type="button"
          onclick={() => query = ''}
          aria-label="Clear search"
          class="absolute right-2 top-1/2 -translate-y-1/2 flex h-5 w-5 items-center justify-center rounded-full border-none cursor-pointer transition-colors"
          style="background:var(--t-surface);color:var(--t-muted)"
        >
          <svg
            class="w-3 h-3"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2.5"
            aria-hidden="true"
          ><path d="M18 6 6 18M6 6l12 12"></path></svg>
        </button>
      {/if}
    </div>
  </div>

  <div class="-mx-1 px-1 flex-nowrap overflow-x-auto sm:flex-wrap gap-1.5 text-[0.75rem]" style="scrollbar-width:thin;scrollbar-color:var(--t-border) transparent;-webkit-overflow-scrolling:touch">
    {#each years as y (y)}
      <button onclick={() => tab = y}
        class="px-2.5 py-1.5 rounded text-[0.75rem] font-semibold transition-colors cursor-pointer border-none whitespace-nowrap"
        style={y === tab
          ? 'background:#00cc66;color:var(--t-ink)'
          : 'background:var(--t-surface);color:var(--t-ink-soft)'}
      >{y} ({files.filter((f) => f.parsed?.y === y).length})</button>
    {/each}
  </div>

  {#if loading}
    <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
      {#each Array(8) as _, i (i)}
        <div class="h-16 bg-[var(--t-surface)] rounded"></div>
      {/each}
    </div>
  {:else if filtered.length === 0}
    <p class="text-[0.85rem] py-8 text-center" style="color:var(--t-muted)">No files</p>
  {:else}
    <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
      {#each limited as f (f.name)}
        {@render dispatchCard(f)}
      {/each}
    </div>
    {#if remainingCount > 0}
      <div class="flex justify-center pt-3">
        <button
          type="button"
          onclick={() => visibleCount += PAGE_SIZE}
          class="px-4 py-2.5 rounded-full text-[0.75rem] font-semibold cursor-pointer border-none transition-colors hover:bg-[rgba(0,204,102,0.14)]"
          style="background:var(--t-surface-3);color:var(--t-ink-soft)">
          Load more · {remainingCount.toLocaleString()} remaining
        </button>
      </div>
    {/if}
  {/if}
</div>
