<script lang="ts">
  let { data } = $props();

  let query = $state('');

  const ids = $derived(data.ids ?? []);
  const q = $derived(query.trim().toUpperCase().replace(/\s+/g, ''));

  const exact = $derived(q.length > 0 && ids.includes(q));
  const matches = $derived.by(() => {
    if (q.length < 3) return [];
    const out: string[] = [];
    for (const id of ids) {
      if (id.includes(q)) {
        out.push(id);
        if (out.length >= 25) break;
      }
    }
    return out;
  });
  const matchCount = $derived.by(() => {
    if (q.length < 3) return 0;
    let n = 0;
    for (const id of ids) if (id.includes(q)) n++;
    return n;
  });

  function fmt(n: number) { return n.toLocaleString('en-IN'); }
</script>

<svelte:head>
  <title>Blocked BH-P IDs — TGPC RPh Index</title>
  <meta name="description" content="Check whether a BH-P registration number is on the Pharmacy Council of India blocked list (PCI Circular IT-11/3/2026-IT, 19 Nov 2026). 44,730 IDs indexed." />
</svelte:head>

<div class="space-y-4">
  <h1 class="text-[1.4rem] font-bold tracking-tight" style="color:var(--t-ink)">Blocked BH-P IDs</h1>
  <p class="text-[0.875rem]" style="color:var(--t-muted)">
    Pharmacy Council of India circular
    <a href="https://pub-4591c8c5282040459ade2ed1e5e3d5be.r2.dev/notice/20261119PCICircularBlockingDuplicateBHPIDs.pdf"
       target="_blank" rel="noopener" class="font-semibold no-underline" style="color:var(--t-link)">IT-11/3/2026-IT (19-11-2026)</a>
    — duplicate / non-compliant BH-P IDs are blocked; affected pharmacists must re-register on
    <span style="color:var(--t-ink-soft)">DIGI-PHARMed</span>.
    {#if ids.length} {fmt(ids.length)} IDs indexed. {/if}
  </p>

  {#if data.error}
    <p class="text-[0.875rem] py-4" style="color:var(--t-red)">{data.error}</p>
  {:else}
    <div class="relative">
      <label for="bhq" class="sr-only">Search BH-P ID</label>
      <input
        id="bhq"
        type="text"
        bind:value={query}
        autocomplete="off"
        autocapitalize="characters"
        spellcheck="false"
        placeholder="Enter BH-P number, e.g. BH-P-25-11457"
        class="w-full rounded border px-3 py-2.5 text-[0.95rem] tabular-nums outline-none"
        style="border-color:var(--t-border);background:var(--t-surface-2);color:var(--t-ink)"
      />
      {#if query}
        <button
          onclick={() => (query = '')}
          class="absolute right-2 top-1/2 -translate-y-1/2 flex h-6 w-6 items-center justify-center rounded-full border-none cursor-pointer"
          style="background:var(--t-surface);color:var(--t-muted)"
          aria-label="Clear"
        >×</button>
      {/if}
    </div>

    <div aria-live="polite" aria-atomic="true" class="min-h-[4rem]">
      {#if data.ids.length === 0}
        <p class="text-[0.85rem] py-4" style="color:var(--t-muted)">Loading blocked IDs…</p>
      {:else if q.length === 0}
        <p class="text-[0.85rem] py-4" style="color:var(--t-muted)">
          Type a BH-P registration number to check whether it is on the blocked list.
        </p>
      {:else if exact}
        <div class="rounded-lg p-4" style="background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.35)">
          <div class="flex items-center gap-2">
            <span class="h-2.5 w-2.5 rounded-full" style="background:#ef4444"></span>
            <span class="text-[1rem] font-bold" style="color:#ef4444">Blocked</span>
          </div>
          <div class="mt-1 font-mono text-[0.95rem] tabular-nums" style="color:var(--t-ink)">{q}</div>
          <p class="mt-2 text-[0.8rem]" style="color:var(--t-ink-soft)">
            This ID is on Annexure-1 of PCI circular IT-11/3/2026-IT. Re-register on
            <span style="color:var(--t-ink)">DIGI-PHARMed</span> with an Aadhaar-matching name
            and genuine educational records, then complete AEBAS registration.
          </p>
        </div>
      {:else if q.length < 3}
        <p class="text-[0.85rem] py-4" style="color:var(--t-muted)">Keep typing — at least 3 characters.</p>
      {:else if matchCount === 0}
        <div class="rounded-lg p-4" style="background:rgba(0,204,102,0.08);border:1px solid rgba(0,204,102,0.35)">
          <div class="flex items-center gap-2">
            <span class="h-2.5 w-2.5 rounded-full" style="background:#00cc66"></span>
            <span class="text-[1rem] font-bold" style="color:#00b359">Not on the blocked list</span>
          </div>
          <p class="mt-1 text-[0.8rem]" style="color:var(--t-ink-soft)">
            {q} does not appear in Annexure-1. Verify from the official circular before acting.
          </p>
        </div>
      {:else}
        <div class="text-[0.8rem] mb-1" style="color:var(--t-muted)">
          {fmt(matchCount)} match{matchCount === 1 ? '' : 'es'}{matchCount > 25 ? ' (showing 25)' : ''}
        </div>
        <ul class="divide-y" style="border-color:var(--t-surface)">
          {#each matches as m (m)}
            <li class="py-1.5 font-mono text-[0.85rem] tabular-nums" style="color:var(--t-ink)">{m}</li>
          {/each}
        </ul>
      {/if}
    </div>
  {/if}
</div>
