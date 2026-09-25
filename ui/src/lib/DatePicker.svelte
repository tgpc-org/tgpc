<script lang="ts">
  import { fade } from 'svelte/transition';

  let { value = $bindable(''), placeholder = 'DD/MM/YYYY' }: { value?: string; placeholder?: string } = $props();

  let display = $derived(value ? value.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$3/$2/$1') : '');

  let open = $state(false);
  let view = $state<Date>(new Date());
  let pickerRef: HTMLDivElement | undefined;

  const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];
  const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];

  function isOpen() {
    return open;
  }

  function iso(d: Date): string {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
  }

  function prevMonth() {
    view = new Date(view.getFullYear(), view.getMonth() - 1, 1);
  }

  function nextMonth() {
    view = new Date(view.getFullYear(), view.getMonth() + 1, 1);
  }

  function select(d: Date) {
    value = iso(d);
    open = false;
  }

  function today(): string {
    return iso(new Date());
  }

  function cells(): Array<Date | null> {
    const first = new Date(view.getFullYear(), view.getMonth(), 1);
    const startDay = first.getDay();
    const daysInMonth = new Date(view.getFullYear(), view.getMonth() + 1, 0).getDate();
    const out: Array<Date | null> = [];
    for (let i = 0; i < startDay; i++) out.push(null);
    for (let d = 1; d <= daysInMonth; d++) out.push(new Date(view.getFullYear(), view.getMonth(), d));
    while (out.length % 7 !== 0) out.push(null);
    return out;
  }

  function cellStyle(d: Date): string {
    const sel = value === iso(d);
    const isToday = value === '' && iso(d) === today();
    if (sel) return 'background:#00cc66;color:#fff;border-radius:6px';
    if (isToday) return 'border:1px solid #00cc66;border-radius:6px';
    return '';
  }

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      open = false;
      e.stopPropagation();
    }
  }

  function onClickOutside(e: MouseEvent) {
    if (pickerRef && !pickerRef.contains(e.target as Node)) open = false;
  }
</script>

<svelte:window on:click={onClickOutside} />

<div class="relative w-full" bind:this={pickerRef}>
  <input
    type="text"
    readonly
    value={display}
    placeholder={placeholder}
    onfocus={() => {
      open = true;
      if (value) {
        const p = value.split('-').map(Number);
        if (p.length === 3 && p[1] >= 1 && p[1] <= 12) view = new Date(p[0], p[1] - 1, 1);
      }
    }}
    onkeydown={onKeydown}
    class="w-full h-7 px-2.5 text-xs rounded-lg border border-[var(--t-border)] bg-[var(--t-bg)] outline-none transition-colors focus:border-[#00cc66] focus:ring-2 focus:ring-[rgba(0,204,102,0.15)] cursor-pointer"
  />
  {#if isOpen()}
<div
      class="absolute left-0 top-full mt-1 z-30 w-full bg-[var(--t-bg)] border border-[var(--t-border)] rounded-lg shadow-lg p-0.5"
      role="dialog"
      aria-label="Date picker"
      tabindex="-1"
      transition:fade={{ duration: 100 }}
      onkeydown={onKeydown}
    >
<div class="flex items-center justify-between mb-0.25">
        <button type="button" onclick={prevMonth} aria-label="Previous month"
          class="w-4 h-4 flex items-center justify-center rounded text-[var(--t-muted)] hover:bg-[var(--t-surface)] cursor-pointer border-none transition-colors">
          <svg class="w-2.5 h-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m15 18-6-6 6-6"/></svg>
        </button>
        <span class="text-[0.6rem] font-semibold text-[var(--t-ink)]">{MONTHS[view.getMonth()]} {view.getFullYear()}</span>
        <button type="button" onclick={nextMonth} aria-label="Next month"
          class="w-4 h-4 flex items-center justify-center rounded text-[var(--t-muted)] hover:bg-[var(--t-surface)] cursor-pointer border-none transition-colors">
          <svg class="w-2.5 h-2.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m9 18 6-6-6-6"/></svg>
        </button>
      </div>
      <div class="grid grid-cols-7 text-center mb-0.25">
        {#each WEEKDAYS as w, i (i)}
          <span class="text-[0.5rem] font-semibold text-[#9ca3af] py-0.25">{w}</span>
        {/each}
        {#each cells() as d, index (d?.toISOString() || index)}
          {#if d}
            <button type="button" onclick={() => select(d)}
              class="h-4 text-[0.6rem] rounded transition-colors hover:bg-[rgba(0,204,102,0.08)] cursor-pointer border-none"
              style={cellStyle(d)}>
              {d.getDate()}
            </button>
          {:else}
            <span class="h-4"></span>
          {/if}
        {/each}
      </div>
<div class="mt-0.5 flex items-center justify-between border-t border-[var(--t-surface)] pt-0.5">
        <button type="button" onclick={() => { value = today(); open = false; }}
          class="text-[0.5rem] font-semibold text-[#00cc66] uppercase hover:underline cursor-pointer border-none bg-transparent">
          Today
        </button>
        {#if value}
          <button type="button" onclick={() => { value = ''; open = false; }}
            class="text-[0.5rem] font-semibold text-[#ef4444] uppercase hover:underline cursor-pointer border-none bg-transparent">
            Clear
          </button>
        {/if}
      </div>
    </div>
  {/if}
</div>
