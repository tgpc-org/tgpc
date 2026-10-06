export interface PharmacistRecord {
  registration_number: string;
  name: string;
  father_name: string | null;
  category: Category;
  serial_number?: number;
  gender?: string | null;
  validity_date?: string | null;
  status?: string | null;
  photo_url?: string | null;
  score?: number;
  education?: EducationEntry[];
  work_experience?: WorkExperience;
}

export interface EducationEntry {
  Category?: string;
  'Board/University'?: string;
  'College Name'?: string;
  'College Address'?: string;
  From?: string;
  To?: string;
  'HT No'?: string;
}

export interface WorkExperience {
  Address?: string;
  State?: string;
  District?: string;
  'Pin code'?: string;
}

export type Category = 'BPharm' | 'DPharm' | 'MPharm' | 'PharmD' | 'QC' | 'QP';

/** Admin-only contact lookup result (session-gated endpoint, never public). */
export interface ContactLookup {
  base: PharmacistRecord | null;
  contact: Record<string, string | null> | null;
}

export interface Notice {
  date: string;
  title: string;
  links: NoticeLink[];
}

export interface NoticeLink {
  url: string;
  label: string;
}

export interface DispatchFile {
  name: string;
  size?: number;
  stale?: boolean;
  parsed?: {
    d: string;
    mo: string;
    y: string;
    date: Date;
  } | null;
}

export interface Stats {
  total: number;
  active: number;
  inactive: number;
  BPharm: number;
  DPharm: number;
  MPharm: number;
  PharmD: number;
  QC: number;
  QP: number;
}

export type ConnectionStatus = 'Live' | 'Busy' | 'Offline';

export type CategoryFilter = 'all' | Category;

export interface ServiceUsageItem {
  label: string;
  used: string | null;
  limit: string | null;
  pct: string;
}

export interface ServiceUsage {
  name: string;
  items: ServiceUsageItem[];
  error?: string;
}

export interface UsageReport {
  generated_at: string;
  services: ServiceUsage[];
  missing_vars: string[];
}

export interface LinkItem {
  heading: string;
  url: string;
  desc: string;
}

export interface LinkGroup {
  name: string;
  items: LinkItem[];
}
