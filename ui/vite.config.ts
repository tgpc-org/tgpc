import adapter from '@sveltejs/adapter-cloudflare';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [
    tailwindcss(),
    sveltekit({
      adapter: adapter(),
      // Native CSP: SvelteKit injects per-request nonces into the inline
      // scripts it renders AND sends the matching header itself. This is the
      // single source of CSP truth for HTML — do NOT also set
      // Content-Security-Policy in hooks.server.ts or _headers
      // (double policies brick hydration). Wildcard hosts keep this working
      // if Supabase/R2 URLs ever change.
      csp: {
        mode: 'auto',
        directives: {
          'default-src': ['self'],
          'script-src': ['self'],
          // Svelte transitions + inline style attributes need unsafe-inline;
          // SvelteKit appends nonces alongside it.
          'style-src': ['self', 'unsafe-inline'],
          'img-src': ['self', 'data:', 'https://*.r2.dev'],
          // wss: for Supabase realtime channel
          'connect-src': ['self', 'https://*.supabase.co', 'wss://*.supabase.co'],
          'font-src': ['self'],
          'object-src': ['none'],
          'base-uri': ['self'],
          'form-action': ['self'],
          'frame-ancestors': ['none']
        }
      }
    })
  ]
});
