import adapter from '@sveltejs/adapter-cloudflare';

/** @type {import('@sveltejs/kit').Config} */
const config = {
  kit: {
    adapter: adapter(),
    // Native CSP: SvelteKit injects per-request nonces into the inline
    // scripts it renders AND sends the matching header itself. This is the
    // single source of CSP truth for HTML — do NOT also set
    // Content-Security-Policy in hooks.server.ts or static/_headers
    // (double policies brick hydration). Wildcard hosts keep this working
    // if Supabase/R2 URLs ever change.
    csp: {
      mode: 'auto',
      directives: {
        'default-src': ['self'],
        'script-src': ['self'],
        // Cloud-only fonts: Google Fonts CDN stylesheets + woff2.
        // SvelteKit appends nonces alongside it.
        'style-src': ['self', 'unsafe-inline', 'https://fonts.googleapis.com'],
        'img-src': ['self', 'data:', 'https://*.r2.dev'],
        // wss: for Supabase realtime channel
        'connect-src': ['self', 'https://*.supabase.co', 'wss://*.supabase.co'],
        'font-src': ['self', 'https://fonts.gstatic.com'],
        'object-src': ['none'],
        'base-uri': ['self'],
        'form-action': ['self'],
        'frame-ancestors': ['none']
      }
    }
  }
};

export default config;
