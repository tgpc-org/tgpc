<script lang="ts">
  import { onMount } from 'svelte';

  const MONTHS = ['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
  let now = $state(new Date());

  onMount(() => {
    const id = setInterval(() => now = new Date(), 1000);
    return () => clearInterval(id);
  });

  let dateStr = $derived(`${String(now.getDate()).padStart(2,'0')} ${MONTHS[now.getMonth()]} ${now.getFullYear()}`);
  let timeStr = $derived(`${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}:${String(now.getSeconds()).padStart(2,'0')}`);
</script>

<!-- Inherits the shell's muted token: dimming with opacity dropped the
     contrast below AA on the light background. -->
<span class="tabular-nums whitespace-nowrap">{dateStr} {timeStr}</span>
