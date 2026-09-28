<script lang="ts">
  import type { AdvancedFilters } from '$lib/api';
  import DatePicker from '$lib/DatePicker.svelte';

  let {
    filters = $bindable({ valid_till: '' } as AdvancedFilters),
    open = $bindable(false),
    active = false,
    onclear
  }: {
    filters?: AdvancedFilters;
    open?: boolean;
    active?: boolean;
    onclear: () => void;
  } = $props();
</script>

<!-- Result filters — mobile: collapsed behind a toggle; desktop: slim single row -->
<div class="mb-2 rounded-lg border bg-[var(--t-bg)] p-2.5 transition-colors" style="border-color:{active ? '#00cc66' : 'var(--t-border)'}">
  <button
    type="button"
    onclick={() => (open = !open)}
    aria-expanded={open}
    aria-controls="refiner-fields"
    class="lg:hidden w-full flex items-center justify-between gap-2 text-[0.7rem] font-semibold uppercase tracking-widest text-[var(--t-muted)] cursor-pointer border-none bg-transparent p-0"
  >
    <span class="flex items-center gap-1.5">
      <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M3 6h18M7 12h10M10 18h4"/></svg>
      Refine
      {#if active}<span class="rounded px-1.5 py-0.5 text-[0.6rem] normal-case tracking-normal" style="background:rgba(0,204,102,0.14);color:var(--t-ink)">Active</span>{/if}
    </span>
    <svg class="w-4 h-4 transition-transform {open ? 'rotate-180' : ''}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
  </button>
  <div id="refiner-fields" class="{open ? 'flex' : 'hidden'} lg:flex flex-wrap items-end gap-2.5 mt-2.5 lg:mt-0 lg:flex-nowrap">
    <label class="flex min-w-[92px] flex-1 flex-col gap-1">
      <span class="text-[0.65rem] font-semibold uppercase tracking-widest text-[var(--t-muted)]">RPC</span>
      <input type="text" bind:value={filters.registration_number} placeholder="TG..."
        class="h-8 w-full rounded-lg border border-[var(--t-border)] bg-[var(--t-bg)] px-2.5 text-[0.8rem] outline-none transition-all focus:border-[#00cc66] focus:ring-2 focus:ring-[rgba(0,204,102,0.15)]" />
    </label>
    <label class="flex min-w-[122px] flex-1 flex-col gap-1">
      <span class="text-[0.65rem] font-semibold uppercase tracking-widest text-[var(--t-muted)]">Name</span>
      <input type="text" bind:value={filters.name} placeholder="Name"
        class="h-8 w-full rounded-lg border border-[var(--t-border)] bg-[var(--t-bg)] px-2.5 text-[0.8rem] outline-none transition-all focus:border-[#00cc66] focus:ring-2 focus:ring-[rgba(0,204,102,0.15)]" />
    </label>
    <label class="flex min-w-[122px] flex-1 flex-col gap-1">
      <span class="text-[0.65rem] font-semibold uppercase tracking-widest text-[var(--t-muted)]">Father Name</span>
      <input type="text" bind:value={filters.father_name} placeholder="Father"
        class="h-8 w-full rounded-lg border border-[var(--t-border)] bg-[var(--t-bg)] px-2.5 text-[0.8rem] outline-none transition-all focus:border-[#00cc66] focus:ring-2 focus:ring-[rgba(0,204,102,0.15)]" />
    </label>
    <label class="flex min-w-[128px] flex-col gap-1">
      <span class="text-[0.65rem] font-semibold uppercase tracking-widest text-[var(--t-muted)]">Gender</span>
      <div class="flex h-8 rounded-full bg-[var(--t-surface)] p-1">
        <button onclick={() => (filters.gender = '')} class="flex-1 rounded-full px-2.5 text-[0.75rem] font-semibold transition-all" style="{!filters.gender ? 'background:#00cc66;color:var(--t-ink)' : 'background:transparent;color:var(--t-ink-soft)'}">All</button>
        <button onclick={() => (filters.gender = 'Male')} class="flex-1 rounded-full px-2.5 text-[0.75rem] font-semibold transition-all" style="{filters.gender === 'Male' ? 'background:#00cc66;color:var(--t-ink)' : 'background:transparent;color:var(--t-ink-soft)'}">Male</button>
        <button onclick={() => (filters.gender = 'Female')} class="flex-1 rounded-full px-2.5 text-[0.75rem] font-semibold transition-all" style="{filters.gender === 'Female' ? 'background:#00cc66;color:var(--t-ink)' : 'background:transparent;color:var(--t-ink-soft)'}">Female</button>
      </div>
    </label>
    <label class="flex min-w-[168px] flex-col gap-1">
      <span class="text-[0.65rem] font-semibold uppercase tracking-widest text-[var(--t-muted)]">Status</span>
      <div class="flex h-8 rounded-full bg-[var(--t-surface)] p-1">
        <button onclick={() => (filters.status = '')} class="flex-1 rounded-full px-2.5 text-[0.75rem] font-semibold transition-all" style="{!filters.status ? 'background:#00cc66;color:var(--t-ink)' : 'background:transparent;color:var(--t-ink-soft)'}">All</button>
        <button onclick={() => (filters.status = 'Active')} class="flex-1 rounded-full px-2.5 text-[0.75rem] font-semibold transition-all" style="{filters.status === 'Active' ? 'background:#00cc66;color:var(--t-ink)' : 'background:transparent;color:var(--t-ink-soft)'}">Active</button>
        <button onclick={() => (filters.status = 'Inactive')} class="flex-1 rounded-full px-2.5 text-[0.75rem] font-semibold transition-all" style="{filters.status === 'Inactive' ? 'background:#ef4444;color:var(--t-ink)' : 'background:transparent;color:var(--t-ink-soft)'}">Inactive</button>
      </div>
    </label>
    <label class="flex min-w-[138px] flex-col gap-1">
      <span class="text-[0.65rem] font-semibold uppercase tracking-widest text-[var(--t-muted)]">Valid Till</span>
      <DatePicker bind:value={filters.valid_till} placeholder="DD/MM/YYYY" />
    </label>
    {#if active}
      <button onclick={onclear} class="h-8 self-end rounded-full border border-[rgba(239,68,68,0.35)] bg-[var(--t-bg)] px-3 text-[0.75rem] font-semibold text-[var(--t-ink)] transition-colors hover:bg-[rgba(239,68,68,0.12)]">Clear</button>
    {/if}
  </div>
</div>
