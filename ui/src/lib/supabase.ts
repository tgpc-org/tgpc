import type { RealtimeChannel, SupabaseClient } from '@supabase/supabase-js';
import { PUBLIC_SUPABASE_URL, PUBLIC_SUPABASE_PUBLISHABLE_KEY } from '$env/static/public';

// supabase-js is ~230 KB minified (~60 KB gzipped) — the heaviest asset on the
// site (≈43% of bytes transferred on the homepage per Cloudflare Radar scan
// f43ff50b). Nothing needs it before first paint, so it loads as a lazy chunk
// via dynamic import, triggered on first data request or realtime subscription.
let client: Promise<SupabaseClient> | null = null;

export function getSupabase(): Promise<SupabaseClient> {
  client ??= import('@supabase/supabase-js').then(({ createClient }) =>
    createClient(PUBLIC_SUPABASE_URL, PUBLIC_SUPABASE_PUBLISHABLE_KEY)
  );
  return client;
}

export type { SupabaseClient, RealtimeChannel };
