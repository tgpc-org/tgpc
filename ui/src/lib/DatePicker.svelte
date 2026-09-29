<script lang="ts">
  import { fade } from 'svelte/transition';
  import { MONTHS } from './dates';

  let { value = $bindable(''), placeholder = 'DD/MM/YYYY' }: { value?: string; placeholder?: string } = $props();

  let display = $derived(value ? value.replace(/^(\d{4})-(\d{2})-(\d{2})$/, '$3/$2/$1') : '');

  let open = $state(false);
  let view = $state<Date>(new Date());
  let pickerRef: HTMLDivElement | undefined;
  let inputRef = $state<HTMLInputElement | undefined>(undefined);

  // When the popover closes, hand focus back to the field it came from —
  // unless focus already moved to something else outside the picker (e.g.
  // the click that dismissed it landed on another control).
  let pickerWasOpen = false;
  $effect(() => {
    if (open) {
      pickerWasOpen = true;
      return;
    }
    if (!pickerWasOpen) return;
    pickerWasOpen = false;
    setTimeout(() => {
      const ae = document.activeElement;
      if ((!ae || ae === document.body || (pickerRef && pickerRef.contains(ae))) && inputRef) inputRef.focus();
    }, 0);
  });

  const WEEKDAYS = ['S', 'M', 'T', 'W', 'T', 'F', 'S'];

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

  // Screen readers get the full date; the visible cell is just the number.
  function ariaDate(d: Date): string {
    return d.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  }

  function addDays(d: Date, n: number): Date {
    return new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
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
    if (sel) return 'background:#00cc66;color:var(--t-ink);border-radius:6px';
    if (isToday) return 'border:1px solid #00cc66;border-radius:6px;color:var(--t-ink)';
    return '';
  }

  function onInputKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      open = false;
      e.stopPropagation();
    } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      open = true;
    } else if (open && (e.key === 'Enter' || e.key === ' ')) {
      // Combobox pattern: activate the focused day, mirroring Space so the
      // semantic role and keyboard behaviour agree.
      e.preventDefault();
      pickerRef?.querySelector<HTMLElement>('button[data-date]:focus')?.click();
    }
  }

  const NAV_KEYS = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']);

  // Roving focus over the day grid: arrows step by day/week, Home/End jump
  // to the month edges. Crossing an edge clamps instead of changing months.
  function onDialogKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      open = false;
      e.stopPropagation();
      return;
    }
    if (!NAV_KEYS.has(e.key)) return;
    const btn = (e.target as HTMLElement).closest?.('button[data-date]') as HTMLElement | null;
    if (!btn) return;
    e.preventDefault();
    const days = cells().filter((c): c is Date => c !== null);
    const [y, m, d] = (btn.getAttribute('data-date') || '').split('-').map(Number);
    const current = new Date(y, m - 1, d);
    let target: Date | null = null;
    if (e.key === 'ArrowLeft') target = addDays(current, -1);
    else if (e.key === 'ArrowRight') target = addDays(current, 1);
    else if (e.key === 'ArrowUp') target = addDays(current, -7);
    else if (e.key === 'ArrowDown') target = addDays(current, 7);
    else if (e.key === 'Home') target = days[0] || null;
    else if (e.key === 'End') target = days[days.length - 1] || null;
    if (!target) return;
    if (target < days[0]) target = days[0];
    if (target > days[days.length - 1]) target = days[days.length - 1];
    pickerRef?.querySelector<HTMLButtonElement>(`button[data-date="${iso(target)}"]`)?.focus();
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
    bind:this={inputRef}
    aria-haspopup="dialog"
    aria-expanded={open}
    role="combobox"
    aria-controls="datepicker-dialog"
    onfocus={() => {
      open = true;
      if (value) {
        const p = value.split('-').map(Number);
        if (p.length === 3 && p[1] >= 1 && p[1] <= 12) view = new Date(p[0], p[1] - 1, 1);
      }
    }}
    onkeydown={onInputKeydown}
    class="w-full h-8 px-2.5 text-[0.8rem] rounded-lg border border-[var(--t-border)] bg-[var(--t-bg)] outline-none transition-colors focus:border-[#00cc66] focus:ring-2 focus:ring-[rgba(0,204,102,0.15)] cursor-pointer"
  />
  {#if isOpen()}
<div
      class="absolute left-0 lg:left-auto lg:right-0 top-full mt-1 z-30 w-60 max-w-[calc(100vw-1.5rem)] bg-[var(--t-bg)] border border-[var(--t-border)] rounded-lg shadow-lg p-2"
      role="dialog"
      id="datepicker-dialog"
      aria-label="Date picker"
      tabindex="-1"
      transition:fade={{ duration: 100 }}
      onkeydown={onDialogKeydown}
    >
<div class="flex items-center justify-between mb-1">
        <button type="button" onclick={prevMonth} aria-label="Previous month"
          class="w-7 h-7 flex items-center justify-center rounded-md text-[var(--t-muted)] hover:bg-[var(--t-surface)] cursor-pointer border-none transition-colors">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m15 18-6-6 6-6"/></svg>
        </button>
        <span class="text-[0.8rem] font-semibold text-[var(--t-ink)]">{MONTHS[view.getMonth()]} {view.getFullYear()}</span>
        <button type="button" onclick={nextMonth} aria-label="Next month"
          class="w-7 h-7 flex items-center justify-center rounded-md text-[var(--t-muted)] hover:bg-[var(--t-surface)] cursor-pointer border-none transition-colors">
          <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="m9 18 6-6-6-6"/></svg>
        </button>
      </div>
      <div class="grid grid-cols-7 text-center mb-1 gap-y-0.5">
        <!-- Weekday initials repeat (S, T) so key by index — keying by value
             throws Svelte's each_key_duplicate when the calendar opens. -->
        {#each WEEKDAYS as w, i (i)}
          <!-- Single letters are noise for AT; the day buttons carry full labels. -->
          <span aria-hidden="true" class="text-[0.65rem] font-semibold text-[var(--t-muted)] py-1">{w}</span>
        {/each}
        {#each cells() as d, index (d?.toISOString() || index)}
          {#if d}
            <button type="button" onclick={() => select(d)}
              data-date={iso(d)}
              aria-label={ariaDate(d)}
              aria-pressed={value === iso(d)}
              aria-current={iso(d) === today() ? 'date' : undefined}
              class="h-8 text-[0.8rem] rounded-md transition-colors hover:bg-[rgba(0,204,102,0.12)] cursor-pointer border-none"
              style={cellStyle(d)}>
              {d.getDate()}
            </button>
          {:else}
            <span class="h-8"></span>
          {/if}
        {/each}
      </div>
<div class="mt-1 flex items-center justify-between border-t border-[var(--t-surface)] pt-1.5">
        <button type="button" onclick={() => { value = today(); open = false; }}
          class="text-[0.7rem] font-semibold uppercase hover:underline cursor-pointer border-none bg-transparent"
          style="color:var(--t-link)">
          Today
        </button>
        {#if value}
          <button type="button" onclick={() => { value = ''; open = false; }}
            class="text-[0.7rem] font-semibold uppercase hover:underline cursor-pointer border-none rounded px-1.5"
            style="background:rgba(239,68,68,0.12);color:var(--t-ink)">
            Clear
          </button>
        {/if}
      </div>
    </div>
  {/if}
</div>
