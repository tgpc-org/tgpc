declare namespace App {
  /**
   * The subset of the Workers `R2Bucket` API the dispatch routes use.
   *
   * The binding is declared in `wrangler.toml`, and — because
   * adapter-cloudflare v8 does not pass `platform` to `server.respond` — it is
   * reached through `$app/env` (see `src/env.ts` and audit F13), not
   * `event.platform.env`.
   */
  interface DispatchBucket {
    list(options?: { prefix?: string; limit?: number; cursor?: string }): Promise<{
      objects: { key: string; size?: number }[];
      truncated?: boolean;
      cursor?: string;
    }>;
    get(key: string): Promise<{ arrayBuffer(): Promise<ArrayBuffer> } | null>;
  }

  interface Platform {
    env: Record<string, string | undefined> & {
      ADMIN_SECRET?: string;
      QUOTA_SECRET?: string;
      DISPATCH?: DispatchBucket;
    };
  }
}
