<script lang="ts">
  import type { PharmacistRecord, CategoryFilter } from '$lib/types';
  import { searchRecords, searchWithRefiners, type AdvancedFilters } from '$lib/api';
  import DatePicker from '$lib/DatePicker.svelte';
  import { CATEGORY_COLORS, CATEGORIES as CAT_NAMES } from '$lib/colors';
  import { MAX_SEARCH_RESULTS, isTruncated } from '$lib/searchLimits';
  import ProfileSidebar from '$lib/components/ProfileSidebar.svelte';
  import { getRecord } from '$lib/api';
  import { PUBLIC_R2_PHOTO_BASE } from '$env/static/public';
  import { fly } from 'svelte/transition';

  function photoUrl(r: PharmacistRecord): string {
    return r.photo_url || `${PUBLIC_R2_PHOTO_BASE}/${r.registration_number}.webp`;
  }

  let query = $state('');
  let category = $state<CategoryFilter>('all');
  let loading = $state(false);
  let results = $state<PharmacistRecord[]>([]);
  let searched = $state(false);
  const CATEGORY_FILTERS: CategoryFilter[] = ['all', ...CAT_NAMES];

  // A full page of results means the fetch stopped at MAX_SEARCH_RESULTS, so
  // more matches may exist than are displayed — the header says so.
  let capped = $derived(isTruncated(results.length));

  let advFilters = $state<AdvancedFilters>({ valid_till: '' });
  let refinersOpen = $state(false);

  function hasAnyRefiner(): boolean {
    return (advFilters.name ?? '').trim() !== '' || (advFilters.father_name ?? '').trim() !== '' || (advFilters.registration_number ?? '').trim() !== ''
      || (advFilters.gender ?? '') !== '' || (advFilters.status ?? '') !== '' || (advFilters.valid_till ?? '') !== '';
  }

  let refinersActive = $derived(hasAnyRefiner());

  const REV_MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  function formatValidTillForDisplay(iso: string): string | null {
    const d = new Date(iso + 'T00:00:00');
    if (isNaN(d.getTime())) return null;
    return `${String(d.getDate()).padStart(2,'0')}-${REV_MONTHS[d.getMonth()]}-${d.getFullYear()}`;
  }

  // Result-bound filters — client-side over fetched results (no extra server fetch)
  let filtered = $derived.by(() => {
    let base = category === 'all' ? results : results.filter(r => r.category === category);
    if (advFilters.name?.trim()) {
      const q = advFilters.name.trim().toLowerCase();
      base = base.filter(r => r.name.toLowerCase().includes(q));
    }
    if (advFilters.father_name?.trim()) {
      const q = advFilters.father_name.trim().toLowerCase();
      base = base.filter(r => (r.father_name || '').toLowerCase().includes(q));
    }
    if (advFilters.registration_number?.trim()) {
      const q = advFilters.registration_number.trim().toLowerCase();
      base = base.filter(r => r.registration_number.toLowerCase().startsWith(q));
    }
    if (advFilters.gender && advFilters.gender !== '') base = base.filter(r => r.gender === advFilters.gender);
    if (advFilters.status && advFilters.status !== '') base = base.filter(r => r.status === advFilters.status);
    if (advFilters.valid_till?.trim()) {
      const dbDate = formatValidTillForDisplay(advFilters.valid_till);
      if (dbDate) base = base.filter(r => r.validity_date === dbDate);
    }
    return base;
  });

  let categoryCounts = $derived.by(() => {
    const m: Record<string, number> = { all: results.length };
    for (const c of CAT_NAMES) m[c] = 0;
    for (const r of results) m[r.category] = (m[r.category] || 0) + 1;
    return m;
  });
  let debounceTimer: ReturnType<typeof setTimeout> | undefined;

  // Debounced typeahead — 300ms after typing, q>=3 (or refiner-only search).
  $effect(() => {
    const q = query.trim();
    // hasAnyRefiner() reads every refiner field, so this effect re-runs on
    // any refiner edit and re-searches (debounced) even with a short query.
    const refinersOn = hasAnyRefiner();
    clearTimeout(debounceTimer);
    if (q.length < 3 && !refinersOn) {
      if (q.length === 0 && searched) {
        // handled by clear effect below
      }
      return;
    }
    debounceTimer = setTimeout(() => { doSearch(); }, 300);
    return () => clearTimeout(debounceTimer);
  });

  // Single scrollable list — no cap, show all results.
  let resultsBox = $state<HTMLDivElement | undefined>();
  let resultsMaxH = $state('calc(100vh - 240px)');
  let resultsMinH = $state('0px');

  function measureResultsBox() {
    if (!resultsBox) return;
    const boxTop = resultsBox.getBoundingClientRect().top;
    const footer = document.querySelector('footer');
    const footerTop = footer ? footer.getBoundingClientRect().top : window.innerHeight;
    const available = Math.round(footerTop - boxTop - 10);
    if (available > 0) {
      resultsMaxH = `${available}px`;
      resultsMinH = `${available}px`;
    }
  }

  let resizeObserver: ResizeObserver | undefined;

  $effect(() => {
    if (resultsBox) {
      measureResultsBox();
      resizeObserver?.disconnect();
      resizeObserver = new ResizeObserver(measureResultsBox);
      resizeObserver.observe(resultsBox);
      return () => resizeObserver?.disconnect();
    }
  });

  $effect(() => {
    if (query.trim() === '' && searched && !hasAnyRefiner()) {
      searched = false;
      results = [];
      category = 'all';
    }
  });

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
    }
    finally { if (mySeq === drawerSeq) drawerLoading = false; }
  }
  function closeDrawer() { drawerSeq++; drawerOpen = false; drawerReg = null; }

  function clearAdvanced() {
    advFilters = { valid_till: '' };
  }



  function onSearchKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') doSearch();
  }

  function chipStyle(cat: CategoryFilter): string {
    if (cat !== category) return 'background:var(--t-surface);color:var(--t-muted)';
    return 'background:#00cc66;color:#fff';
  }



  function reset() {
    query = '';
    category = 'all';
    results = [];
    searched = false;
    advFilters = { valid_till: '' };
  }

  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const DAYS = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

  function fmtDate(d: Date) {
    return `${DAYS[d.getDay()]}, ${String(d.getDate()).padStart(2,'0')} ${MONTHS[d.getMonth()]} ${d.getFullYear()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
  }

  function fileDateStr(d: Date) {
    return `${String(d.getDate()).padStart(2,'0')}${String(d.getMonth()+1).padStart(2,'0')}${d.getFullYear()}`;
  }

  // jspdf + autotable + html2canvas (~650KB) load on demand, not in the
  // critical homepage bundle.
  async function exportPDF() {
    if (filtered.length === 0) return;
    const [{ jsPDF }, { default: autoTable }] = await Promise.all([
      import('jspdf'),
      import('jspdf-autotable')
    ]);
    const doc = new jsPDF({ format: 'a4', unit: 'mm' });
    const now = new Date();
    const kw = cleanHeader(query.trim() || '(all)');
    const title = `TGPC RPh Index - Search: ${kw} - ${fmtDate(now)}`;
    const countLine = `Results: ${filtered.length.toLocaleString()} of ${results.length.toLocaleString()}${refinersActive || category !== 'all' ? ' (filtered)' : ''}`;
    const filtParts = refinerParts();
    const filterLine = filtParts.length ? `Filters: ${filtParts.join(' | ')}` : 'Filters: none';
    const body = filtered.map(r => [r.registration_number, r.name, r.father_name || '—', r.gender || '—', r.category, r.validity_date || '—', r.status || '—']);

    const TEXTS = Object.fromEntries(
      Object.entries(CATEGORY_COLORS).map(([k, hex]) => [
        k,
        [parseInt(hex.slice(1, 3), 16), parseInt(hex.slice(3, 5), 16), parseInt(hex.slice(5, 7), 16)]
      ])
    );

    autoTable(doc, {
      startY: 18,
      head: [['RPC NUMBER', 'NAME', 'FATHER NAME', 'GENDER', 'CATEGORY', 'VALID TILL', 'STATUS']],
      body,
      theme: 'striped',
      headStyles: { fillColor: [0, 204, 102], textColor: [255, 255, 255], fontStyle: 'bold', fontSize: 9 },
      bodyStyles: { fontSize: 8, cellPadding: 2 },
      alternateRowStyles: { fillColor: [247, 247, 247] },
      margin: { top: 16, left: 10, right: 10, bottom: 12 },
      tableWidth: 'auto',
      didParseCell: (data) => {
        if (data.section === 'body' && data.column.index === 4) {
          const cat = (data.cell.raw as string);
          const text = TEXTS[cat];
          if (text) data.cell.styles.textColor = text as [number, number, number];
        }
      },
      didDrawPage: (_data) => {
        doc.setFontSize(11);
        doc.setTextColor(0, 204, 102);
        doc.text(title, 10, 10);
        doc.setFontSize(7);
        doc.setTextColor(100, 100, 100);
        doc.text(`${countLine} | ${filterLine}`, 10, 14);
      }
    });
    const total = doc.getNumberOfPages();
    for (let i = 1; i <= total; i++) {
      doc.setPage(i);
      doc.setFontSize(8);
      doc.setTextColor(150, 150, 150);
      const ph = doc.internal.pageSize.height;
      doc.text('TGPC RPh Index - Open-Source Pharmacist Data', 10, ph - 10);
      doc.text('tgpc.pages.dev', doc.internal.pageSize.width / 2, ph - 10, { align: 'center' });
      doc.text(`Page ${i} / ${total}`, doc.internal.pageSize.width - 10, ph - 10, { align: 'right' });
    }
    doc.save(`TGPC-RPH-SEARCH-${safeFilename(query.trim())}-${fileDateStr(now)}.pdf`);
  }

  const FORMULA_CHARS = ['=', '+', '-', '@', '\t', '\r'];

  // Export header/filename hygiene: raw search/refiner input must not be able
  // to split `#`-comment rows (CR/LF) or produce hostile filenames.
  function cleanHeader(s: string): string {
    return s.replace(/[\r\n]+/g, ' ');
  }

  function safeFilename(s: string): string {
    const flat = s.replace(/[\r\n]+/g, ' ').replace(/[/\\?%*:|"<>]/g, '').trim();
    return (flat || 'all').slice(0, 60);
  }

  function refinerParts(): string[] {
    const parts: string[] = [];
    if (category !== 'all') parts.push(`Category: ${category}`);
    if (advFilters.registration_number?.trim()) parts.push(`RPC: ${cleanHeader(advFilters.registration_number.trim())}`);
    if (advFilters.name?.trim()) parts.push(`Name: ${cleanHeader(advFilters.name.trim())}`);
    if (advFilters.father_name?.trim()) parts.push(`Father: ${cleanHeader(advFilters.father_name.trim())}`);
    if (advFilters.gender) parts.push(`Gender: ${advFilters.gender}`);
    if (advFilters.status) parts.push(`Status: ${advFilters.status}`);
    if (advFilters.valid_till) parts.push(`Valid Till: ${advFilters.valid_till}`);
    return parts;
  }

  function csvCell(value: unknown): string {
    const str = String(value ?? '');
    const escaped = str.replace(/"/g, '""');
    const prefix = FORMULA_CHARS.includes(str.trimStart().charAt(0)) ? "'" : '';
    return `"${prefix}${escaped}"`;
  }

  function exportCSV() {
    if (filtered.length === 0) return;
    const now = new Date();
    const kw = cleanHeader(query.trim() || '(all)');
    const countLineCsv = `# Results: ${filtered.length.toLocaleString()} of ${results.length.toLocaleString()}${refinersActive || category !== 'all' ? ' (filtered)' : ''}`;
    const filtPartsCsv = refinerParts();
    const filterLineCsv = filtPartsCsv.length ? `# Filters: ${filtPartsCsv.join(' | ')}` : '# Filters: none';
    const header = ['RPC NUMBER', 'NAME', 'FATHER NAME', 'GENDER', 'CATEGORY', 'VALID TILL', 'STATUS'];
    const rows = filtered.map(r => [
      csvCell(r.registration_number),
      csvCell(r.name),
      csvCell(r.father_name),
      csvCell(r.gender),
      csvCell(r.category),
      csvCell(r.validity_date),
      csvCell(r.status)
    ]);
    const combinedCsv = `${countLineCsv} | ${filterLineCsv.replace('# Filters:', 'Filters:')}`;
    const csv = [`# TGPC RPh Index - Search: ${kw} - ${fmtDate(now)}`, combinedCsv, header.join(','), ...rows.map(r => r.join(','))].join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `TGPC-RPH-SEARCH-${safeFilename(query.trim())}-${fileDateStr(now)}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
  }
</script>

<div class="space-y-2">
  <h1 class="sr-only">Search Telangana State Pharmacy Council pharmacist records by name or RPC number</h1>
  <!-- Search + Chips row -->
  <div class="flex flex-col sm:flex-row items-start sm:items-center gap-3">
    <div class="flex items-center w-full min-w-0 border-b-2 border-[var(--t-border)] transition-colors focus-within:border-[#00cc66]">
      <div class="relative min-w-0 min-h-[2rem] flex-1" style="display:{searched ? 'inline-grid' : 'grid'};grid-template-columns:1fr">
        <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9ca3af] pointer-events-none z-10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
          <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
        </svg>
        <span class="col-start-1 row-start-1 invisible whitespace-nowrap pl-9 {searched ? 'pr-36' : 'pr-16'} py-1.5 text-[0.95rem] max-sm:text-base min-w-0 overflow-hidden">{query || 'Search by Name or Registered Pharmacist Certificate (RPC) Number'}</span>
        <label for="tgpc-search" class="sr-only">Search by name or Registered Pharmacist Certificate (RPC) number</label>
        <input
          id="tgpc-search"
          type="text"
          bind:value={query}
          onkeydown={onSearchKeydown}
          placeholder="Search by Name or Registered Pharmacist Certificate (RPC) Number"
          aria-label="Search"
          autocomplete="off"
          class="col-start-1 row-start-1 w-full pl-9 {searched ? 'pr-36' : 'pr-16'} py-1.5 text-[0.95rem] bg-transparent outline-none max-sm:text-base"
        />
        {#if query.trim()}
          <div class="absolute right-0.5 top-1/2 -translate-y-1/2 z-10 flex items-center gap-1">
            <button onclick={doSearch} disabled={query.trim().length < 3}
              class="rounded cursor-pointer border-none transition-colors disabled:opacity-30 disabled:cursor-not-allowed {searched ? 'px-2.5 py-1 text-[0.65rem] font-medium' : 'px-3 py-1 text-[0.7rem] font-semibold'}"
              style="background:{query.trim().length >= 3 ? (searched ? 'rgba(0,204,102,0.08)' : '#00cc66') : 'var(--t-surface)'};color:{query.trim().length >= 3 ? (searched ? '#00cc66' : '#fff') : '#9ca3af'}"
              transition:fly={{ y: 4, duration: 120, opacity: 0 }}>
              SEARCH
            </button>
            {#if searched}
              <button onclick={reset}
                class="px-2.5 py-1 rounded text-[0.65rem] font-medium cursor-pointer border-none transition-colors uppercase"
                style="background:rgba(239,68,68,0.06);color:#ef4444"
                transition:fly={{ y: 4, duration: 120, opacity: 0 }}>
                Clear
              </button>
            {/if}
          </div>
        {/if}
      </div>
    </div>

    {#if searched}
      <div class="flex flex-wrap items-center gap-1 min-w-0 w-full sm:w-auto sm:flex-1" transition:fly={{ y: 6, duration: 200, opacity: 0 }}>
        <span class="text-[0.75rem] text-[#9ca3af] tabular-nums flex-shrink-0">{filtered.length.toLocaleString()} results</span>
        {#if capped}
          <!-- Same classes as the count span so the contrast fingerprint
               (rule + CSS target only) is unchanged. -->
          <span class="text-[0.75rem] text-[#9ca3af] tabular-nums flex-shrink-0">· first {MAX_SEARCH_RESULTS} matches shown — narrow your search</span>
        {/if}
        {#if refinersActive}
          <span class="text-[0.65rem] font-semibold uppercase rounded px-1.5 py-0.5 flex-shrink-0" style="background:rgba(0,204,102,0.08);color:#00cc66">Filtered</span>
        {/if}
        <span class="-mx-1 px-1 flex-nowrap overflow-x-auto sm:mx-0 sm:px-0 sm:flex-wrap sm:ml-auto flex items-center gap-1.5" style="scrollbar-width:thin;scrollbar-color:var(--t-border) transparent;-webkit-overflow-scrolling:touch">
          {#each CATEGORY_FILTERS as cat (cat)}
            <button onclick={() => { category = cat; }}
              class="px-2.5 py-1 rounded text-[0.7rem] font-medium transition-all cursor-pointer border-none whitespace-nowrap"
              style={chipStyle(cat)}>
              {cat === 'all' ? 'All' : cat} <span class="opacity-60">({(categoryCounts[cat] || 0).toLocaleString()})</span>
            </button>
          {/each}
          <button onclick={exportCSV} class="flex items-center gap-1 px-2.5 py-1 rounded text-[0.65rem] font-medium cursor-pointer border-none transition-colors whitespace-nowrap" style="background:rgba(0,204,102,0.08);color:#00cc66">EXPORT CSV</button>
          <button onclick={exportPDF} class="flex items-center gap-1 px-2.5 py-1 rounded text-[0.65rem] font-medium cursor-pointer border-none transition-colors whitespace-nowrap" style="background:rgba(239,68,68,0.06);color:#ef4444">EXPORT PDF</button>
        </span>
      </div>
    {/if}
  </div>

  <!-- Results -->
  {#if searched}
    <div transition:fly={{ y: 10, duration: 250, opacity: 0 }}>
    {#if loading}
      <div class="space-y-3 py-4">
        {#each Array(8) as _, i (i)}
          <div class="h-4 bg-[var(--t-surface)] rounded animate-pulse" style="width:{40 + Math.random() * 60}%"></div>
        {/each}
      </div>
    {:else}
      {#if results.length > 0}
        <!-- Result filters — mobile: collapsed behind a toggle; desktop: slim single row -->
      <div class="mb-2 rounded-lg border bg-[var(--t-bg)] p-2 transition-colors" style="border-color:{refinersActive ? '#00cc66' : 'var(--t-border)'}">
        <button
          type="button"
          onclick={() => refinersOpen = !refinersOpen}
          aria-expanded={refinersOpen}
          aria-controls="refiner-fields"
          class="lg:hidden w-full flex items-center justify-between gap-2 text-[0.7rem] font-semibold uppercase tracking-widest text-[#9ca3af] cursor-pointer border-none bg-transparent p-0"
        >
          <span class="flex items-center gap-1.5">
            <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h18M7 12h10M10 18h4"/></svg>
            Refine
            {#if refinersActive}<span class="rounded px-1.5 py-0.5 text-[0.6rem] normal-case tracking-normal" style="background:rgba(0,204,102,0.08);color:#00cc66">Active</span>{/if}
          </span>
          <svg class="w-4 h-4 transition-transform {refinersOpen ? 'rotate-180' : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        </button>
        <div id="refiner-fields" class="{refinersOpen ? 'flex' : 'hidden'} lg:flex flex-wrap items-end gap-2 mt-2 lg:mt-0 lg:flex-nowrap">
        <label class="flex min-w-[92px] flex-1 flex-col gap-1">
          <span class="text-[0.6rem] font-semibold uppercase tracking-widest text-[#9ca3af]">RPC</span>
          <input type="text" bind:value={advFilters.registration_number} placeholder="TG..."
            class="h-7 w-full rounded-lg border border-[var(--t-border)] bg-[var(--t-bg)] px-2.5 text-sm outline-none transition-all focus:border-[#00cc66] focus:ring-2 focus:ring-[rgba(0,204,102,0.15)]" />
        </label>
        <label class="flex min-w-[122px] flex-1 flex-col gap-1">
          <span class="text-[0.6rem] font-semibold uppercase tracking-widest text-[#9ca3af]">Name</span>
          <input type="text" bind:value={advFilters.name} placeholder="Name"
            class="h-7 w-full rounded-lg border border-[var(--t-border)] bg-[var(--t-bg)] px-2.5 text-sm outline-none transition-all focus:border-[#00cc66] focus:ring-2 focus:ring-[rgba(0,204,102,0.15)]" />
        </label>
        <label class="flex min-w-[122px] flex-1 flex-col gap-1">
          <span class="text-[0.6rem] font-semibold uppercase tracking-widest text-[#9ca3af]">Father Name</span>
          <input type="text" bind:value={advFilters.father_name} placeholder="Father"
            class="h-7 w-full rounded-lg border border-[var(--t-border)] bg-[var(--t-bg)] px-2.5 text-sm outline-none transition-all focus:border-[#00cc66] focus:ring-2 focus:ring-[rgba(0,204,102,0.15)]" />
        </label>
        <label class="flex min-w-[128px] flex-col gap-1">
          <span class="text-[0.6rem] font-semibold uppercase tracking-widest text-[#9ca3af]">Gender</span>
            <div class="flex h-7 rounded-full bg-[var(--t-surface)] p-1">
            <button onclick={() => advFilters.gender = ''} class="flex-1 rounded-full px-2 text-xs font-semibold transition-all" style="{!advFilters.gender ? 'background:#00cc66;color:#fff' : 'background:transparent;color:var(--t-muted)'}">All</button>
            <button onclick={() => advFilters.gender = 'Male'} class="flex-1 rounded-full px-2 text-xs font-semibold transition-all" style="{advFilters.gender === 'Male' ? 'background:#00cc66;color:#fff' : 'background:transparent;color:var(--t-muted)'}">Male</button>
            <button onclick={() => advFilters.gender = 'Female'} class="flex-1 rounded-full px-2 text-xs font-semibold transition-all" style="{advFilters.gender === 'Female' ? 'background:#00cc66;color:#fff' : 'background:transparent;color:var(--t-muted)'}">Female</button>
          </div>
        </label>
        <label class="flex min-w-[168px] flex-col gap-1">
          <span class="text-[0.6rem] font-semibold uppercase tracking-widest text-[#9ca3af]">Status</span>
            <div class="flex h-7 rounded-full bg-[var(--t-surface)] p-1">
            <button onclick={() => advFilters.status = ''} class="flex-1 rounded-full px-2 text-xs font-semibold transition-all" style="{!advFilters.status ? 'background:#00cc66;color:#fff' : 'background:transparent;color:var(--t-muted)'}">All</button>
            <button onclick={() => advFilters.status = 'Active'} class="flex-1 rounded-full px-2 text-xs font-semibold transition-all" style="{advFilters.status === 'Active' ? 'background:#00cc66;color:#fff' : 'background:transparent;color:var(--t-muted)'}">Active</button>
            <button onclick={() => advFilters.status = 'Inactive'} class="flex-1 rounded-full px-2 text-xs font-semibold transition-all" style="{advFilters.status === 'Inactive' ? 'background:#ef4444;color:#fff' : 'background:transparent;color:var(--t-muted)'}">Inactive</button>
          </div>
        </label>
        <label class="flex min-w-[138px] flex-col gap-1">
          <span class="text-[0.6rem] font-semibold uppercase tracking-widest text-[#9ca3af]">Valid Till</span>
          <DatePicker bind:value={advFilters.valid_till} placeholder="DD/MM/YYYY" />
        </label>
        {#if refinersActive}
          <button onclick={clearAdvanced} class="h-7 self-end rounded-full border border-[rgba(239,68,68,0.2)] bg-[var(--t-bg)] px-3 text-xs font-semibold text-[#ef4444] transition-colors hover:bg-[rgba(239,68,68,0.06)]">Clear</button>
        {/if}
        </div>
      </div>
      {/if}
      {#if filtered.length === 0}
        <p class="text-[0.85rem] text-[#9ca3af] py-8 text-center">No results</p>
      {:else}
        <div class="hidden md:block">
        <div style="max-height:{resultsMaxH};min-height:{resultsMinH};overflow-y:auto;overflow-x:auto" bind:this={resultsBox}>
        <table class="w-full" style="table-layout:auto">
          <thead class="sticky top-0 bg-[var(--t-bg)] z-10">
            <tr class="text-[0.65rem] font-semibold text-[#9ca3af] uppercase tracking-wider">
              <th class="font-inherit text-left py-1.5 border-b-2 border-[var(--t-border)] w-[52px]"></th>
              <th class="font-inherit text-left py-1.5 border-b-2 border-[var(--t-border)]">RPC NUMBER</th>
              <th class="font-inherit text-left py-1.5 border-b-2 border-[var(--t-border)]">Name</th>
              <th class="font-inherit text-left py-1.5 border-b-2 border-[var(--t-border)] hidden lg:table-cell">Father Name</th>
              <th class="font-inherit text-left py-1.5 border-b-2 border-[var(--t-border)] hidden xl:table-cell">Gender</th>
              <th class="font-inherit text-left py-1.5 border-b-2 border-[var(--t-border)]">Category</th>
              <th class="font-inherit text-left py-1.5 border-b-2 border-[var(--t-border)] hidden xl:table-cell">Valid Till</th>
              <th class="font-inherit text-right py-1.5 border-b-2 border-[var(--t-border)] pr-10">Status</th>
            </tr>
          </thead>
          <tbody>
            {#each filtered as r (r.registration_number)}
              <tr class="text-[0.875rem] text-[var(--t-ink-soft)] border-b border-[var(--t-surface)]" style="content-visibility:auto;contain-intrinsic-size:48px">
                <td class="py-1.5">
                  <img src={photoUrl(r)} alt="" loading="lazy" decoding="async" width="36" height="44" class="w-9 h-11 rounded object-cover bg-[var(--t-surface)]" />
                </td>
                <td class="py-2.5 text-[var(--t-link)]" style="font-weight:600">
                  <a href="/rph/{r.registration_number}" onclick={(e) => { e.preventDefault(); openDrawer(r.registration_number); }} class="hover:underline no-underline cursor-pointer" aria-label="View profile for {r.registration_number}">
                    {r.registration_number}
                  </a>
                </td>
                <td class="py-2.5 truncate hidden lg:table-cell" title={r.name}>{r.name}</td>
                <td class="py-2.5 truncate hidden lg:table-cell" title={r.father_name || ''}>{r.father_name || '—'}</td>
                <td class="py-2.5 hidden xl:table-cell">{r.gender || '—'}</td>
                <td class="py-2.5" style="color:{CATEGORY_COLORS[r.category] === '#111827' ? 'var(--t-ink)' : CATEGORY_COLORS[r.category]}">{r.category}</td>
                <td class="py-2.5 hidden xl:table-cell">{r.validity_date || '—'}</td>
                <td class="py-2.5 text-right pr-10">
                  {#if r.status}
                    <span style="color:{r.status === 'Active' ? 'var(--t-ink)' : '#ef4444'}">{r.status}</span>
                  {:else}
                    <span class="text-[var(--t-ink-soft)]">—</span>
                  {/if}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
        </div>
      </div>
      <div class="md:hidden space-y-1.5">
          {#each filtered as r (r.registration_number)}
            <div class="flex gap-3 p-2.5 rounded-lg border border-[var(--t-surface)] bg-[var(--t-surface-3)] text-[0.875rem]" style="content-visibility:auto;contain-intrinsic-size:140px">
              <img src={photoUrl(r)} alt="" loading="lazy" decoding="async" width="48" height="58" class="w-12 h-14 rounded-md object-cover bg-[var(--t-surface)] flex-shrink-0" />
              <div class="min-w-0 flex-1">
                <div class="flex items-center justify-between gap-2">
                  <a href="/rph/{r.registration_number}" onclick={(e) => { e.preventDefault(); openDrawer(r.registration_number); }} class="text-[var(--t-link)] hover:underline no-underline cursor-pointer tabular-nums" style="font-weight:600" aria-label="View profile for {r.registration_number}">{r.registration_number}</a>
                  {#if r.status}
                    <span class="flex-shrink-0 rounded-full px-2 py-0.5 text-[0.6rem] font-semibold uppercase tracking-wider" style="background:{r.status === 'Active' ? 'rgba(0,204,102,0.1)' : 'rgba(239,68,68,0.1)'};color:{r.status === 'Active' ? '#00cc66' : '#ef4444'}">{r.status}</span>
                  {/if}
                </div>
                <div class="mt-1 text-[var(--t-ink)] font-medium leading-snug">{r.name}</div>
                <div class="mt-0.5 text-[var(--t-muted)] text-[0.8rem] leading-snug">{r.father_name || '—'}</div>
                <div class="flex flex-wrap items-center gap-x-2 gap-y-1 mt-1.5 text-[0.75rem]">
                  <span class="rounded-full px-2 py-0.5 font-semibold uppercase tracking-wider text-[0.6rem]" style="background:color-mix(in srgb, {CATEGORY_COLORS[r.category] === '#111827' ? 'var(--t-ink)' : CATEGORY_COLORS[r.category]} 10%, transparent);color:{CATEGORY_COLORS[r.category] === '#111827' ? 'var(--t-ink)' : CATEGORY_COLORS[r.category]}">{r.category}</span>
                  {#if r.gender}<span class="text-[var(--t-muted)]">{r.gender}</span>{/if}
                  {#if r.validity_date}<span class="text-[var(--t-muted)]">Valid till {r.validity_date}</span>{/if}
                </div>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    {/if}
    </div>
  {/if}
  <ProfileSidebar open={drawerOpen} record={drawerRecord} photo={drawerPhoto} loading={drawerLoading} error={drawerError} onClose={closeDrawer} />
</div>