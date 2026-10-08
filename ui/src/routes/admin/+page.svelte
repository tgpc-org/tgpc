<script lang="ts">
  import { invalidateAll } from '$app/navigation';
  import type { ContactLookup, UsageReport } from '$lib/types';
  import type { OpsSnapshot } from '$lib/ops';
  import { fmtInt } from '$lib/ops';
  import type { PageData } from './$types';

  let { data }: { data: PageData } = $props();

  // Authorization is decided server-side in +page.server.ts and the link list
  // is only present in `data.groups` for an authenticated session, so flipping
  // client state cannot reveal anything.
  const authed = $derived(data.authed);
  const groups = $derived(data.groups);

  let secret = $state('');
  let show = $state(false);
  let error = $state('');
  let loading = $state(false);
  let tab = $state<'usage' | 'links' | 'contacts' | 'ops'>('usage');
  let report = $state<UsageReport | null>(null);
  let usageLoading = $state(false);
  let usageError = $state('');

  let lookupReg = $state('');
  let lookup = $state<ContactLookup | null>(null);
  let lookupLoading = $state(false);
  let lookupError = $state('');

  const CONTACT_LABELS: Record<string, string> = {
    dob: 'Date of birth',
    date_of_registration: 'Registered on',
    home_address: 'Home address',
    home_state: 'Home state',
    work_study_address: 'Work/study address',
    work_study_state: 'Work/study state',
    mobile_no: 'Mobile',
    email_id: 'Email'
  };

  function contactEmpty(c: Record<string, string | null> | null): boolean {
    if (!c) return true;
    return Object.values(c).every((v) => v === null || v === '');
  }

  async function lookupContact() {
    const reg = lookupReg.trim();
    if (!reg || lookupLoading) return;
    lookupLoading = true;
    lookupError = '';
    lookup = null;
    try {
      const r = await fetch(`/api/admin/contacts?reg=${encodeURIComponent(reg)}`);
      if (r.status === 400) {
        lookupError = 'Enter a valid registration number (e.g. TG061874)';
      } else if (r.status === 403) {
        lookupError = 'Unauthorized';
      } else if (r.status === 404) {
        lookupError = 'No such record';
      } else if (r.ok) {
        lookup = (await r.json()) as ContactLookup;
      } else {
        lookupError = 'Server error';
      }
    } catch {
      lookupError = 'Connection error';
    }
    lookupLoading = false;
  }

  async function login() {
    if (!secret.trim()) return;
    loading = true;
    error = '';
    try {
      const r = await fetch('/api/admin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ secret: secret.trim() })
      });
      if (r.status === 403) {
        error = 'Wrong password';
        loading = false;
        return;
      }
      if (r.ok) {
        // The secret is no longer needed client-side — the session cookie
        // carries authorization from here on.
        secret = '';
        await invalidateAll();
        await loadUsage();
      } else {
        error = 'Server error';
      }
    } catch {
      error = 'Connection error';
    }
    loading = false;
  }

  async function loadUsage() {
    usageLoading = true;
    usageError = '';
    try {
      const r = await fetch('/api/usage');
      if (r.status === 403) {
        usageError = 'Unauthorized';
      } else if (r.ok) {
        report = await r.json();
      } else {
        usageError = 'Server error';
      }
    } catch {
      usageError = 'Connection error';
    }
    usageLoading = false;
  }

  let copied = $state('');

  let ops = $state<OpsSnapshot | null>(null);
  let opsLoading = $state(false);
  let opsError = $state('');
  let ctlLoading = $state(false);
  let ctlError = $state('');
  let opsTimer: ReturnType<typeof setInterval> | undefined;

  async function loadOps() {
    opsLoading = true;
    opsError = '';
    try {
      const r = await fetch('/api/admin/ops');
      if (r.status === 403) {
        opsError = 'Unauthorized';
      } else if (r.ok) {
        ops = (await r.json()) as OpsSnapshot;
      } else {
        opsError = 'Server error';
      }
    } catch {
      opsError = 'Connection error';
    }
    opsLoading = false;
  }

  async function setHalt(halt: boolean) {
    if (ctlLoading) return;
    const note = halt ? 'halted from admin OPS tab' : 'resumed from admin OPS tab';
    ctlLoading = true;
    ctlError = '';
    try {
      const r = await fetch('/api/admin/ops/ctl', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ halt, note })
      });
      if (!r.ok) {
        ctlError = r.status === 403 ? 'Unauthorized' : 'Control failed';
      } else {
        await loadOps();
      }
    } catch {
      ctlError = 'Connection error';
    }
    ctlLoading = false;
  }

  function startOpsPolling() {
    stopOpsPolling();
    opsTimer = setInterval(() => {
      if (tab === 'ops' && !document.hidden) loadOps();
    }, 15000);
  }

  function stopOpsPolling() {
    if (opsTimer) clearInterval(opsTimer);
    opsTimer = undefined;
  }

  $effect(() => {
    if (tab === 'ops' && authed) {
      loadOps();
      startOpsPolling();
    } else {
      stopOpsPolling();
    }
    return () => stopOpsPolling();
  });

  async function copyUrl(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      copied = url;
      setTimeout(() => { copied = ''; }, 2000);
    } catch {}
  }

  async function logout() {
    secret = '';
    tab = 'usage';
    report = null;
    usageError = '';
    lookupReg = '';
    lookup = null;
    lookupError = '';
    try {
      await fetch('/api/admin', { method: 'DELETE' });
    } catch {}
    await invalidateAll();
  }

  let panel = $state<HTMLDivElement | undefined>();
  let panelHeight = $state(0);

  function measurePanel() {
    if (!panel) return;
    const panelTop = panel.getBoundingClientRect().top;
    const footer = document.querySelector('footer');
    const footerTop = footer ? footer.getBoundingClientRect().top : window.innerHeight;
    const available = Math.round(footerTop - panelTop - 10);
    if (available > 0) {
      panelHeight = available;
    }
  }

  let resizeObserver: ResizeObserver | undefined;
  let measureRaf = 0;

  $effect(() => {
    if (!panel) return;
    cancelAnimationFrame(measureRaf);
    measureRaf = requestAnimationFrame(() => {
      measurePanel();
      measureRaf = 0;
    });
    resizeObserver?.disconnect();
    resizeObserver = new ResizeObserver(measurePanel);
    resizeObserver.observe(panel);
    window.addEventListener('resize', measurePanel);
    return () => {
      cancelAnimationFrame(measureRaf);
      resizeObserver?.disconnect();
      window.removeEventListener('resize', measurePanel);
    };
  });
</script>

<svelte:head>
  <title>Admin — TGPC RPh Index</title>
</svelte:head>

<div bind:this={panel} style="height:{panelHeight}px;overflow:hidden;display:flex;flex-direction:column">
  {#if !authed}
    <div class="text-center py-16">
      <h1 class="text-2xl font-bold mb-1">Admin</h1>
      <form onsubmit={(e) => { e.preventDefault(); login(); }} class="max-w-xs mx-auto">
        <div class="flex items-center gap-2 border border-[var(--t-border)] rounded focus-within:border-[#00cc66] bg-[var(--t-bg)]">
          <input
            type={show ? 'text' : 'password'}
            bind:value={secret}
            placeholder="Password"
            disabled={loading}
            class="flex-1 outline-none border-none bg-transparent px-3 py-1.5 text-sm min-w-0"
          >
          <button
            type="button"
            onclick={() => show = !show}
            class="shrink-0 px-2 py-1 text-[var(--t-muted)] hover:text-[#00cc66] text-sm"
            aria-label={show ? 'Hide password' : 'Show password'}
            tabindex="-1"
          >{#if show}
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-4 h-4" aria-hidden="true"><path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"></path><path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"></path><path d="M6.61 6.61A13.526 13.526 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"></path><line x1="2" x2="22" y1="2" y2="22"></line></svg>
          {:else}
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-4 h-4" aria-hidden="true"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"></path><circle cx="12" cy="12" r="3"></circle></svg>
          {/if}</button>
        </div>
        <button
          type="submit"
          disabled={loading}
          class="mt-2 w-full bg-[#00cc66] text-[var(--t-ink)] text-sm font-semibold px-4 py-2 rounded hover:opacity-90 disabled:opacity-50 transition-opacity"
        >{loading ? 'Checking...' : 'Unlock'}</button>
        {#if error}
          <p class="text-xs text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)] rounded px-2 py-1 mt-2 inline-block">{error}</p>
        {/if}
      </form>
    </div>
  {:else}
    <div class="flex items-center justify-between pb-1">
      <div class="flex items-center gap-0.5" style="font-size:0.7rem;padding-bottom:3px">
        <button style="text-decoration:none;padding:2px 4px;font-weight:700;color:#ef4444;white-space:nowrap;cursor:default;border:none;background:transparent">ADMIN</button>
        <span style="color:var(--t-border-soft);font-weight:300;padding:0;user-select:none">—</span>
        <button onclick={() => tab = 'usage'}
          style="text-decoration:none;padding:2px 4px;font-weight:700;color:{tab === 'usage' ? '#00cc66' : 'var(--t-muted)'};white-space:nowrap;cursor:pointer;border:none;background:transparent">USAGE</button>
        <span style="color:var(--t-border-soft);font-weight:300;padding:0;user-select:none">/</span>
        <button onclick={() => tab = 'links'}
          style="text-decoration:none;padding:2px 4px;font-weight:700;color:{tab === 'links' ? '#00cc66' : 'var(--t-muted)'};white-space:nowrap;cursor:pointer;border:none;background:transparent">INTERNAL LINKS</button>
        <span style="color:var(--t-border-soft);font-weight:300;padding:0;user-select:none">/</span>
        <button onclick={() => tab = 'contacts'}
          style="text-decoration:none;padding:2px 4px;font-weight:700;color:{tab === 'contacts' ? '#00cc66' : 'var(--t-muted)'};white-space:nowrap;cursor:pointer;border:none;background:transparent">CONTACTS</button>
        <span style="color:var(--t-border-soft);font-weight:300;padding:0;user-select:none">/</span>
        <button onclick={() => tab = 'ops'}
          style="text-decoration:none;padding:2px 4px;font-weight:700;color:{tab === 'ops' ? '#00cc66' : 'var(--t-muted)'};white-space:nowrap;cursor:pointer;border:none;background:transparent">OPS</button>
      </div>
      <button onclick={logout}
        class="shrink-0 text-xs font-semibold px-3 py-1.5 rounded border border-[var(--t-border)] text-[var(--t-muted)] hover:bg-[var(--t-surface-2)] hover:text-[#ef4444] transition-colors">
        LOGOUT
      </button>
    </div>

    <div style="flex:1;min-height:0;overflow:hidden;display:flex;flex-direction:column">
      {#if tab === 'usage'}
        <div style="flex:1;min-height:0;overflow-y:auto">
          <div class="flex items-center justify-end mb-2 whitespace-nowrap">
            <button onclick={loadUsage} disabled={usageLoading}
              class="shrink-0 bg-[#00cc66] text-[var(--t-ink)] text-xs font-semibold px-3 py-1.5 rounded hover:opacity-90 disabled:opacity-50 transition-opacity">
              {usageLoading ? 'Loading...' : 'Refresh'}
            </button>
            <span class="text-xs text-[var(--t-muted)] ml-1">
              {#if report?.generated_at}
                Updated {new Date(report.generated_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
              {/if}
            </span>
          </div>

          {#if report?.missing_vars?.length}
            <div class="text-xs bg-[rgba(239,68,68,0.12)] border border-[rgba(239,68,68,0.35)] text-[var(--t-ink)] rounded px-3 py-2 mb-4">
              <strong>Note:</strong> Some credentials are not configured as Cloudflare Pages environment variables:
              {report.missing_vars.join(', ')}. Set them in the Cloudflare dashboard for live data.
            </div>
          {/if}

          {#if usageError}
            <div class="inline-block text-xs text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)] rounded px-2 py-1 mb-4">{usageError}</div>
          {/if}

          {#if report}
            {#each report.services as service (service.name)}
              <div class="mb-5 border border-[var(--t-border)] rounded-lg overflow-hidden">
                <div class="bg-[var(--t-surface-2)] px-3 py-2 font-semibold text-sm border-b border-[var(--t-border)]">
                  {service.name}
                </div>
                {#if service.error}
                  <div class="px-3 py-3 text-xs text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)]">{service.error}</div>
                {:else if service.items.length === 0}
                  <div class="px-3 py-3 text-xs text-[var(--t-muted)]">No data available.</div>
                {:else}
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="text-left text-[var(--t-muted)] border-b border-[var(--t-border)]">
                        <th class="px-3 py-1.5 font-medium">Metric</th>
                        <th class="px-3 py-1.5 font-medium text-right">Value</th>
                      </tr>
                    </thead>
                    <tbody>
                      {#each service.items as item (item.label)}
                        <tr class="border-b border-[var(--t-border)] last:border-b-0">
                          <td class="px-3 py-1.5">{item.label}</td>
                          <td class="px-3 py-1.5 text-right font-mono">{item.used}</td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                {/if}
              </div>
            {/each}
          {:else if !usageLoading && !usageError}
            <div class="px-3 py-3 text-xs text-[var(--t-muted)]">No usage data yet.</div>
          {/if}
        </div>
      {:else if tab === 'links'}
        <div class="border border-[var(--t-border)] rounded-lg overflow-hidden" style="flex:1;min-height:0;overflow-y:auto;display:flex;flex-direction:column">
          <div class="divide-y divide-[var(--t-border)]" style="flex:1;display:flex;flex-direction:column">
            {#each groups as group (group.name)}
              <div style="flex:1">
                <div class="bg-[var(--t-surface-2)] px-3 py-2 font-semibold text-sm border-b border-[var(--t-border)] text-[var(--t-ink)]">
                  {group.name}
                </div>
                <div class="divide-y divide-[var(--t-border)]">
                  {#each group.items as item (item.url)}
                    <div class="flex items-center gap-2 px-3 py-1.5">
                      <span class="flex-1 text-xs font-mono text-[var(--t-link)] break-all">{item.url}</span>
                      <button
                        onclick={() => copyUrl(item.url)}
                        class="shrink-0 text-xs font-semibold px-2 py-1 rounded border border-[var(--t-border)] text-[var(--t-muted)] hover:bg-[var(--t-surface-2)] transition-colors"
                      >{copied === item.url ? 'Copied!' : 'Copy'}</button>
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        class="shrink-0 bg-[#00cc66] text-[var(--t-ink)] text-xs font-semibold px-2 py-1.5 rounded hover:opacity-90 transition-opacity"
                      >Open <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3 inline" aria-hidden="true"><path d="M15 3h6v6"></path><path d="M10 14 21 3"></path><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path></svg></a>
                    </div>
                  {/each}
                </div>
              </div>
            {/each}
          </div>
        </div>
      {:else if tab === 'contacts'}
        <div style="flex:1;min-height:0;overflow-y:auto">
          <form onsubmit={(e) => { e.preventDefault(); lookupContact(); }} class="flex items-center gap-2 mb-3">
            <input
              bind:value={lookupReg}
              placeholder="Registration number (e.g. TG061874)"
              disabled={lookupLoading}
              autocomplete="off"
              spellcheck={false}
              class="flex-1 outline-none border border-[var(--t-border)] rounded px-3 py-1.5 text-sm bg-transparent focus-within:border-[#00cc66] min-w-0 font-mono"
            >
            <button
              type="submit"
              disabled={lookupLoading}
              class="shrink-0 bg-[#00cc66] text-[var(--t-ink)] text-xs font-semibold px-3 py-1.5 rounded hover:opacity-90 disabled:opacity-50 transition-opacity">
              {lookupLoading ? 'Looking up...' : 'Look up'}
            </button>
          </form>

          {#if lookupError}
            <div class="inline-block text-xs text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)] rounded px-2 py-1 mb-4">{lookupError}</div>
          {/if}

          {#if lookup?.base}
            <div class="mb-5 border border-[var(--t-border)] rounded-lg overflow-hidden">
              <div class="bg-[var(--t-surface-2)] px-3 py-2 font-semibold text-sm border-b border-[var(--t-border)]">
                {lookup.base.registration_number} — {lookup.base.name}
              </div>
              <table class="w-full text-xs">
                <tbody>
                  {#each [['Father', lookup.base.father_name], ['Category', lookup.base.category], ['Gender', lookup.base.gender], ['Status', lookup.base.status], ['Valid till', lookup.base.validity_date]] as [label, value] (label)}
                    <tr class="border-b border-[var(--t-border)] last:border-b-0">
                      <td class="px-3 py-1.5 text-[var(--t-muted)]">{label}</td>
                      <td class="px-3 py-1.5 text-right font-mono">{value || '—'}</td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          {/if}

          {#if lookup}
            {#if contactEmpty(lookup.contact)}
              <div class="px-3 py-3 text-xs text-[var(--t-muted)] border border-[var(--t-border)] rounded-lg">No DG contact record for this registration.</div>
            {:else}
              <div class="mb-5 border border-[var(--t-border)] rounded-lg overflow-hidden">
                <div class="bg-[var(--t-surface-2)] px-3 py-2 font-semibold text-sm border-b border-[var(--t-border)]">
                  Contact details
                </div>
                <table class="w-full text-xs">
                  <tbody>
                    {#each Object.entries(lookup.contact ?? {}) as [key, value] (key)}
                      {#if key !== 'renewal_validity'}
                      <tr class="border-b border-[var(--t-border)] last:border-b-0">
                        <td class="px-3 py-1.5 text-[var(--t-muted)]">{CONTACT_LABELS[key] ?? key}</td>
                        <td class="px-3 py-1.5 text-right font-mono break-all">{value || '—'}</td>
                      </tr>
                      {/if}
                    {/each}
                  </tbody>
                </table>
              </div>
            {/if}
          {/if}
        </div>
      {:else if tab === 'ops'}
        <div style="flex:1;min-height:0;overflow-y:auto">
          <div class="flex items-center justify-end gap-2 mb-2 whitespace-nowrap">
            <span class="text-xs text-[var(--t-muted)]">
              {#if ops?.generated_at}
                Updated {new Date(ops.generated_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
              {/if}
            </span>
            <button onclick={loadOps} disabled={opsLoading}
              class="shrink-0 bg-[#00cc66] text-[var(--t-ink)] text-xs font-semibold px-3 py-1.5 rounded hover:opacity-90 disabled:opacity-50 transition-opacity">
              {opsLoading ? 'Loading...' : 'Refresh'}
            </button>
          </div>

          {#if opsError}
            <div class="inline-block text-xs text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)] rounded px-2 py-1 mb-4">{opsError}</div>
          {/if}
          {#if ctlError}
            <div class="inline-block text-xs text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)] rounded px-2 py-1 mb-4 ml-2">{ctlError}</div>
          {/if}

          {#if ops}
            <div class="mb-5 border border-[var(--t-border)] rounded-lg overflow-hidden">
              <div class="bg-[var(--t-surface-2)] px-3 py-2 font-semibold text-sm border-b border-[var(--t-border)]">Registry</div>
              <table class="w-full text-xs">
                <tbody>
                  <tr class="border-b border-[var(--t-border)]">
                    <td class="px-3 py-1.5 text-[var(--t-muted)]">rph rows</td>
                    <td class="px-3 py-1.5 text-right font-mono">{ops.supabase.error ? ops.supabase.error : fmtInt(ops.supabase.rph)}</td>
                  </tr>
                  <tr class="border-b border-[var(--t-border)]">
                    <td class="px-3 py-1.5 text-[var(--t-muted)]">DG contacts</td>
                    <td class="px-3 py-1.5 text-right font-mono">{ops.supabase.error ? ops.supabase.error : fmtInt(ops.supabase.rph_dg_contacts)}</td>
                  </tr>
                  <tr>
                    <td class="px-3 py-1.5 text-[var(--t-muted)]">last_sync</td>
                    <td class="px-3 py-1.5 text-right font-mono">{ops.supabase.last_sync ? ops.supabase.last_sync.slice(0, 16).replace('T', ' ') : '—'}</td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div class="mb-5 border border-[var(--t-border)] rounded-lg overflow-hidden">
              <div class="bg-[var(--t-surface-2)] px-3 py-2 font-semibold text-sm border-b border-[var(--t-border)]">DG coverage</div>
              {#if 'error' in ops.coverage}
                <div class="px-3 py-3 text-xs text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)]">{ops.coverage.error}</div>
              {:else}
                <table class="w-full text-xs">
                  <tbody>
                    <tr class="border-b border-[var(--t-border)]">
                      <td class="px-3 py-1.5 text-[var(--t-muted)]">Completed</td>
                      <td class="px-3 py-1.5 text-right font-mono">{fmtInt(ops.coverage.completed)}</td>
                    </tr>
                    <tr class="border-b border-[var(--t-border)]">
                      <td class="px-3 py-1.5 text-[var(--t-muted)]">Refused at source</td>
                      <td class="px-3 py-1.5 text-right font-mono">{fmtInt(ops.coverage.terminal)}</td>
                    </tr>
                    <tr class="border-b border-[var(--t-border)]">
                      <td class="px-3 py-1.5 text-[var(--t-muted)]">To go</td>
                      <td class="px-3 py-1.5 text-right font-mono">{fmtInt(ops.coverage.uncovered)}</td>
                    </tr>
                    <tr>
                      <td class="px-3 py-1.5 text-[var(--t-muted)]">Resolved</td>
                      <td class="px-3 py-1.5 text-right font-mono">{ops.coverage.pct_resolved ?? '—'}%</td>
                    </tr>
                  </tbody>
                </table>
              {/if}
            </div>

            <div class="mb-5 border border-[var(--t-border)] rounded-lg overflow-hidden">
              <div class="bg-[var(--t-surface-2)] px-3 py-2 font-semibold text-sm border-b border-[var(--t-border)]">VPS fetch loop</div>
              {#if 'error' in ops.vps && ops.vps.error}
                <div class="px-3 py-3 text-xs text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)]">{ops.vps.error}</div>
              {:else}
                <table class="w-full text-xs">
                  <tbody>
                    <tr class="border-b border-[var(--t-border)]">
                      <td class="px-3 py-1.5 text-[var(--t-muted)]">Batch saved / failed</td>
                      <td class="px-3 py-1.5 text-right font-mono">{fmtInt(ops.vps.done)} / {fmtInt(ops.vps.failed)}</td>
                    </tr>
                    <tr class="border-b border-[var(--t-border)]">
                      <td class="px-3 py-1.5 text-[var(--t-muted)]">Heartbeat</td>
                      <td class="px-3 py-1.5 text-right font-mono">{ops.vps.updated_at ? ops.vps.updated_at.slice(0, 16).replace('T', ' ') : '—'}{ops.vps.age_min != null ? ` (${ops.vps.age_min}m ago)` : ''}</td>
                    </tr>
                    <tr>
                      <td class="px-3 py-1.5 text-[var(--t-muted)]">State</td>
                      <td class="px-3 py-1.5 text-right font-mono">{ops.vps.alive ? 'RUNNING' : ops.vps.halt ? 'HALTED' : 'idle'}</td>
                    </tr>
                  </tbody>
                </table>
                <div class="flex items-center gap-2 px-3 py-2">
                  <button onclick={() => setHalt(true)} disabled={ctlLoading}
                    class="shrink-0 text-xs font-semibold px-3 py-1.5 rounded border border-[rgba(239,68,68,0.35)] text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)] hover:opacity-90 disabled:opacity-50 transition-opacity">
                    {ctlLoading ? 'Working...' : 'Halt loop'}
                  </button>
                  <button onclick={() => setHalt(false)} disabled={ctlLoading}
                    class="shrink-0 bg-[#00cc66] text-[var(--t-ink)] text-xs font-semibold px-3 py-1.5 rounded hover:opacity-90 disabled:opacity-50 transition-opacity">
                    {ctlLoading ? 'Working...' : 'Resume loop'}
                  </button>
                  <span class="text-xs text-[var(--t-muted)]">Applies next batch (R2 control channel). Loop start stays in terminal.</span>
                </div>
              {/if}
            </div>

            <div class="mb-5 border border-[var(--t-border)] rounded-lg overflow-hidden">
              <div class="bg-[var(--t-surface-2)] px-3 py-2 font-semibold text-sm border-b border-[var(--t-border)]">CI runs</div>
              {#if 'error' in ops.ci}
                <div class="px-3 py-3 text-xs text-[var(--t-ink)] bg-[rgba(239,68,68,0.12)]">{ops.ci.error}</div>
              {:else if ops.ci.length === 0}
                <div class="px-3 py-3 text-xs text-[var(--t-muted)]">No runs found.</div>
              {:else}
                <table class="w-full text-xs">
                  <tbody>
                    {#each ops.ci as run (run.name + run.created_at)}
                      <tr class="border-b border-[var(--t-border)] last:border-b-0">
                        <td class="px-3 py-1.5 font-mono">{run.name} ({run.branch})</td>
                        <td class="px-3 py-1.5 text-right font-mono">{run.result} · {run.created_at}</td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              {/if}
            </div>
          {:else if !opsLoading && !opsError}
            <div class="px-3 py-3 text-xs text-[var(--t-muted)]">No ops data yet.</div>
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</div>