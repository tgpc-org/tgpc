<script lang="ts">
  import type { DispatchFile } from '$lib/types';
  import { fetchDispatchFiles } from '$lib/api';
  import { browser } from '$app/environment';

  import { cachedOrNull, setCache } from '$lib/cache';

  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  let { data } = $props();

  let files = $state<DispatchFile[]>([]);
  let years = $state<string[]>([]);
  let tab = $state<string | null>(null);
  let query = $state('');
  let loading = $state(true);

  function parse(n: string) {
    const m = n.match(/DL(\d{2})(\d{2})(\d{4})[A-Z]*\.pdf/i);
    return m ? { d: m[1], mo: m[2], y: m[3], date: new Date(+m[3], +m[2]-1, +m[1]) } : null;
  }

  function fmt(f: { d: string; mo: string; y: string }) {
    return `${f.d} ${MONTHS[+f.mo-1]} ${f.y}`;
  }

  let sizes = $state<Record<string, number>>({});

  function build(raw: { name: string; size?: number; stale?: boolean }[]) {
    raw.forEach(f => { if (f.size) sizes[f.name] = f.size; });
    files = raw.map(f => ({ name: f.name, parsed: parse(f.name), size: f.size, stale: f.stale }))
      .filter(f => f.parsed)
      .sort((a, b) => b.parsed!.date.getTime() - a.parsed!.date.getTime());
    years = [...new Set(files.map(f => f.parsed!.y))].sort((a, b) => +b - +a);
    tab = years[0] || null;
  }

  let filtered = $derived.by(() => files.filter(f => {
    if (!f.parsed) return false;
    if (tab && f.parsed.y !== tab) return false;
    if (!query) return true;
    const q = query.toLowerCase();
    return f.name.toLowerCase().includes(q) || fmt(f.parsed).toLowerCase().includes(q);
  }));

  const cached = browser && cachedOrNull<{ name: string; size?: number; stale?: boolean }[]>("tgpc_dispatch");
  // svelte-ignore state_referenced_locally
  const initial = cached || data.files;
  if (initial.length > 0) { build(initial); loading = false; }

  if (!cached && browser) {
    fetchDispatchFiles().then(raw => {
      setCache('tgpc_dispatch', raw);
      build(raw);
      loading = false;
    });
  }
</script>

<div class="space-y-4">
  <div class="flex items-center gap-2">
    <div class="relative flex-1">
      <svg class="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-[#9ca3af] pointer-events-none" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
        <circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/>
      </svg>
      <input type="text" bind:value={query} placeholder="Search files"
        aria-label="Search"
        class="w-full pl-9 pr-4 py-1.5 border-b-2 border-[#e5e7eb] text-[0.95rem] bg-transparent outline-none transition-colors focus:border-[#00cc66] max-sm:text-base" />
    </div>
  </div>

  <div class="flex items-center gap-1 text-[0.75rem]">
    {#each years as y (y)}
      <button onclick={() => tab = y}
        class="px-2.5 py-1 rounded text-[0.7rem] font-medium transition-all cursor-pointer border-none"
        style={y === tab ? 'background:#00cc66;color:#fff' : 'background:#f3f4f6;color:#6b7280'}>
        {y} <span class="opacity-50">({files.filter(f => f.parsed?.y === y).length})</span>
      </button>
    {/each}
  </div>

  {#if loading}
    <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
      {#each Array(8) as _, i (i)}
        <div class="h-16 bg-[#f3f4f6] rounded"></div>
      {/each}
    </div>
  {:else if filtered.length === 0}
    <p class="text-[0.85rem] text-[#9ca3af] py-8 text-center">No files</p>
  {:else}
    <div class="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-1.5">
      {#if tab === null}
        {#each years as y (y)}
          {@const fy = filtered.filter(f => f.parsed?.y === y)}
          {#if fy.length > 0}
            <div class="col-span-full text-[0.65rem] font-semibold text-[#9ca3af] uppercase tracking-wider py-2">{y} — {fy.length}</div>
            {#each fy as f (f.name)}
              <a href={`/api/dispatch/${f.name}`} target="_blank" rel="noopener"
                class="flex items-center gap-2 p-2.5 border border-[#e5e7eb] rounded-lg no-underline text-[#111827] hover:bg-[#f9fafb] transition-colors">
                <img src="/pdf.svg" alt="" width="24" height="24" class="block flex-shrink-0" />
                <div class="min-w-0">
                  <div class="text-[0.6rem] font-semibold uppercase tracking-widest text-[#9ca3af]">Dispatch List</div>
                  <div class="text-[0.8rem] font-medium truncate">{fmt(f.parsed!)}</div>
                  <div class="text-[0.65rem] text-[#9ca3af]">{sizes[f.name] ? Math.round(sizes[f.name] / 1024) + ' KB' : ''}</div>
                </div>
              </a>
            {/each}
          {/if}
        {/each}
      {:else}
        {#each filtered as f (f.name)}
          <a href={`/api/dispatch/${f.name}`} target="_blank" rel="noopener"
            class="flex items-center gap-2 p-2.5 border border-[#e5e7eb] rounded-lg no-underline text-[#111827] hover:bg-[#f9fafb] transition-colors">
            <img src="/pdf.svg" alt="" width="24" height="24" class="block flex-shrink-0" />
            <div class="min-w-0">
              <div class="text-[0.6rem] font-semibold uppercase tracking-widest text-[#9ca3af]">Dispatch List</div>
              <div class="text-[0.8rem] font-medium truncate">{fmt(f.parsed!)}</div>
              <div class="text-[0.65rem] text-[#9ca3af]">{sizes[f.name] ? Math.round(sizes[f.name] / 1024) + ' KB' : ''}</div>
            </div>
          </a>
        {/each}
      {/if}
    </div>
  {/if}
</div>
