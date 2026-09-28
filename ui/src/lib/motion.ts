// CSS covers stylesheet animations via the global @media block in app.css;
// svelte transitions declare their duration when the transition block
// instantiates, so JS-driven ones (fly/fade) consult this instead.
export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
