<script lang="ts">
  import { invalidateAll } from '$app/navigation';
  import type { UsageReport } from '$lib/types';
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
  let tab = $state<'usage' | 'links'>('usage');
  let report = $state<UsageReport | null>(null);
  let usageLoading = $state(false);
  let usageError = $state('');

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
          class="mt-2 w-full bg-[#00cc66] text-white text-sm font-semibold px-4 py-1.5 rounded disabled:opacity-50"
        >{loading ? 'Checking...' : 'Unlock'}</button>
        {#if error}
          <p class="text-xs text-[#ef4444] mt-2">{error}</p>
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
              class="shrink-0 bg-[#00cc66] text-white text-xs font-semibold px-3 py-1 rounded hover:bg-[#00b359] disabled:opacity-50">
              {usageLoading ? 'Loading...' : 'Refresh'}
            </button>
            <span class="text-xs text-[#9ca3af] ml-1">
              {#if report?.generated_at}
                Updated {new Date(report.generated_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
              {/if}
            </span>
          </div>

          {#if report?.missing_vars?.length}
            <div class="text-xs bg-[rgba(239,68,68,0.06)] border border-[rgba(239,68,68,0.35)] text-[#ef4444] rounded px-3 py-2 mb-4">
              <strong>Note:</strong> Some credentials are not configured as Cloudflare Pages environment variables:
              {report.missing_vars.join(', ')}. Set them in the Cloudflare dashboard for live data.
            </div>
          {/if}

          {#if usageError}
            <div class="text-xs text-[#ef4444] mb-4">{usageError}</div>
          {/if}

          {#if report}
            {#each report.services as service (service.name)}
              <div class="mb-5 border border-[var(--t-border)] rounded-lg overflow-hidden">
                <div class="bg-[var(--t-surface-2)] px-3 py-2 font-semibold text-sm border-b border-[var(--t-border)]">
                  {service.name}
                </div>
                {#if service.error}
                  <div class="px-3 py-3 text-xs text-[#ef4444]">{service.error}</div>
                {:else if service.items.length === 0}
                  <div class="px-3 py-3 text-xs text-[#9ca3af]">No data available.</div>
                {:else}
                  <table class="w-full text-xs">
                    <thead>
                      <tr class="text-left text-[#9ca3af] border-b border-[var(--t-border)]">
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
            <div class="px-3 py-3 text-xs text-[#9ca3af]">No usage data yet.</div>
          {/if}
        </div>
      {:else}
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
                      <span class="flex-1 text-xs font-mono text-[#2563eb] break-all">{item.url}</span>
                      <button
                        onclick={() => copyUrl(item.url)}
                        class="shrink-0 text-xs font-semibold px-2 py-1 rounded border border-[var(--t-border)] text-[var(--t-muted)] hover:bg-[var(--t-surface-2)] transition-colors"
                      >{copied === item.url ? 'Copied!' : 'Copy'}</button>
                      <a
                        href={item.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        class="shrink-0 bg-[#00cc66] text-white text-xs font-semibold px-2 py-1 rounded hover:bg-[#00b359] transition-colors"
                      >Open <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="w-3 h-3 inline" aria-hidden="true"><path d="M15 3h6v6"></path><path d="M10 14 21 3"></path><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path></svg></a>
                    </div>
                  {/each}
                </div>
              </div>
            {/each}
          </div>
        </div>
      {/if}
    </div>
  {/if}
</div>