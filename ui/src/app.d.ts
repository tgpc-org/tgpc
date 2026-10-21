declare namespace App {
  interface Platform {
    env: Record<string, string | undefined> & {
      ADMIN_SECRET?: string;
      QUOTA_SECRET?: string;
      DISPATCH?: { list: (options?: { limit?: number }) => Promise<{ objects: { key: string; size?: number }[] }> };
    };
  }
}