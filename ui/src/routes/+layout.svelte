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
  import { PUBLIC_SUPABASE_URL } from '$env/static/public';
  import { R2_ORIGIN } from '$lib/r2';
  import { setCache } from '$lib/cache';
  import { initTheme, themeName, toggleTheme } from '$lib/theme';

  import BrandLockup from '$lib/components/BrandLockup.svelte';
  import StatusPill from '$lib/components/StatusPill.svelte';
  import StatsBar from '$lib/components/StatsBar.svelte';
  import NavTabs from '$lib/components/NavTabs.svelte';

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

  let activeTab = $derived($page.url.pathname === '/' ? 'search' : $page.url.pathname === '/notice' ? 'notice' : $page.url.pathname === '/dispatch' ? 'dispatch' : '');
</script>
<div class="min-h-screen flex flex-col" style="background:var(--t-bg)">
  <a href="#main-content" class="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[100] focus:rounded-lg focus:px-3 focus:py-2 focus:font-bold focus:no-underline" style="background:var(--t-surface-2);color:#00cc66">Skip to main content</a>
  <header class="sticky top-0 z-50" style="background:var(--t-bg);box-shadow:var(--shadow-sm)">
    <div class="w-full px-4 sm:px-6 py-2.5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-4">
      <div class="flex flex-col gap-1">
        <BrandLockup />
        <div class="flex items-center gap-2 text-[0.7rem] w-full">
          <StatusPill status={status} />
        </div>
      </div>
      <StatsBar stats={stats} lastSync={lastSync} />
    </div>
    <NavTabs activeTab={activeTab} isDark={$themeName === 'dark'} onToggleTheme={() => toggleTheme()} />
  </header>

  <main id="main-content" class="flex-1 w-full px-4 sm:px-6 pt-1 pb-4 sm:pb-16">
    {@render children()}
  </main>

  <footer class="relative mt-4 sm:mt-0 sm:fixed sm:bottom-0 w-full border-t py-1 text-[0.5rem] leading-tight"
          style="background:var(--t-bg);border-color:var(--t-border);color:#9ca3af;padding-bottom:calc(0.25rem + env(safe-area-inset-bottom, 0px))">
    <div class="w-full px-4 sm:px-6 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-1 sm:gap-4">
      <span class="text-left flex-1 sm:pr-4" style="text-wrap:balance"><span style="color:#ef4444">DISCLAIMER:</span> This is an unofficial, third-party tool not affiliated with TGPC or any government body. Data is for reference only — verify all information from official sources before use. Users assume all risk.<br>No warranty as to accuracy, completeness, or timeliness. No liability for errors, omissions, or actions taken based on this content. Operated under fair dealing (Indian Copyright Act, 1957, Section 52).</span>
      <span class="text-left sm:text-right whitespace-nowrap font-semibold flex-shrink-0 text-[0.7rem]">TGPC RPh Index &copy; {new Date().getFullYear()}</span>
    </div>
  </footer>
</div>

<style>
</style>
