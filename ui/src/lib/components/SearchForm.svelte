<script lang="ts">
  import { fly } from 'svelte/transition';
  import { prefersReducedMotion } from '#lib/motion.js';

  let {
    query = $bindable(''),
    searched = $bindable(false),
    onsearch,
    onreset
  }: {
    query?: string;
    searched?: boolean;
    onsearch: () => void;
    onreset: () => void;
  } = $props();

  function onSearchKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') onsearch();
  }

  // Brand fills carry ink text: white on brand green is only 2.1:1.
  function buttonBg(): string {
    const len = query.trim().length;
    if (len < 3) return 'var(--t-surface)';
    return searched ? 'rgba(0,204,102,0.14)' : '#00cc66';
  }
</script>

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
          <button onclick={onsearch} disabled={query.trim().length < 3}
            class="rounded cursor-pointer border-none transition-colors disabled:opacity-45 disabled:cursor-not-allowed {searched ? 'px-2.5 py-1.5 text-[0.7rem] font-semibold' : 'px-3 py-1.5 text-[0.75rem] font-bold'}"
            style="background:{buttonBg()};color:{query.trim().length >= 3 ? 'var(--t-ink)' : 'var(--t-muted)'}"
            transition:fly={{ y: 4, duration: prefersReducedMotion() ? 0 : 120, opacity: 0 }}>
            SEARCH
          </button>
          {#if searched}
            <button onclick={onreset}
              class="px-2.5 py-1.5 rounded text-[0.7rem] font-semibold cursor-pointer border-none transition-colors uppercase"
              style="background:rgba(239,68,68,0.12);color:var(--t-ink)"
              transition:fly={{ y: 4, duration: prefersReducedMotion() ? 0 : 120, opacity: 0 }}>
              Clear
            </button>
          {/if}
        </div>
      {/if}
      {#if query.trim().length > 0 && query.trim().length < 3}
        <!-- The SEARCH button sits disabled below 3 chars; this says why
             instead of leaving the tap doing nothing. -->
        <p aria-live="polite" class="sr-only">Type at least 3 characters to search</p>
        <span
          class="absolute right-0.5 -bottom-4 z-10 text-[0.65rem] whitespace-nowrap"
          style="color:var(--t-muted)"
        >Type at least 3 characters</span>
      {/if}
    </div>
  </div>
</div>
