<svelte:head>
  <title>TGPC RPh Index</title>
  <meta name="description" content="Unofficial open-source index of the Telangana State Pharmacy Council pharmacist registry. Search pharmacists by name or RPC number, browse notices and dispatch lists." />
  <link rel="canonical" href={$page.url.origin + $page.url.pathname} />
  <meta property="og:type" content="website" />
  <meta property="og:site_name" content="TGPC RPh Index" />
  <meta property="og:title" content="TGPC RPh Index" />
  <meta property="og:description" content="Unofficial open-source index of the Telangana State Pharmacy Council pharmacist registry. Search pharmacists by name or RPC number, browse notices and dispatch lists." />
  <meta property="og:url" content={$page.url.origin + $page.url.pathname} />
  <meta name="twitter:card" content="summary" />
  <meta name="twitter:title" content="TGPC RPh Index" />
  <meta name="twitter:description" content="Unofficial open-source index of the Telangana State Pharmacy Council pharmacist registry. Search pharmacists by name or RPC number, browse notices and dispatch lists." />
  <link rel="preconnect" href={PUBLIC_SUPABASE_URL} />
  {#if R2_ORIGIN}<link rel="preconnect" href={R2_ORIGIN} />{/if}
  <link rel="dns-prefetch" href={PUBLIC_SUPABASE_URL} />
  {#if R2_ORIGIN}<link rel="dns-prefetch" href={R2_ORIGIN} />{/if}
</svelte:head>

<script lang="ts">
  import '../app.css';
  import type { ConnectionStatus, Stats } from '$lib/types';
  import { getStats } from '$lib/api';
  import { supabase } from '$lib/supabase';
  import { page } from '$app/stores';
  import { CATEGORY_COLORS, CATEGORIES, CATEGORY_KEYS } from '$lib/colors';
  import { PUBLIC_SUPABASE_URL } from '$env/static/public';
  import { R2_ORIGIN } from '$lib/r2';
  import { setCache } from '$lib/cache';
  import { initTheme, themeName, toggleTheme } from '$lib/theme';

  import Clock from '$lib/components/Clock.svelte';

  let { children, data } = $props();
  // svelte-ignore state_referenced_locally
  let ssrStats = data.stats;
  // svelte-ignore state_referenced_locally
  let ssrSync = data.lastSync;

  let status = $state<ConnectionStatus>(ssrStats ? 'Live' : 'Busy');
  let stats = $state<Stats | null>(ssrStats);
  let lastSync = $state<string>(ssrSync);

  async function loadStats() {
    status = 'Busy';
    const data = await getStats();
    if (data) {
      stats = data;
      status = 'Live';
      setCache('tgpc_stats', data);
    } else if (!ssrStats) {
      status = 'Offline';
    }
  }

  async function loadLastSync() {
    try {
      const { data, error } = await supabase
        .from('metadata')
        .select('value')
        .eq('key', 'last_sync')
        .single();
      if (!error && data?.value) {
        const d = new Date(data.value);
        const s = d.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', weekday: 'short', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
        lastSync = s.toUpperCase().replace(/,/g, '');
        setCache('tgpc_last_sync', lastSync);
      }
    } catch {}
  }

  $effect(() => {
    // Sync toggle state with the FOUC guard in app.html (client only).
    initTheme();
  });

  $effect(() => {
    // SSR already supplied stats — don't refetch on mount; realtime channel
    // below keeps them fresh.
    if (!ssrStats) loadStats();
    if (!ssrSync) loadLastSync();
    const channel = supabase
      .channel('metadata-changes')
      .on('postgres_changes', { event: '*', schema: 'public', table: 'metadata', filter: `key=eq.last_sync` }, () => { loadStats(); loadLastSync(); })
      .subscribe();
    return () => { supabase.removeChannel(channel); };
  });

  // Text stays on the ink/muted tokens (both AA on every surface); brand
  // red/green appear as dots/fills, where contrast rules do not apply.
  let statusConfig = $derived.by(() => ({
    Live: { bg: 'rgba(0,204,102,0.08)', border: 'rgba(0,204,102,0.35)', dot: '#00cc66' },
    Busy: { bg: 'rgba(239,68,68,0.06)', border: 'rgba(239,68,68,0.35)', dot: '#ef4444' },
    Offline: { bg: 'rgba(239,68,68,0.06)', border: 'rgba(239,68,68,0.35)', dot: '#ef4444' }
  })[status]);

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

  // One flat tile list so the strip can scroll as a single row on phones
  // without the 7-column layout spilling out of the header.
  let tiles = $derived.by(() => {
    const out: { label: string; value: string; dot: string }[] = [
      { label: 'Total RPh', value: val('total'), dot: '#ef4444' }
    ];
    for (const cat of sortedCategories) {
      const color = CATEGORY_COLORS[cat];
      out.push({
        label: cat,
        value: val(CATEGORY_KEYS[CATEGORIES.indexOf(cat)] as keyof Stats),
        dot: color === '#111827' ? 'var(--t-ink)' : color
      });
    }
    return out;
  });

  let activeTab = $derived($page.url.pathname === '/' ? 'search' : $page.url.pathname === '/notice' ? 'notice' : $page.url.pathname === '/dispatch' ? 'dispatch' : '');

  let searchRef = $state<HTMLAnchorElement | undefined>(undefined);
  let noticeRef = $state<HTMLAnchorElement | undefined>(undefined);
  let dispatchRef = $state<HTMLAnchorElement | undefined>(undefined);

  const TAB_CLASS = 'px-3 py-2.5 text-[0.75rem] font-bold uppercase tracking-wider whitespace-nowrap no-underline transition-colors';
  const TAB_DIVIDER = 'text-[0.75rem] font-light select-none px-1';

  function tabStyle(active: boolean): string {
    return `color:${active ? 'var(--t-ink)' : 'var(--t-muted)'}`;
  }

  // Slider geometry is measured (not derived from a stored string) so it also
  // follows window resizes and font loading.
  let slider = $state({ left: 0, width: 0, ready: false });

  $effect(() => {
    const tab = activeTab;
    const el = tab === 'search' ? searchRef : tab === 'notice' ? noticeRef : dispatchRef;
    if (!el) {
      slider = { left: 0, width: 0, ready: false };
      return;
    }
    const measure = () => {
      slider = { left: el.offsetLeft, width: el.offsetWidth, ready: true };
    };
    measure();
    window.addEventListener('resize', measure);
    return () => window.removeEventListener('resize', measure);
  });
</script>

<div class="min-h-screen flex flex-col" style="background:var(--t-bg)">
  <a href="#main-content" class="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[100] focus:rounded-lg focus:px-3 focus:py-2 focus:font-bold focus:no-underline" style="background:var(--t-surface-2);color:var(--t-ink)">Skip to main content</a>

  <header class="sticky top-0 z-50 border-b" style="background:var(--t-bg);border-color:var(--t-border)">
    <div class="w-full px-3 sm:px-5">
      <!-- Brand row -->
      <div class="flex items-center justify-between gap-3 py-2">
        <div class="flex items-center gap-3 min-w-0">
          <a href="/" class="no-underline shrink-0" title="TGPC RPh Index — home">
            <span class="text-[1.65rem] font-bold tracking-tight inline-flex items-center gap-1" style="color:var(--t-ink);white-space:nowrap">
              <span style="color:#00cc66">TGPC</span><span style="color:#ef4444">RPh</span><span class="text-[#9ca3af]">Index</span>
            </span>
          </a>
          <span class="hidden md:inline text-[0.75rem] font-medium truncate" style="color:var(--t-muted)">Open-source TGPC pharmacist data</span>
        </div>

        <div class="flex items-center gap-2 shrink-0">
          <span
            class="inline-flex items-center gap-1.5 h-7 rounded-full px-2.5 text-[0.7rem] font-semibold whitespace-nowrap"
            style="background:{statusConfig.bg};border:1px solid {statusConfig.border};color:var(--t-ink)"
            title="Live data status"
          >
            <span class="h-1.5 w-1.5 rounded-full shrink-0" style="background:{statusConfig.dot}"></span>
            {status}
            {#if status !== 'Offline'}
              <!-- ink-soft, not muted: the pill's brand tint lowers the
                   background enough to push muted text under 4.5:1. -->
              <span class="hidden lg:inline-flex items-center gap-1.5 font-normal" style="color:var(--t-ink-soft)">
                <span aria-hidden="true">·</span>
                <Clock />
              </span>
            {/if}
          </span>

          <button
            onclick={() => toggleTheme()}
            aria-pressed={$themeName === 'dark'}
            aria-label="Toggle day and night mode"
            title={$themeName === 'dark' ? 'Switch to day mode' : 'Switch to night mode'}
            class="shrink-0 inline-flex items-center justify-center h-9 w-9 rounded-full border transition-colors cursor-pointer"
            style="border-color:var(--t-border);background:var(--t-surface-2);color:var(--t-muted)"
          >
            {#if $themeName === 'dark'}
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
            {:else}
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
            {/if}
          </button>
        </div>
      </div>

      <!-- Registry stats: one scrollable strip, readable at every width -->
      <div
        class="-mx-3 sm:-mx-5 px-3 sm:px-5 border-t overflow-x-auto"
        style="border-color:var(--t-border);scrollbar-width:thin;scrollbar-color:var(--t-border) transparent;-webkit-overflow-scrolling:touch"
      >
        <div class="w-full min-w-max flex items-stretch gap-1 py-1.5">
          {#each tiles as tile, i (tile.label)}
            <div class="flex flex-col justify-center gap-0.5 px-3 shrink-0" style="border-right:{i < tiles.length - 1 ? '1px solid var(--t-border)' : 'none'}">
              <span class="flex items-center gap-1.5 text-[0.65rem] font-semibold uppercase tracking-wider whitespace-nowrap" style="color:var(--t-muted)">
                <span class="h-1.5 w-1.5 rounded-full shrink-0" style="background:{tile.dot}"></span>
                {tile.label}
              </span>
              <span class="text-[1rem] font-bold tabular-nums leading-none" style="color:var(--t-ink)">{tile.value}</span>
            </div>
          {/each}

          <div class="ml-auto flex items-center gap-4 pl-4 shrink-0">
            <div class="flex flex-col justify-center gap-0.5">
              <span class="flex items-center gap-1.5 text-[0.65rem] font-semibold uppercase tracking-wider whitespace-nowrap" style="color:var(--t-muted)">
                <span class="h-1.5 w-1.5 rounded-full shrink-0" style="background:{statusConfig.dot}"></span>
                Last sync
              </span>
              <span class="text-[0.7rem] font-medium whitespace-nowrap" style="color:var(--t-ink)">{lastSync || '—'}</span>
            </div>
            <div class="flex flex-col justify-center gap-0.5">
              <span class="text-[0.65rem] font-semibold uppercase tracking-wider whitespace-nowrap" style="color:var(--t-muted)">Active</span>
              <span class="text-[0.7rem] font-bold tabular-nums whitespace-nowrap" style="color:var(--t-ink)">{val('active')}</span>
            </div>
            <div class="flex flex-col justify-center gap-0.5">
              <span class="text-[0.65rem] font-semibold uppercase tracking-wider whitespace-nowrap" style="color:var(--t-muted)">Inactive</span>
              <span class="text-[0.7rem] font-bold tabular-nums whitespace-nowrap" style="color:var(--t-ink)">{val('inactive')}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- Primary navigation -->
      <nav
        aria-label="Primary"
        class="relative -mx-3 sm:-mx-5 px-3 sm:px-5 border-t flex items-center gap-1 overflow-x-auto"
        style="border-color:var(--t-border);scrollbar-width:none"
      >
        <a href="/" bind:this={searchRef} aria-current={activeTab === 'search' ? 'page' : undefined} class={TAB_CLASS} style={tabStyle(activeTab === 'search')}>Search</a>
        <span aria-hidden="true" class={TAB_DIVIDER} style="color:var(--t-border-soft)">/</span>
        <a href="/notice" bind:this={noticeRef} aria-current={activeTab === 'notice' ? 'page' : undefined} class={TAB_CLASS} style={tabStyle(activeTab === 'notice')}>Notices</a>
        <span aria-hidden="true" class={TAB_DIVIDER} style="color:var(--t-border-soft)">/</span>
        <a href="/dispatch" bind:this={dispatchRef} aria-current={activeTab === 'dispatch' ? 'page' : undefined} class={TAB_CLASS} style={tabStyle(activeTab === 'dispatch')}>Dispatch List</a>
        {#if slider.ready}
          <div
            aria-hidden="true"
            style="position:absolute;bottom:0;left:0;height:2px;border-radius:1px;background:#00cc66;transition:transform 0.25s ease-out,width 0.25s ease-out;will-change:transform,width;transform:translateX({slider.left}px);width:{slider.width}px"
          ></div>
        {/if}
      </nav>
    </div>
  </header>

  <main id="main-content" class="flex-1 w-full px-3 sm:px-5 pt-2 pb-4 sm:pb-20 lg:pb-24">
    {@render children()}
  </main>

  <footer
    class="relative mt-6 sm:mt-0 sm:fixed sm:bottom-0 w-full border-t text-[0.7rem] leading-snug"
    style="background:var(--t-bg);border-color:var(--t-border);color:var(--t-muted);padding-bottom:calc(0.4rem + env(safe-area-inset-bottom, 0px))"
  >
    <div class="w-full px-3 sm:px-5 py-1.5 flex flex-col sm:flex-row sm:items-start justify-between gap-1.5 sm:gap-6">
      <span class="flex-1" style="text-wrap:pretty">
        <span class="inline-flex items-center gap-1.5 font-bold uppercase tracking-wide align-baseline" style="color:var(--t-ink)">
          <span class="h-1.5 w-1.5 rounded-full shrink-0" style="background:#ef4444"></span>Disclaimer
        </span>
        — This is an unofficial, third-party tool not affiliated with TGPC or any government body. Data is for reference only — verify all information from official sources before use. Users assume all risk. No warranty as to accuracy, completeness, or timeliness. No liability for errors, omissions, or actions taken based on this content. Operated under fair dealing (Indian Copyright Act, 1957, Section 52).
      </span>
      <span class="whitespace-nowrap font-semibold shrink-0" style="color:var(--t-ink)">TGPC RPh Index &copy; {new Date().getFullYear()}</span>
    </div>
  </footer>
</div>
