<script lang="ts">
  let {
    activeTab = '',
    isDark = false,
    onToggleTheme = () => {}
  }: {
    activeTab?: string;
    isDark?: boolean;
    onToggleTheme?: () => void;
  } = $props();

  function linkColor(tab: string): string {
    return activeTab === tab ? '#00cc66' : 'var(--t-muted)';
  }
</script>

<nav
  aria-label="Primary"
  class="w-full px-4 sm:px-6 border-b"
  style="display:flex;align-items:center;gap:2px;font-size:0.7rem;padding-top:3px;padding-bottom:3px;overflow-x:auto;position:relative;border-color:var(--t-border)"
>
  <a
    href="/"
    aria-current={activeTab === 'search' ? 'page' : undefined}
    style="text-decoration:none;padding:2px 4px;font-weight:700;color:{linkColor('search')};white-space:nowrap;border-bottom:2px solid {activeTab === 'search' ? '#00cc66' : 'transparent'}"
  >SEARCH</a>
  <span style="color:var(--t-border);font-weight:300;padding:0 2px;user-select:none">/</span>
  <a
    href="/notice"
    aria-current={activeTab === 'notice' ? 'page' : undefined}
    style="text-decoration:none;padding:2px 4px;font-weight:700;color:{linkColor('notice')};white-space:nowrap;border-bottom:2px solid {activeTab === 'notice' ? '#00cc66' : 'transparent'}"
  >NOTICES</a>
  <span style="color:var(--t-border);font-weight:300;padding:0 2px;user-select:none">/</span>
  <a
    href="/dispatch"
    aria-current={activeTab === 'dispatch' ? 'page' : undefined}
    style="text-decoration:none;padding:2px 4px;font-weight:700;color:{linkColor('dispatch')};white-space:nowrap;border-bottom:2px solid {activeTab === 'dispatch' ? '#00cc66' : 'transparent'}"
  >DISPATCH LIST</a>
  <button
    onclick={onToggleTheme}
    aria-pressed={isDark}
    aria-label="Toggle day and night mode"
    title={isDark ? 'Switch to day mode' : 'Switch to night mode'}
    style="margin-left:auto;flex-shrink:0;display:inline-flex;align-items:center;justify-content:center;width:26px;height:26px;border-radius:50%;border:1px solid var(--t-border);background:var(--t-surface-2);color:var(--t-muted);cursor:pointer"
  >
    {#if isDark}
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4" /><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41" /></svg>
    {:else}
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z" /></svg>
    {/if}
  </button>
</nav>
