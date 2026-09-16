import type { Category } from './types';

export const TGPC = {
  green: '#00cc66',
  red: '#ef4444',
  grey: '#9ca3af',
  blue: '#2563eb'
} as const;

export const CATEGORY_COLORS: Record<Category, string> = {
  BPharm: '#2563eb',
  DPharm: '#00cc66',
  MPharm: '#111827',
  PharmD: '#ef4444',
  QC: '#00b359',
  QP: '#9ca3af'
};

export const CATEGORIES: Category[] = ['BPharm', 'DPharm', 'MPharm', 'PharmD', 'QC', 'QP'];

export const CATEGORY_KEYS = ['BPharm', 'DPharm', 'MPharm', 'PharmD', 'QC', 'QP'] as const;
