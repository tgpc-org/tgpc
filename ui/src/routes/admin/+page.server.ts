import { dev } from '$app/env';
import { adminLinkGroups } from '#lib/server/adminLinks.js';
import { isAuthed } from '#lib/server/auth.js';
import type { PageServerLoad } from './$types';

/**
 * Gate the admin payload server-side.
 *
 * The link list is only included in the response for an authenticated session,
 * so it never reaches an unauthenticated visitor's bundle. `authed` is derived
 * from a signed HttpOnly cookie, not from client state.
 */
export const load: PageServerLoad = async ({ cookies, platform }) => {
  // Dev convenience only. `dev` is statically replaced at build time, so this
  // branch is eliminated from production output.
  const authed = dev || await isAuthed(cookies, platform);

  if (!authed) {
    return { authed: false as const, groups: [] };
  }

  return { authed: true as const, groups: adminLinkGroups(platform) };
};
