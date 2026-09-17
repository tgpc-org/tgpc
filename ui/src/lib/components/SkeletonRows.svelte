<script lang="ts">
  let { rows = 8 as number }: { rows?: number } = $props();
</script>

<div class="space-y-3 py-4" role="status" aria-label="Loading results">
  {#each Array(rows) as _, i (i)}
    <div class="h-4 bg-[var(--t-surface)] rounded animate-pulse" style="width:{45 + ((i * 37) % 50)}%"></div>
  {/each}
</div>
