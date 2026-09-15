import { PUBLIC_R2_PHOTO_BASE } from '$env/static/public';

/**
 * Public R2 bucket URLs (CODE_REVIEW.md L2). Derived from the single
 * PUBLIC_R2_PHOTO_BASE Pages env var (`.../photos`) so the host lives in one
 * place instead of being hardcoded across components and the Python pipeline.
 */

const origin = (() => {
  // Never throw at module import: one bad Pages env var must degrade the
  // photo/notice/dispatch URLs, not 500 every importing page.
  try {
    return new URL(PUBLIC_R2_PHOTO_BASE).origin;
  } catch {
    return '';
  }
})();

export const R2_ORIGIN = origin;
export const R2_PHOTOS = PUBLIC_R2_PHOTO_BASE;
export const R2_DISPATCH = `${origin}/dispatch`;
export const R2_NOTICES = `${origin}/notice`;
