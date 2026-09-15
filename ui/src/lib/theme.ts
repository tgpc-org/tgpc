import { browser } from '$app/environment';
import { writable } from 'svelte/store';

export type Theme = 'light' | 'dark';

const STORAGE_KEY = 'tgpc-theme';

/** Saved preference, or the OS scheme on first visit. */
export function initialTheme(): Theme {
  if (!browser) return 'light';
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === 'light' || saved === 'dark') return saved;
  } catch {}
  try {
    if (window.matchMedia('(prefers-color-scheme: dark)').matches) return 'dark';
  } catch {}
  return 'light';
}

function apply(theme: Theme) {
  if (!browser) return;
  document.documentElement.classList.toggle('dark', theme === 'dark');
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {}
  // Keep the mobile browser chrome in sync with the page.
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', theme === 'dark' ? '#111827' : '#00cc66');
}

export const themeName = writable<Theme>('light');

export function initTheme() {
  const t = initialTheme();
  themeName.set(t);
  apply(t);
}

export function toggleTheme() {
  themeName.update((t) => {
    const next: Theme = t === 'dark' ? 'light' : 'dark';
    apply(next);
    return next;
  });
}
