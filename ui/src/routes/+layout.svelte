<svelte:head>
  <title>TGPC RPh Index</title>
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

  let statusConfig = $derived.by(() => ({
    Live: { bg: 'rgba(0,204,102,0.05)', border: 'rgba(0,204,102,0.35)', text: '#00b359', dot: '#00cc66' },
    Busy: { bg: 'rgba(239,68,68,0.05)', border: 'rgba(239,68,68,0.35)', text: '#ef4444', dot: '#ef4444' },
    Offline: { bg: 'rgba(239,68,68,0.05)', border: 'rgba(239,68,68,0.35)', text: '#ef4444', dot: '#ef4444' }
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

  let activeTab = $derived($page.url.pathname === '/' ? 'search' : $page.url.pathname === '/notice' ? 'notice' : $page.url.pathname === '/dispatch' ? 'dispatch' : '');

  let searchRef: HTMLAnchorElement | undefined;
  let noticeRef: HTMLAnchorElement | undefined;
  let dispatchRef: HTMLAnchorElement | undefined;
  let sliderStyle = $state('');

  $effect(() => {
    const tab = activeTab;
    if (!tab) { sliderStyle = ''; return; }
    let el = tab === 'search' ? searchRef : tab === 'notice' ? noticeRef : dispatchRef;
    if (el) {
      sliderStyle = `transform:translateX(${el.offsetLeft}px);width:${el.offsetWidth}px`;
    }
  });
</script>
<div class="min-h-screen flex flex-col" style="background:var(--t-bg)">
  <header class="sticky top-0 z-50" style="background:var(--t-bg)">
    <div class="w-full px-4 sm:px-6 py-2.5 flex items-center justify-between gap-4">
      <div class="flex flex-col">
          <div style="display:table;width:0">
            <a href="/" class="no-underline" style="display:table-row;white-space:nowrap;width:1px">
              <span class="text-[1.65rem] font-bold tracking-tight inline-flex items-center gap-1" style="color:var(--t-ink);white-space:nowrap">
                <span style="color:#00cc66">TGPC</span><span style="color:#ef4444">RPh</span><span class="text-[#9ca3af]">Index</span>
              </span>
            </a>
            <span class="text-[0.65rem] text-[#9ca3af] font-medium truncate mb-0.5" style="display:table-row;width:100%;white-space:nowrap;overflow:hidden">Open-Source TGPC Pharmacist Data</span>
            <div style="display:table-row;width:100%;white-space:nowrap;overflow:hidden">
              <div class="flex items-center gap-2 text-[0.7rem] w-full">
                <span class="flex w-full items-center justify-center gap-px h-5 px-1.5 rounded-full text-[0.75rem] font-medium box-border overflow-hidden"
                      style="background:{statusConfig.bg};border:1px solid {statusConfig.border};color:{statusConfig.text}">
                  <span class="w-1.5 h-1.5 rounded-full flex-shrink-0" style="background:{statusConfig.dot}"></span>
                  <span class="text-[10px] font-medium leading-[18px] inline-block w-[28px] text-center">{status}</span>
                  {#if status !== 'Offline'}
                  <Clock/>
                  {/if}
                </span>
              </div>
            </div>
          </div>
      </div>
      <div style="background:var(--t-surface-2);border:1px solid var(--t-border);border-radius:8px;padding:6px 10px 4px 10px;display:flex;flex-direction:column;gap:0;min-width:0;max-width:100%">
        <div style="display:flex;flex-wrap:wrap;gap:10px 12px;align-items:center;justify-content:center">
          <div style="border-right:1px solid var(--t-border);padding-right:12px">
              <div style="display:flex;flex-direction:column;gap:4px;text-align:center">
              <div style="font-size:0.8rem;font-weight:500;letter-spacing:0.5px;color:#9ca3af">TOTAL <span style="color:#ef4444">RPh</span></div>
              <div style="font-size:1.25rem;font-weight:700;color:var(--t-ink);line-height:1;font-variant-numeric:tabular-nums">{val('total')}</div>
            </div>
          </div>
          {#each sortedCategories as cat, i (cat)}
            <div style="border-right:{i < 5 ? '1px solid var(--t-border)' : 'none'};padding-right:{i < 5 ? '12px' : '0'}">
              <div style="display:flex;flex-direction:column;gap:4px;text-align:center">
                <div style="font-size:0.8rem;font-weight:500;letter-spacing:0.5px;color:{CATEGORY_COLORS[cat] === '#111827' ? 'var(--t-ink)' : CATEGORY_COLORS[cat]}">{cat}</div>
                <div style="font-size:1.25rem;font-weight:700;color:var(--t-ink);line-height:1;font-variant-numeric:tabular-nums">{val(CATEGORY_KEYS[CATEGORIES.indexOf(cat)] as keyof Stats)}</div>
              </div>
            </div>
          {/each}
        </div>
              <div style="font-size:0.5rem;color:#9ca3af;font-weight:500;letter-spacing:0.3px;text-transform:uppercase;margin-top:4px;padding-top:4px;border-top:1px solid var(--t-border);display:flex;align-items:center;gap:6px;flex-wrap:wrap">
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
    </div>
    <div class="w-full px-4 sm:px-6 border-b" style="display:flex;align-items:center;gap:2px;font-size:0.7rem;padding-top:3px;padding-bottom:3px;overflow-x:auto;position:relative;border-color:var(--t-border)">
      <a href="/" bind:this={searchRef} style="text-decoration:none;padding:2px 4px;font-weight:700;color:{activeTab === 'search' ? '#00cc66' : 'var(--t-muted)'};white-space:nowrap">SEARCH</a>
      <span style="color:var(--t-border);font-weight:300;padding:0 2px;user-select:none">/</span>
      <a href="/notice" bind:this={noticeRef} style="text-decoration:none;padding:2px 4px;font-weight:700;color:{activeTab === 'notice' ? '#00cc66' : 'var(--t-muted)'};white-space:nowrap">NOTICES</a>
      <span style="color:var(--t-border);font-weight:300;padding:0 2px;user-select:none">/</span>
      <a href="/dispatch" bind:this={dispatchRef} style="text-decoration:none;padding:2px 4px;font-weight:700;color:{activeTab === 'dispatch' ? '#00cc66' : 'var(--t-muted)'};white-space:nowrap">DISPATCH LIST</a>
      <button
        onclick={() => toggleTheme()}
        aria-pressed={$themeName === 'dark'}
        aria-label="Toggle day and night mode"
        title={$themeName === 'dark' ? 'Switch to day mode' : 'Switch to night mode'}
        style="margin-left:auto;flex-shrink:0;display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;border:1px solid var(--t-border);background:var(--t-surface-2);color:var(--t-muted);cursor:pointer"
      >
        {#if $themeName === 'dark'}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
        {:else}
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
        {/if}
      </button>
      {#if sliderStyle}
      <div style="position:absolute;bottom:0;left:0;height:2px;background:#00cc66;border-radius:1px;transition:transform 0.25s ease-out,width 0.25s ease-out;will-change:transform,width;{sliderStyle}"></div>
      {/if}
    </div>
  </header>

  <main class="flex-1 w-full px-4 sm:px-6 pt-1 pb-9">
    {@render children()}
  </main>

  <footer class="fixed bottom-0 w-full border-t py-1 text-[0.5rem] leading-tight"
          style="background:var(--t-bg);border-color:var(--t-border);color:#9ca3af;padding-bottom:calc(0.25rem + env(safe-area-inset-bottom, 0px))">
    <div class="w-full px-4 sm:px-6 flex items-center justify-between gap-4">
      <span class="text-left flex-1 pr-4" style="text-wrap:balance"><span style="color:#ef4444">DISCLAIMER:</span> This is an unofficial, third-party tool not affiliated with TGPC or any government body. Data is for reference only — verify all information from official sources before use. Users assume all risk.<br>No warranty as to accuracy, completeness, or timeliness. No liability for errors, omissions, or actions taken based on this content. Operated under fair dealing (Indian Copyright Act, 1957, Section 52).</span>
      <span class="text-right whitespace-nowrap font-semibold flex-shrink-0 text-[0.7rem]">TGPC RPh Index &copy; {new Date().getFullYear()}</span>
    </div>
  </footer>
</div>

<style>
</style>
