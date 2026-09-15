<script lang="ts">
  import { goto } from '$app/navigation';
  import { CATEGORY_COLORS } from '$lib/colors';
  import type { PharmacistRecord } from '$lib/types';

  let { data } = $props();
  let record = $derived((data as { record: PharmacistRecord }).record);
  let photo = $derived((data as { photo: string }).photo);
  let seo = $derived((data as { seo: { title: string; description: string; ogImage: string; canonical: string } }).seo);

  let photoError = $state(false);

  function handlePhotoError() {
    photoError = true;
  }

  function formatDate(dateStr: string | null | undefined): string {
    if (!dateStr) return '—';
    return dateStr;
  }

  function statusColor(status: string | null | undefined): string {
    return status === 'Active' ? '#00cc66' : '#ef4444';
  }

  // Theme-aware: MPharm's #111827 is invisible on night backgrounds, and
  // unknown categories fall back to the muted token (not raw #6b7280).
  function categoryColor(cat: string): string {
    const c = CATEGORY_COLORS[cat as keyof typeof CATEGORY_COLORS];
    if (!c) return 'var(--t-muted)';
    return c === '#111827' ? 'var(--t-ink)' : c;
  }

  function printPage() {
    window.print();
  }

  function backToSearch() {
    if (window.history.length > 1 && document.referrer) window.history.back();
    else goto('/');
  }

  function displayWork(v: string | null | undefined): string {
    if (!v) return '—';
    const t = v.trim();
    if (t === '' || t === '----' || t === '—' || t === '--') return '—';
    return v;
  }
</script>

<svelte:head>
  <title>{seo.title}</title>
  <meta name="description" content={seo.description} />
  <meta property="og:title" content={seo.title} />
  <meta property="og:description" content={seo.description} />
  <meta property="og:image" content={seo.ogImage} />
  <meta property="og:type" content="profile" />
  <meta property="og:url" content={seo.canonical} />
  <link rel="canonical" href={seo.canonical} />
  <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "Person",
      "name": "{record.name}",
      "identifier": "{record.registration_number}",
      "description": "{seo.description}",
      "image": "{seo.ogImage}",
      "url": "{seo.canonical}",
      "worksFor": {
        "@type": "Organization",
        "name": "Telangana State Pharmacy Council"
      },
      "jobTitle": "Registered Pharmacist",
      "gender": "{record.gender || ''}",
      "knowsAbout": ["Pharmacy", "{record.category}"]
    }
  </script>
</svelte:head>

<div class="max-w-3xl mx-auto space-y-3 px-4 py-3">
  <div class="flex items-center gap-3 mb-2">
    <button
      onclick={backToSearch}
      class="flex items-center gap-1.5 px-3 py-1.5 rounded border border-[var(--t-border)] text-[0.75rem] font-medium text-[var(--t-ink-soft)] hover:bg-[var(--t-row)] transition-colors"
      aria-label="Back to search"
    >
      <svg class="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M19 12H5M12 19l-7-7 7-7" />
      </svg>
      Back to Search
    </button>
  </div>

  <!-- Single Module -->
  <div class="bg-[var(--t-bg)] border border-[var(--t-border)] rounded-xl p-3 space-y-3">
    <div class="flex flex-col md:flex-row gap-4 items-start md:items-center">
      <div class="flex-shrink-0 w-24 h-32 md:w-28 md:h-36 rounded-lg bg-[var(--t-surface)] overflow-hidden relative">
        <img
          src={photo}
          alt={`${record.name}'s photo`}
          onerror={handlePhotoError}
          decoding="async"
          fetchpriority="high"
          class="w-full h-full object-cover {photoError ? 'hidden' : ''}"
        />
        {#if photoError}
          <div class="w-full h-full flex items-center justify-center bg-[var(--t-surface)]">
            <svg class="w-12 h-12 text-[var(--t-border-soft)]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2" />
              <circle cx="12" cy="7" r="4" />
            </svg>
          </div>
        {/if}
      </div>

      <div class="flex-1 min-w-0 space-y-2">
        <h1 class="text-xl md:text-2xl font-bold text-[var(--t-ink)] truncate">{record.name}</h1>

        <div class="flex flex-wrap items-center gap-2.5">
          <span class="px-3 py-1 rounded-full text-[0.7rem] font-semibold uppercase tracking-wider tabular-nums"
            style="background:color-mix(in srgb, #2563eb 10%, transparent);color:var(--t-link)">
            RPC: {record.registration_number}
          </span>
          <span class="px-3 py-1 rounded-full text-[0.7rem] font-semibold uppercase tracking-wider"
            style="background:color-mix(in srgb, {categoryColor(record.category)} 10%, transparent);color:{categoryColor(record.category)}">
            {record.category}
          </span>
          <span class="px-3 py-1 rounded-full text-[0.7rem] font-semibold uppercase tracking-wider"
            style="background:{record.status === 'Active' ? 'rgba(0,204,102,0.1)' : 'rgba(239,68,68,0.1)'};color:{statusColor(record.status)}">
            {record.status || 'Unknown'}
          </span>
        </div>

        <div class="flex flex-wrap items-center gap-4 text-[0.875rem] text-[var(--t-muted)]">
          <span>Serial: <span class="text-[var(--t-ink-soft)] font-medium">#{record.serial_number || '—'}</span></span>
          <span>Valid till: <span class="text-[var(--t-ink-soft)] font-medium">{formatDate(record.validity_date)}</span></span>
        </div>
      </div>
    </div>

    <div class="border-t border-[var(--t-surface)] pt-2 flex flex-wrap gap-3 text-[0.875rem]">
      <div class="flex items-center gap-1.5">
        <span class="text-[#9ca3af]">Father:</span>
        <span class="text-[var(--t-ink-soft)] font-medium">{record.father_name || '—'}</span>
      </div>
      {#if record.gender}
        <div class="flex items-center gap-1.5">
          <span class="text-[#9ca3af]">Gender:</span>
          <span class="text-[var(--t-ink-soft)] font-medium">{record.gender}</span>
        </div>
      {/if}
    </div>

    {#if record.education && record.education.length > 0}
      <div class="border-t border-[var(--t-surface)] pt-2 space-y-2">
        <h2 class="text-sm font-semibold text-[var(--t-ink)] flex items-center gap-2">
          <svg class="w-5 h-5 text-[#00cc66]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M22 10v6M2 10l10-5 10 5-10 5z" />
            <path d="M6 12v5c3 3 9 3 12 0v-5" />
          </svg>
          Education
        </h2>
        <div class="space-y-3">
          {#each record.education as edu (edu['HT No'] || edu.Category)}
            <div class="rounded-lg border border-[var(--t-surface)] bg-[var(--t-surface-3)] p-2.5 space-y-1.5">
              <div class="flex items-center justify-between gap-2">
                <span class="text-[0.6rem] font-semibold uppercase tracking-wider text-[#9ca3af]">Category</span>
                <span class="text-sm font-semibold" style="color:{categoryColor(edu.Category || '')}">{edu.Category || '—'}</span>
              </div>
              <div class="grid grid-cols-1 gap-1.5 text-sm">
                <div><span class="text-[#9ca3af]">Board / University:</span> <span class="text-[var(--t-ink-soft)] font-medium">{edu['Board/University'] || '—'}</span></div>
                <div><span class="text-[#9ca3af]">College:</span> <span class="text-[var(--t-ink-soft)] font-medium">{edu['College Name'] || '—'}</span></div>
                {#if edu['College Address']}<div><span class="text-[#9ca3af]">Address:</span> <span class="text-[var(--t-ink-soft)]">{edu['College Address']}</span></div>{/if}
                <div class="flex flex-wrap gap-4"><span class="text-[#9ca3af]">HT No:</span> <span class="text-[var(--t-ink-soft)] font-medium tabular-nums">{edu['HT No'] || '—'}</span> {#if edu.From || edu.To}<span class="text-[#9ca3af]">From–To:</span> <span class="text-[var(--t-ink-soft)]">{edu.From || ''}{#if edu.From && edu.To} – {/if}{edu.To || ''}</span>{/if}</div>
              </div>
            </div>
          {/each}
        </div>
      </div>
    {/if}

    {#if record.work_experience && (record.work_experience.Address || record.work_experience.State || record.work_experience.District || record.work_experience['Pin code'])}
      <div class="border-t border-[var(--t-surface)] pt-2 space-y-2">
        <h2 class="text-sm font-semibold text-[var(--t-ink)] flex items-center gap-2">
          <svg class="w-5 h-5 text-[#00cc66]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="2" y="7" width="20" height="14" rx="2" />
            <path d="M16 7V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v2" />
          </svg>
          Work Experience
        </h2>
        <dl class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[0.875rem]">
          <div>
            <dt class="text-[#9ca3af] text-[0.7rem] font-semibold uppercase tracking-wider">Address</dt>
          <dd class="text-[var(--t-ink-soft)] mt-0.5">{displayWork(record.work_experience.Address)}</dd>
        </div>
        <div>
          <dt class="text-[#9ca3af] text-[0.7rem] font-semibold uppercase tracking-wider">State</dt>
          <dd class="text-[var(--t-ink-soft)] mt-0.5">{displayWork(record.work_experience.State)}</dd>
        </div>
        <div>
          <dt class="text-[#9ca3af] text-[0.7rem] font-semibold uppercase tracking-wider">District</dt>
          <dd class="text-[var(--t-ink-soft)] mt-0.5">{displayWork(record.work_experience.District)}</dd>
        </div>
        <div>
          <dt class="text-[#9ca3af] text-[0.7rem] font-semibold uppercase tracking-wider">Pin Code</dt>
          <dd class="text-[var(--t-ink-soft)] mt-0.5">{displayWork(record.work_experience['Pin code'])}</dd>
          </div>
        </dl>
      </div>
    {/if}
    <div class="border-t border-[var(--t-surface)] pt-3 flex items-center justify-end">
      <button
        onclick={printPage}
        class="flex items-center gap-1.5 px-3 py-1.5 rounded border border-[var(--t-border)] text-xs font-medium text-[var(--t-ink-soft)] hover:bg-[var(--t-row)] transition-colors"
      >
        <svg class="w-3.5 h-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="6 9 6 2 18 2 18 9" />
          <path d="M6 18H4a2 2 0 0 1-2-2v-5" />
          <path d="M18 18h2a2 2 0 0 0 2-2v-5" />
          <rect x="6" y="14" width="12" height="8" />
        </svg>
        Print
      </button>
    </div>
  </div>

  <footer class="text-center text-[0.7rem] text-[#9ca3af] py-4 border-t border-[var(--t-border)]">
    Data sourced from Telangana State Pharmacy Council &middot; <a href="https://tgpc.pages.dev" class="text-[var(--t-link)] hover:underline">tgpc.pages.dev</a>
  </footer>
</div>

<style>
  @media print {
    .max-w-3xl { max-width: none !important; padding: 0 !important; }
    .bg-\[var\(--t-bg\)\] { box-shadow: none !important; border: 1px solid var(--t-border) !important; break-inside: avoid; }
    button { display: none !important; }
    a { text-decoration: none !important; color: inherit !important; }
  }
</style>