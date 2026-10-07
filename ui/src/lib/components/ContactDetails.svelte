<script lang="ts">
  // Contact details section for profile surfaces (drawer + record page).
  // Client-fetched from the session-gated /api/admin/contacts endpoint, so
  // contact PII never appears in server-rendered HTML — only an authed
  // browser that asks for it receives it. Renders nothing when the record
  // has no DG contact row.

  let { reg, compact = false }: { reg: string; compact?: boolean } = $props();

  interface Contact {
    [k: string]: string | null;
  }

  const FIELDS: Array<[string, string]> = [
    ['mobile_no', 'Mobile'],
    ['email_id', 'Email'],
    ['dob', 'Date of birth'],
    ['date_of_registration', 'Registered on'],
    ['renewal_validity', 'Renewal valid till'],
    ['home_address', 'Home address'],
    ['home_state', 'Home state'],
    ['work_study_address', 'Work/study address'],
    ['work_study_state', 'Work/study state']
  ];

  let contact = $state<Contact | null>(null);
  let seq = 0;

  $effect(() => {
    const mySeq = ++seq;
    contact = null;
    if (!reg) return;
    fetch(`/api/admin/contacts?reg=${encodeURIComponent(reg)}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (mySeq !== seq) return;
        const c = (d?.contact ?? null) as Contact | null;
        contact =
          c && Object.values(c).some((v) => v !== null && v !== '') ? c : null;
      })
      .catch(() => {
        if (mySeq === seq) contact = null;
      });
  });
</script>

{#if contact}
  <div class="border-t border-[var(--t-surface)] pt-2 space-y-2">
    {#if compact}
      <h3 class="text-xs font-semibold text-[var(--t-ink)] flex items-center gap-2"><svg class="w-4 h-4 text-[#00cc66]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z"/></svg> Contact Details</h3>
      <dl class="grid grid-cols-1 gap-2 text-xs">
        {#each FIELDS as [key, label] (key)}
          {#if contact[key]}
            <div><dt class="text-[var(--t-muted)] text-[0.65rem] font-semibold uppercase tracking-wider">{label}</dt><dd class="text-[var(--t-ink-soft)] mt-0.5 break-all">{contact[key]}</dd></div>
          {/if}
        {/each}
      </dl>
    {:else}
      <h2 class="text-sm font-semibold text-[var(--t-ink)] flex items-center gap-2">
        <svg class="w-5 h-5 text-[#00cc66]" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" />
        </svg>
        Contact Details
      </h2>
      <dl class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[0.875rem]">
        {#each FIELDS as [key, label] (key)}
          {#if contact[key]}
            <div>
              <dt class="text-[var(--t-muted)] text-[0.7rem] font-semibold uppercase tracking-wider">{label}</dt>
              <dd class="text-[var(--t-ink-soft)] mt-0.5 break-all">{contact[key]}</dd>
            </div>
          {/if}
        {/each}
      </dl>
    {/if}
  </div>
{/if}
