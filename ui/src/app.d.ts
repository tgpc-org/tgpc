declare namespace App {
  interface Platform {
    env: Record<string, string | undefined> & {
      ADMIN_SECRET?: string;
      QUOTA_SECRET?: string;
      DISPATCH?: { list: (options?: { limit?: number }) => Promise<{ objects: { key: string; size?: number }[] }> };
      /** Optional shared rate-limit counter (Workers KV). When absent the
       *  limiter stays per-isolate; see lib/server/rateLimit.ts. */
      RATE_LIMIT?: {
        get(key: string): Promise<string | null>;
        put(key: string, value: string, options?: { expirationTtl?: number }): Promise<void>;
      };
    };
  }
}