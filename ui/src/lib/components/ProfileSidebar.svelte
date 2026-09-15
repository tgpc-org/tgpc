<script lang="ts">
  import { CATEGORY_COLORS } from '$lib/colors';
  import type { PharmacistRecord } from '$lib/types';
  import { fly, fade } from 'svelte/transition';

  let { open = false, record = null as PharmacistRecord | null, photo = '', loading = false, error = null as string | null, onClose = () => {} }: {
    open?: boolean;
    record?: PharmacistRecord | null;
    photo?: string;
    loading?: boolean;
    error?: string | null;
    onClose?: () => void;
  } = $props();

  let photoError = $state(false);

  $effect(() => {
    if (open) photoError = false;
  });

  function handlePhotoError() { photoError = true; }
  function formatDate(v: string | null | undefined) { return v || '—'; }
  function statusColor(s: string | null | undefined) { return s === 'Active' ? '#00cc66' : '#ef4444'; }
  // Theme-aware: MPharm's #111827 is invisible on night backgrounds, and
  // unknown categories fall back to the muted token (not raw #6b7280).
  function categoryColor(cat: string) {
    const c = CATEGORY_COLORS[cat as keyof typeof CATEGORY_COLORS];
    if (!c) return 'var(--t-muted)';
    return c === '#111827' ? 'var(--t-ink)' : c;
  }
  function printPage() { window.print(); }
  function displayWork(v: string | null | undefined): string {
    if (!v) return '—';
    const t = v.trim();
    if (t === '' || t === '----' || t === '—' || t === '--') return '—';
    return v;
  }

  let drawerEl = $state<HTMLDivElement | undefined>(undefined);
  let closeBtn = $state<HTMLButtonElement | undefined>(undefined);

  $effect(() => {
    if (open) {
      document.body.style.overflow = 'hidden';
      // Focus close button on open
      setTimeout(() => closeBtn?.focus(), 50);
    } else document.body.style.overflow = '';
    return () => { document.body.style.overflow = ''; };
  });

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape' && open) { onClose(); return; }
    if (e.key === 'Tab' && open && drawerEl) {
      const nodes = drawerEl.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
      if (nodes.length === 0) return;
      const first = nodes[0];
      const last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
  <!-- Overlay -->
  <button
    type="button"
    aria-label="Close profile"
    class="fixed inset-0 z-40 bg-[#111827]/30 border-0 p-0 cursor-pointer"
    onclick={onClose}
    transition:fade={{ duration: 150 }}
  ></button>
  <!-- Drawer -->
  <div
    bind:this={drawerEl}
    tabindex="-1"
    class="fixed right-0 top-0 z-50 h-dvh w-full sm:w-[420px] bg-[var(--t-bg)] border-l border-[var(--t-border)] overflow-y-auto flex flex-col focus:outline-none"
    role="dialog"
    aria-modal="true"
    aria-label={record ? `${record.name} profile` : 'Pharmacist profile'}
    transition:fly={{ x: 420, duration: 220 }}
  >
    <div class="sticky top-0 z-10 flex items-center justify-between gap-2 bg-[var(--t-bg)] border-b border-[var(--t-border)] px-4 py-3">
      <span class="text-sm font-semibold text-[var(--t-ink)] truncate">{loading ? 'Loading…' : error ? 'Not found' : 'Profile'}</span>
      <button bind:this={closeBtn} onclick={onClose} aria-label="Close" class="w-8 h-8 flex items-center justify-center rounded-full border border-[var(--t-border)] text-[var(--t-muted)] hover:bg-[var(--t-surface-3)] transition-colors">
        <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6 6 18M6 6l12 12"/></svg>
      </button>
    </div>

    <div class="flex-1 p-3 space-y-3">
      {#if loading}
        <div class="space-y-3 py-4">
          {#each Array(6) as _, i (i)}
            <div class="h-4 bg-[var(--t-surface)] rounded animate-pulse" style="width:{40 + Math.random()*60}%"></div>
          {/each}
        </div>
      {:else if error}
        <p class="text-sm text-[#ef4444] py-8 text-center">{error}</p>
      {:else if record}
        <!-- Single Module -->
        <div class="bg-[var(--t-bg)] border border-[var(--t-border)] rounded-xl p-3 space-y-3">
          <div class="flex gap-4 items-start">
            <div class="flex-shrink-0 w-24 h-30 rounded-lg bg-[var(--t-surface)] overflow-hidden relative" style="width:80px;height:100px">
              <img src={photo} alt={`${record.name}'s photo`} onerror={handlePhotoError} decoding="async" class="w-full h-full object-cover {photoError ? 'hidden' : ''}" />
              {#if photoError}
                <div class="w-full h-full flex items-center justify-center bg-[var(--t-surface)]">
                  <svg class="w-10 h-10 text-[var(--t-border-soft)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/></svg>
                </div>
              {/if}
            </div>
            <div class="flex-1 min-w-0 space-y-2">
              <h2 class="text-base font-bold text-[var(--t-ink)] truncate">{record.name}</h2>
              <div class="flex flex-wrap items-center gap-2">
                <span class="px-2.5 py-1 rounded-full text-[0.6rem] font-semibold uppercase tracking-wider tabular-nums" style="background:color-mix(in srgb, #2563eb 10%, transparent);color:var(--t-link)">RPC: {record.registration_number}</span>
                <span class="px-2.5 py-1 rounded-full text-[0.6rem] font-semibold uppercase tracking-wider" style="background:color-mix(in srgb, {categoryColor(record.category)} 10%, transparent);color:{categoryColor(record.category)}">{record.category}</span>
                <span class="px-2.5 py-1 rounded-full text-[0.6rem] font-semibold uppercase tracking-wider" style="background:{record.status === 'Active' ? 'rgba(0,204,102,0.1)' : 'rgba(239,68,68,0.1)'};color:{statusColor(record.status)}">{record.status || 'Unknown'}</span>
              </div>
              <div class="flex flex-wrap gap-3 text-xs text-[var(--t-muted)]">
                <span>Serial: <span class="text-[var(--t-ink-soft)] font-medium">#{record.serial_number || '—'}</span></span>
                <span>Valid till: <span class="text-[var(--t-ink-soft)] font-medium">{formatDate(record.validity_date)}</span></span>
              </div>
            </div>
          </div>
          <div class="border-t border-[var(--t-surface)] pt-2 flex flex-wrap gap-3 text-sm">
            <div class="flex items-center gap-1.5"><span class="text-[#9ca3af] text-xs">Father:</span><span class="text-[var(--t-ink-soft)] font-medium text-sm">{record.father_name || '—'}</span></div>
            {#if record.gender}<div class="flex items-center gap-1.5"><span class="text-[#9ca3af] text-xs">Gender:</span><span class="text-[var(--t-ink-soft)] font-medium text-sm">{record.gender}</span></div>{/if}
          </div>

          {#if record.education && record.education.length > 0}
            <div class="border-t border-[var(--t-surface)] pt-2 space-y-2">
              <h3 class="text-xs font-semibold text-[var(--t-ink)] flex items-center gap-2"><svg class="w-4 h-4 text-[#00cc66]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 10v6M2 10l10-5 10 5-10 5z"/><path d="M6 12v5c3 3 9 3 12 0v-5"/></svg> Education</h3>
              <div class="space-y-3">
                {#each record.education as edu (edu['HT No'] || edu.Category)}
                  <div class="rounded-lg border border-[var(--t-surface)] bg-[var(--t-surface-3)] p-2 space-y-1.5">
                    <div class="flex items-center justify-between gap-2">
                      <span class="text-[0.6rem] font-semibold uppercase tracking-wider text-[#9ca3af]">Category</span>
                      <span class="text-xs font-semibold" style="color:{categoryColor(edu.Category || '')}">{edu.Category || '—'}</span>
                    </div>
                    <div class="grid grid-cols-1 gap-1.5 text-xs">
                      <div><span class="text-[#9ca3af]">Board / University:</span> <span class="text-[var(--t-ink-soft)] font-medium">{edu['Board/University'] || '—'}</span></div>
                      <div><span class="text-[#9ca3af]">College:</span> <span class="text-[var(--t-ink-soft)] font-medium">{edu['College Name'] || '—'}</span></div>
                      {#if edu['College Address']}<div><span class="text-[#9ca3af]">Address:</span> <span class="text-[var(--t-ink-soft)]">{edu['College Address']}</span></div>{/if}
                      <div class="flex gap-4"><span class="text-[#9ca3af]">HT No:</span> <span class="text-[var(--t-ink-soft)] font-medium tabular-nums">{edu['HT No'] || '—'}</span> {#if edu.From || edu.To}<span class="text-[#9ca3af]">From–To:</span> <span class="text-[var(--t-ink-soft)]">{edu.From || ''}{#if edu.From && edu.To} – {/if}{edu.To || ''}</span>{/if}</div>
                    </div>
                  </div>
                {/each}
              </div>
            </div>
          {/if}

          {#if record.work_experience && (record.work_experience.Address || record.work_experience.State || record.work_experience.District || record.work_experience['Pin code'])}
            <div class="border-t border-[var(--t-surface)] pt-2 space-y-2">
              <h3 class="text-xs font-semibold text-[var(--t-ink)] flex items-center gap-2"><svg class="w-4 h-4 text-[#00cc66]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2"/></svg> Work Experience</h3>
              <dl class="grid grid-cols-1 gap-2 text-xs">
                <div><dt class="text-[#9ca3af] text-[0.65rem] font-semibold uppercase tracking-wider">Address</dt><dd class="text-[var(--t-ink-soft)] mt-0.5">{displayWork(record.work_experience.Address)}</dd></div>
                <div class="grid grid-cols-2 gap-2">
                  <div><dt class="text-[#9ca3af] text-[0.65rem] font-semibold uppercase tracking-wider">State</dt><dd class="text-[var(--t-ink-soft)] mt-0.5">{displayWork(record.work_experience.State)}</dd></div>
                  <div><dt class="text-[#9ca3af] text-[0.65rem] font-semibold uppercase tracking-wider">District</dt><dd class="text-[var(--t-ink-soft)] mt-0.5">{displayWork(record.work_experience.District)}</dd></div>
                </div>
                <div><dt class="text-[#9ca3af] text-[0.65rem] font-semibold uppercase tracking-wider">Pin Code</dt><dd class="text-[var(--t-ink-soft)] mt-0.5">{displayWork(record.work_experience['Pin code'])}</dd></div>
              </dl>
            </div>
          {/if}

          <div class="border-t border-[var(--t-surface)] pt-3 flex justify-end">
            <button onclick={printPage} class="flex items-center gap-1.5 px-2.5 py-1 rounded border border-[var(--t-border)] text-xs font-medium text-[var(--t-ink-soft)] hover:bg-[var(--t-row)] transition-colors">
              <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5"/><path d="M18 18h2a2 2 0 0 0 2-2v-5"/><rect x="6" y="14" width="12" height="8"/></svg> Print
            </button>
          </div>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  @media print {
    div[role="dialog"] { display: none !important; }
  }
</style>
