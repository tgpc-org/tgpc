<script lang="ts">
  let {
    title = 'No results',
    hint = 'Try a longer name or a full RPC number.'
  }: {
    title?: string;
    hint?: string;
  } = $props();
</script>

<div class="py-10 text-center" role="status">
  <div
    class="mx-auto mb-3 flex h-11 w-11 items-center justify-center rounded-full"
    style="background:rgba(0,204,102,0.08)"
    aria-hidden="true"
  >
    <svg class="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="#00cc66" stroke-width="2" aria-hidden="true">
      <circle cx="11" cy="11" r="8" /><path d="m21 21-4.35-4.35" />
    </svg>
  </div>
  <p class="text-[0.9rem] font-semibold" style="color:var(--t-ink)">{title}</p>
  <p class="mt-1 text-[0.8rem] text-[#9ca3af]">{hint}</p>
</div>
