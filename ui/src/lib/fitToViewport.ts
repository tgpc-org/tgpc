/**
 * Size a scrollable list to the space between itself and the shell footer.
 *
 * The shell (header + fixed footer) supplies its own height, so every list
 * used to hardcode the leftover: `max-height:calc(100vh - 240px)`. That magic
 * number was wrong on every device whose header/footer height differed and had
 * to be re-tuned whenever the shell changed — the list either stopped short of
 * the full viewport or slid under the footer.
 *
 * `availableHeight` is the pure arithmetic (unit-tested); `fitToViewport` is
 * the Svelte action that measures the live DOM and re-measures on layout
 * changes. Both are deliberately free of SvelteKit/Supabase imports so the
 * `node:test` suite can load this module.
 */

/** Gap kept between the bottom of the list and the footer, in px. */
export const FIT_GAP = 10;

/**
 * Pixels a list can occupy before it would touch the footer.
 *
 * Clamped at 0: a zero/negative result means the footer is above the list
 * (short viewport), and a negative max-height would break layout.
 */
export function availableHeight(boxTop: number, footerTop: number, gap: number = FIT_GAP): number {
  if (!Number.isFinite(boxTop) || !Number.isFinite(footerTop)) return 0;
  const available = Math.round(footerTop - boxTop - gap);
  return available > 0 ? available : 0;
}

function footerTop(): number {
  const footer = document.querySelector('footer');
  return footer ? footer.getBoundingClientRect().top : window.innerHeight;
}

/**
 * Svelte action: keep `node` inside the viewport.
 *
 * Sets `max-height` (never `height`) so short lists stay short instead of
 * being stretched to fill the screen. Hidden lists measure as 0 and are left
 * unconstrained — they re-measure when the breakpoint makes them visible,
 * because the element resizing fires the observer.
 *
 * `document.body` is observed as well: opening a filter panel above the list
 * moves it down without changing its own size, and only the body's growth
 * reports that.
 */
export function fitToViewport(node: HTMLElement, gap: number = FIT_GAP) {
  let frame = 0;

  function measure() {
    frame = 0;
    const box = node.getBoundingClientRect();
    // display:none (breakpoint-hidden list) reports 0/0 — leave it alone.
    if (box.height === 0 && box.top === 0) {
      node.style.maxHeight = '';
      return;
    }
    const height = availableHeight(box.top, footerTop(), gap);
    const next = height > 0 ? `${height}px` : '';
    // Assigning only on change avoids a ResizeObserver feedback loop.
    if (node.style.maxHeight !== next) node.style.maxHeight = next;
  }

  function schedule() {
    if (frame) return;
    frame = requestAnimationFrame(measure);
  }

  schedule();
  const observer = new ResizeObserver(schedule);
  observer.observe(node);
  observer.observe(document.body);
  const footer = document.querySelector('footer');
  if (footer) observer.observe(footer);
  window.addEventListener('resize', schedule);

  return {
    update(nextGap: number) {
      gap = nextGap;
      schedule();
    },
    destroy() {
      if (frame) cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener('resize', schedule);
    }
  };
}
