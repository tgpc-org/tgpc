<script lang="ts">
  import type { PharmacistRecord, CategoryFilter } from '$lib/types';
  import { searchRecords, searchWithRefiners, getRecord, type AdvancedFilters } from '$lib/api';
  import { parseDDMonYYYY, formatDDMonYYYY } from '$lib/dates';
  import { PUBLIC_R2_PHOTO_BASE } from '$env/static/public';
  import { CATEGORIES as CAT_NAMES } from '$lib/colors';
  import { MAX_SEARCH_RESULTS, isTruncated } from '$lib/searchLimits';
  import { fly } from 'svelte/transition';
  import { page } from '$app/stores';
  import { afterNavigate } from '$app/navigation';
  import { parseSearchUrl, buildSearchQuery, type SearchUrlState } from '$lib/searchUrl';
  import { prefersReducedMotion } from '$lib/motion';
  import { exportCSV, exportPDF, type ExportContext } from '$lib/exporters';

  import ProfileSidebar from '$lib/components/ProfileSidebar.svelte';
  import SearchForm from '$lib/components/SearchForm.svelte';
  import Refiners from '$lib/components/Refiners.svelte';
  import ResultsTable from '$lib/components/ResultsTable.svelte';
  import MobileCards from '$lib/components/MobileCards.svelte';

  const CATEGORY_FILTERS: CategoryFilter[] = ['all', ...CAT_NAMES];
  type SortKey = 'rank' | 'registration_number' | 'name' | 'category' | 'validity_date' | 'status';
  type ColumnKey = Exclude<SortKey, 'rank'>;

  // ---- Search state (mirrored to the URL) ---------------------------------
  // The URL is the source of truth on entry (shared links, refresh,
  // back/forward); interactions mutate this state and writeUrl() pushes the
  // canonical form back out with history.replaceState.
  let query = $state('');
  let category = $state<CategoryFilter>('all');
  let advFilters = $state<AdvancedFilters>({ valid_till: '' });
  let sortKey = $state<SortKey>('rank');
  let sortDir = $state<1 | -1>(1);

  let loading = $state(false);
  let searched = $state(false);
  let results = $state<PharmacistRecord[]>([]);
  let refinersOpen = $state(false);

  // Fixed widths keep the loading skeleton stable between renders (it used to
  // use Math.random(), so every render reshuffled the bars).
  const SKELETON_WIDTHS = [92, 68, 80, 55, 74, 61, 88, 70];
  const MOBILE_SKELETON_WIDTHS = [86, 72, 90, 60, 78];

  function hasAnyRefiner(): boolean {
    return (advFilters.name ?? '').trim() !== '' || (advFilters.father_name ?? '').trim() !== '' || (advFilters.registration_number ?? '').trim() !== ''
      || (advFilters.gender ?? '') !== '' || (advFilters.status ?? '') !== '' || (advFilters.valid_till ?? '') !== '';
  }

  let refinersActive = $derived(hasAnyRefiner());

  // ---- URL <-> state ------------------------------------------------------
  function syncFromUrl(sp: URLSearchParams) {
    const s = parseSearchUrl(sp);
    query = s.q;
    category = s.cat as CategoryFilter;
    advFilters = {
      registration_number: s.rpc || undefined,
      name: s.name || undefined,
      father_name: s.father || undefined,
      gender: s.gender,
      status: s.status,
      valid_till: s.valid
    };
    sortKey = s.sort;
    sortDir = s.dir === 'desc' ? -1 : 1;
    if (query.trim().length >= 3 || hasAnyRefiner()) {
      suppressDebounce++;
      void doSearch();
    }
  }

  // Our own replaceState writes are skipped (lastPushedQs) so syncing state
  // out to the URL never re-enters here and re-triggers a search.
  let lastPushedQs: string | null = null;
  let firstNav = true;
  // URL writes are blocked until the first navigation sync has run — the
  // initial effect flush must not canonicalize an entry URL like /?q=ram to
  // bare / while state still holds defaults. $state so that flipping it
  // re-runs the writeUrl effect: a state change that lands entirely within
  // this window (e.g. typing before hydration finishes) would otherwise
  // never trigger a URL write, since nothing else changes afterwards.
  let navSynced = $state(false);

  afterNavigate(() => {
    const qs = $page.url.search.replace(/^\?/, '');
    const echoed = qs === lastPushedQs;
    const isFirst = firstNav;
    firstNav = false;
    navSynced = true;
    if (echoed) return; // our own replaceState echoed back — nothing to sync
    if (isFirst && !qs) return; // initial load: pre-hydration typing already merged into state
    syncFromUrl(new URLSearchParams($page.url.search));
  });

  // Keep the URL canonical: every state change rewrites ?q etc with
  // replaceState (no history spam; share/refresh/back still work). The loop
  // terminates because parse(build(state)) === state.
  function writeUrl() {
    // Read all tracked state BEFORE the navSynced guard: a Svelte 5 effect
    // that returns before reading anything collects no dependencies and
    // would never re-run when the state later changes.
    const s: SearchUrlState = {
      q: query.trim(),
      cat: category,
      sort: sortKey,
      dir: sortDir === -1 ? 'desc' : 'asc',
      rpc: (advFilters.registration_number || '').trim(),
      name: (advFilters.name || '').trim(),
      father: (advFilters.father_name || '').trim(),
      gender: (advFilters.gender || '') as SearchUrlState['gender'],
      status: (advFilters.status || '') as SearchUrlState['status'],
      valid: advFilters.valid_till || ''
    };
    if (!navSynced) return;
    const qs = buildSearchQuery(s).toString();
    // location.search, not $page.url: our own replaceState can leave the
    // SvelteKit page store stale, which would make this comparison lie.
    const current = location.search.replace(/^\?/, '');
    if (qs === current) return;
    lastPushedQs = qs;
    // history.state is the deserialized (plain) history entry — passing
    // $page.state here would throw DataCloneError (reactive proxies can't
    // be structured-cloned) and kill the render flush on every keystroke.
    history.replaceState(history.state, '', qs ? `/?${qs}` : '/');
    // The profile page's back button restores this when there's no history
    // to go back to (direct/shared links).
    if (qs) { try { sessionStorage.setItem('tgpc_last_search', qs); } catch { /* private mode */ } }
  }

  $effect(() => {
    writeUrl();
  });

  // ---- Fetching -----------------------------------------------------------
  let searchSeq = 0;

  async function doSearch() {
    const q = query.trim();
    const useRefiners = hasAnyRefiner();
    if (q.length < 3 && !useRefiners) return;
    const mySeq = ++searchSeq;
    loading = true;
    searched = true;
    try {
      // Refiners filter server-side so results are never silently truncated
      // to the fetched slice; plain queries keep the ranked RPC path.
      // Never throws outward — a failed fetch shows empty, not an error page.
      let res: PharmacistRecord[] = [];
      try {
        res = useRefiners ? await searchWithRefiners(query, advFilters) : await searchRecords(query);
      } catch {
        res = [];
      }
      if (mySeq !== searchSeq) return; // superseded by newer keystroke
      results = res;
      category = 'all';
    } finally {
      // Only the latest request clears loading. A superseded request does
      // nothing — the in-flight/superseding request (or the debounced
      // effect) owns the next fetch, so chaining here would duplicate it.
      if (mySeq === searchSeq) loading = false;
    }
  }

  // Debounced typeahead — 300ms after typing, q>=3 (or refiner-only search).
  let debounceTimer: ReturnType<typeof setTimeout> | undefined;
  // URL hydration searches immediately; without this counter the debounce
  // effect would fire a second, identical fetch 300ms later.
  let suppressDebounce = 0;
  $effect(() => {
    if (suppressDebounce > 0) { suppressDebounce--; return; }
    const q = query.trim();
    // hasAnyRefiner() reads every refiner field, so this effect re-runs on
    // any refiner edit and re-searches (debounced) even with a short query.
    const refinersOn = hasAnyRefiner();
    clearTimeout(debounceTimer);
    if (q.length < 3 && !refinersOn) return;
    debounceTimer = setTimeout(() => { void doSearch(); }, 300);
    return () => clearTimeout(debounceTimer);
  });

  $effect(() => {
    if (query.trim() === '' && searched && !hasAnyRefiner()) {
      searched = false;
      results = [];
      category = 'all';
    }
  });

  function reset() {
    query = '';
    category = 'all';
    results = [];
    searched = false;
    sortKey = 'rank';
    sortDir = 1;
    advFilters = { valid_till: '' };
  }

  function clearAdvanced() {
    advFilters = { valid_till: '' };
  }

  // ---- Filtering + sorting (client-side over the fetched slice) -----------
  // A full page of results means the fetch stopped at MAX_SEARCH_RESULTS, so
  // more matches may exist than are displayed — the header says so.
  let capped = $derived(isTruncated(results.length));

  let filtered = $derived.by(() => {
    let base = category === 'all' ? results : results.filter((r) => r.category === category);
    if (advFilters.name?.trim()) {
      const q = advFilters.name.trim().toLowerCase();
      base = base.filter((r) => r.name.toLowerCase().includes(q));
    }
    if (advFilters.father_name?.trim()) {
      const q = advFilters.father_name.trim().toLowerCase();
      base = base.filter((r) => (r.father_name || '').toLowerCase().includes(q));
    }
    if (advFilters.registration_number?.trim()) {
      const q = advFilters.registration_number.trim().toLowerCase();
      base = base.filter((r) => r.registration_number.toLowerCase().startsWith(q));
    }
    if (advFilters.gender && advFilters.gender !== '') base = base.filter((r) => r.gender === advFilters.gender);
    if (advFilters.status && advFilters.status !== '') base = base.filter((r) => r.status === advFilters.status);
    if (advFilters.valid_till?.trim()) {
      const dbDate = formatDDMonYYYY(advFilters.valid_till);
      if (dbDate) base = base.filter((r) => r.validity_date === dbDate);
    }
    return base;
  });

  const COLLATOR = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

  /** `validity_date` ("07-Mar-2028") as a timestamp, or null when unusable. */
  function validityTime(v: string | null | undefined): number | null {
    if (!v) return null;
    const parsed = parseDDMonYYYY(v);
    if (!parsed) return null;
    return Date.UTC(parsed.year, parsed.monthIndex, parsed.day);
  }

  function compareBy(key: ColumnKey, a: PharmacistRecord, b: PharmacistRecord): number {
    switch (key) {
      case 'registration_number': return COLLATOR.compare(a.registration_number, b.registration_number);
      case 'name': return COLLATOR.compare(a.name, b.name);
      case 'category': return COLLATOR.compare(a.category, b.category);
      case 'status': return COLLATOR.compare(a.status || '', b.status || '');
      case 'validity_date': {
        const ta = validityTime(a.validity_date);
        const tb = validityTime(b.validity_date);
        if (ta === null || tb === null) return ta === tb ? 0 : ta === null ? 1 : -1;
        return ta - tb;
      }
    }
  }

  let sorted = $derived.by(() => {
    const key = sortKey;
    if (key === 'rank') return filtered;
    const dir = sortDir;
    return [...filtered].sort((a, b) => {
      const cmp = compareBy(key, a, b);
      // Rows without a usable date stay at the bottom in both directions.
      if (key === 'validity_date' && cmp !== 0) {
        const missingA = validityTime(a.validity_date) === null;
        const missingB = validityTime(b.validity_date) === null;
        if (missingA !== missingB) return missingA ? 1 : -1;
      }
      return dir * cmp;
    });
  });

  function toggleSort(key: ColumnKey) {
    if (sortKey === key) sortDir = sortDir === 1 ? -1 : 1;
    else { sortKey = key; sortDir = 1; }
  }

  let categoryCounts = $derived.by(() => {
    const m: Record<string, number> = { all: results.length };
    for (const c of CAT_NAMES) m[c] = 0;
    for (const r of results) m[r.category] = (m[r.category] || 0) + 1;
    return m;
  });

  // Brand fills carry ink text: white on brand green is only 2.1:1.
  function chipStyle(cat: CategoryFilter): string {
    if (cat !== category) return 'background:var(--t-surface);color:var(--t-ink-soft)';
    return 'background:#00cc66;color:var(--t-ink)';
  }

  // ---- Empty landing state -------------------------------------------------
  // The first visit used to be a bare search input over ~450px of blank
  // space. These starter chips carry verified live examples so every tap
  // lands on results; a missed tap falls through to the existing actionable
  // "No results" state. Kept an <h2> so smoke.spec.ts's single-h1 rule holds.
  const STARTER_SEARCHES = ['reddy', 'sharma', 'kumar', 'TG061874'];

  // ---- Exports ------------------------------------------------------------
  function exportContext(): ExportContext {
    const filters: string[] = [];
    if (category !== 'all') filters.push(`Category: ${category}`);
    if (advFilters.registration_number?.trim()) filters.push(`RPC: ${advFilters.registration_number.trim()}`);
    if (advFilters.name?.trim()) filters.push(`Name: ${advFilters.name.trim()}`);
    if (advFilters.father_name?.trim()) filters.push(`Father: ${advFilters.father_name.trim()}`);
    if (advFilters.gender) filters.push(`Gender: ${advFilters.gender}`);
    if (advFilters.status) filters.push(`Status: ${advFilters.status}`);
    if (advFilters.valid_till) filters.push(`Valid Till: ${advFilters.valid_till}`);
    return {
      rows: sorted,
      filteredCount: filtered.length,
      totalCount: results.length,
      isFiltered: refinersActive || category !== 'all',
      query: query.trim(),
      filters
    };
  }

  // ---- Render exactly one results list ------------------------------------
  // Previously the desktop table AND the mobile cards were both mounted for
  // every row (toggled by CSS only), which doubled DOM node count on phones.
  // Results only exist after a client search, so this is never part of SSR
  // output and there is no hydration mismatch — the media query is correct
  // before the first rows render.
  let isDesktop = $state(true);
  $effect(() => {
    const mq = window.matchMedia('(min-width: 768px)');
    const update = () => { isDesktop = mq.matches; };
    update();
    mq.addEventListener('change', update);
    return () => mq.removeEventListener('change', update);
  });

  // ---- Status announcements (screen readers) ------------------------------
  let announcement = $state('');
  $effect(() => {
    if (loading) announcement = 'Searching…';
    else if (searched) announcement = `${sorted.length} result${sorted.length === 1 ? '' : 's'}${capped ? ` (first ${MAX_SEARCH_RESULTS} shown)` : ''}`;
  });

  // ---- Drawer -------------------------------------------------------------
  let drawerOpen = $state(false);
  let drawerReg = $state<string | null>(null);
  let drawerRecord = $state<PharmacistRecord | null>(null);
  let drawerLoading = $state(false);
  let drawerError = $state<string | null>(null);
  let drawerPhoto = $derived(drawerRecord ? (drawerRecord.photo_url || `${PUBLIC_R2_PHOTO_BASE}/${drawerRecord.registration_number}.webp`) : '');

  let drawerSeq = 0;

  async function openDrawer(reg: string) {
    const clean = reg.trim().toUpperCase();
    if (drawerOpen && drawerReg === clean) { closeDrawer(); return; }
    const mySeq = ++drawerSeq;
    drawerReg = clean;
    drawerOpen = true;
    drawerLoading = true;
    drawerError = null;
    drawerRecord = null;
    try {
      const rec = await getRecord(clean);
      if (mySeq !== drawerSeq) return; // user clicked another profile meanwhile
      if (!rec) { drawerError = `No record found for ${clean}`; }
      else drawerRecord = rec;
    } catch {
      if (mySeq !== drawerSeq) return;
      drawerError = 'Failed to load profile';
    } finally {
      if (mySeq === drawerSeq) drawerLoading = false;
    }
  }

  function closeDrawer() { drawerSeq++; drawerOpen = false; drawerReg = null; }

  // "/" anywhere focuses the search field (ignored while typing in a field).
  function onWindowKeydown(e: KeyboardEvent) {
    if (e.key !== '/' || e.ctrlKey || e.metaKey || e.altKey) return;
    const el = document.activeElement as HTMLElement | null;
    if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement || el?.isContentEditable) return;
    const input = document.getElementById('tgpc-search') as HTMLInputElement | null;
    if (input) { e.preventDefault(); input.focus(); input.select(); }
  }
</script>

<svelte:window onkeydown={onWindowKeydown} />

<div class="space-y-2">
  <h1 class="sr-only">Search Telangana State Pharmacy Council pharmacist records by name or RPC number</h1>

  <SearchForm bind:query bind:searched onsearch={doSearch} onreset={reset} />

  {#if !searched}
    <!-- First-visit empty state: show the way in, not a blank page. -->
    <div class="py-8 sm:py-12 text-center space-y-4">
      <h2 class="text-[1.1rem] font-bold" style="color:var(--t-ink)">Find a registered pharmacist</h2>
      <p class="text-[0.85rem] max-w-md mx-auto" style="color:var(--t-muted)">
        Search the registry by name, or by the RPC number printed on the certificate (e.g. <span class="tabular-nums">TG061874</span>).
      </p>
      <div class="flex flex-wrap justify-center gap-2">
        {#each STARTER_SEARCHES as s (s)}
          <button
            onclick={() => { query = s; void doSearch(); }}
            class="px-3 py-1.5 rounded-full text-[0.75rem] font-semibold cursor-pointer border-none transition-colors tabular-nums hover:bg-[rgba(0,204,102,0.14)]"
            style="background:var(--t-surface-3);color:var(--t-ink-soft)"
          >
            {s}
          </button>
        {/each}
      </div>
      <p class="text-[0.75rem]">
        <a href="/notice" class="underline underline-offset-2" style="color:var(--t-link)">Browse the latest council notices</a>
      </p>
    </div>
  {/if}

  <!-- Result count, category chips, exports -->
  {#if searched}
    <div class="flex flex-wrap items-center gap-1 min-w-0 w-full sm:w-auto sm:flex-1" transition:fly={{ y: 6, duration: prefersReducedMotion() ? 0 : 200, opacity: 0 }}>
      <span class="text-[0.75rem] tabular-nums flex-shrink-0" style="color:var(--t-muted)">{sorted.length.toLocaleString()} results</span>
      {#if capped}
        <span class="text-[0.75rem] tabular-nums flex-shrink-0" style="color:var(--t-muted)">· first {MAX_SEARCH_RESULTS} matches shown — narrow your search</span>
      {/if}
      {#if refinersActive}
        <span class="text-[0.65rem] font-semibold uppercase rounded px-1.5 py-0.5 flex-shrink-0" style="background:rgba(0,204,102,0.14);color:var(--t-ink)">Filtered</span>
      {/if}
      <span class="-mx-1 px-1 flex-nowrap overflow-x-auto sm:mx-0 sm:px-0 sm:flex-wrap sm:ml-auto flex items-center gap-1.5" style="scrollbar-width:thin;scrollbar-color:var(--t-border) transparent;-webkit-overflow-scrolling:touch">
        {#each CATEGORY_FILTERS as cat (cat)}
          <button onclick={() => { category = cat; }}
            class="px-2.5 py-1 rounded text-[0.7rem] font-medium transition-all cursor-pointer border-none whitespace-nowrap"
            style={chipStyle(cat)}>
            {cat === 'all' ? 'All' : cat} ({(categoryCounts[cat] || 0).toLocaleString()})
          </button>
        {/each}
        <button onclick={() => exportCSV(exportContext())} class="flex items-center gap-1 px-2.5 py-1.5 rounded text-[0.7rem] font-semibold cursor-pointer border-none transition-colors whitespace-nowrap" style="background:rgba(0,204,102,0.14);color:var(--t-ink)">EXPORT CSV</button>
        <button onclick={() => void exportPDF(exportContext())} class="flex items-center gap-1 px-2.5 py-1.5 rounded text-[0.7rem] font-semibold cursor-pointer border-none transition-colors whitespace-nowrap" style="background:rgba(239,68,68,0.12);color:var(--t-ink)">EXPORT PDF</button>
      </span>
    </div>
  {/if}

  <p id="search-status" aria-live="polite" class="sr-only">{announcement}</p>

  <!-- Results -->
  {#if searched}
    <div transition:fly={{ y: 10, duration: prefersReducedMotion() ? 0 : 250, opacity: 0 }}>
      {#if loading}
        <!-- Skeletons mirror each list type so switching widths doesn't jump. -->
        <div class="md:hidden space-y-2" aria-hidden="true">
          {#each [0, 1, 2] as card (card)}
            <div class="p-3 rounded-xl border border-[var(--t-surface)] bg-[var(--t-surface-3)] space-y-2">
              {#each MOBILE_SKELETON_WIDTHS as w, i (i)}
                <div class="h-3 bg-[var(--t-surface)] rounded animate-pulse" style="width:{w}%"></div>
              {/each}
            </div>
          {/each}
        </div>
        <div class="hidden md:block space-y-3 py-4" aria-hidden="true">
          {#each SKELETON_WIDTHS as w, i (i)}
            <div class="h-4 bg-[var(--t-surface)] rounded animate-pulse" style="width:{w}%"></div>
          {/each}
        </div>
      {:else}
        {#if results.length > 0}
          <Refiners bind:filters={advFilters} bind:open={refinersOpen} active={refinersActive} onclear={clearAdvanced} />
        {/if}
        {#if filtered.length === 0}
          <!-- Actionable empty state: say what to try next instead of a bare
               "No results". -->
          <div class="py-10 text-center space-y-3">
            <!-- 'No results' stays its own node so e2e getByText('No results',
                 { exact: true }) keeps matching. -->
            <p class="text-[0.95rem] font-semibold" style="color:var(--t-ink)"><span>No results</span>{#if query.trim()}<span> for “{query.trim()}”</span>{/if}</p>
            <ul class="text-[0.8rem] space-y-1" style="color:var(--t-muted)">
              <li>Check the spelling, or try fewer letters</li>
              <li>Searching by RPC number starts with <span class="tabular-nums">TG…</span></li>
              {#if refinersActive || category !== 'all'}<li>Filters are active — they narrow every search</li>{/if}
            </ul>
            {#if refinersActive || category !== 'all'}
              <button onclick={clearAdvanced}
                class="rounded-full border px-3 py-1.5 text-[0.75rem] font-semibold transition-colors hover:bg-[rgba(239,68,68,0.12)]"
                style="border-color:rgba(239,68,68,0.35);color:var(--t-ink)">
                Clear filters
              </button>
            {/if}
          </div>
        {:else if isDesktop}
          <ResultsTable rows={sorted} sortKey={sortKey} sortDir={sortDir} onsort={toggleSort} onopen={openDrawer} />
        {:else}
          <MobileCards rows={sorted} onopen={openDrawer} />
        {/if}
      {/if}
    </div>
  {/if}

  <ProfileSidebar open={drawerOpen} record={drawerRecord} photo={drawerPhoto} loading={drawerLoading} error={drawerError} onClose={closeDrawer} />
</div>
