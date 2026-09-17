<script lang="ts">
  import type { ConnectionStatus } from '$lib/types';
  import Clock from '$lib/components/Clock.svelte';

  let { status = 'Busy' as ConnectionStatus }: { status?: ConnectionStatus } = $props();

  let config = $derived.by(() => ({
    Live: { bg: 'rgba(0,204,102,0.05)', border: 'rgba(0,204,102,0.35)', text: '#00b359', dot: '#00cc66' },
    Busy: { bg: 'rgba(239,68,68,0.05)', border: 'rgba(239,68,68,0.35)', text: '#ef4444', dot: '#ef4444' },
    Offline: { bg: 'rgba(239,68,68,0.05)', border: 'rgba(239,68,68,0.35)', text: '#ef4444', dot: '#ef4444' }
  })[status]);
</script>

<span
  class="flex w-full items-center justify-center gap-px h-5 px-1.5 rounded-full text-[0.75rem] font-medium box-border overflow-hidden"
  style="background:{config.bg};border:1px solid {config.border};color:{config.text}"
  role="status"
  aria-label="Connection status: {status}"
>
  <span class="w-1.5 h-1.5 rounded-full flex-shrink-0" style="background:{config.dot}"></span>
  <span class="text-[10px] font-medium leading-[18px] inline-block w-[28px] text-center">{status}</span>
  {#if status !== 'Offline'}
    <Clock />
  {/if}
</span>
