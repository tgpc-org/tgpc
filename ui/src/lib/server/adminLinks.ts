/**
 * Internal TGPC endpoint reference, for the admin console.
 *
 * Server-only: this list is a reconnaissance map of a government system's
 * internal endpoints, so it must never reach the browser bundle. It is served
 * only to an authenticated session, via `routes/admin/+page.server.ts`.
 *
 * SECURITY (CODE_REVIEW.md C1): the two credential-bearing URLs read their
 * real values from Cloudflare Pages environment variables ONLY —
 *   ADMIN_LINK_PHARMACIST_URL / ADMIN_LINK_EMAIL_VERIFY_URL
 * The fallbacks below are PLACEHOLDERS (same shape as the real thing) so the
 * URL structure stays documented here without any live token in version
 * control. Personal reference copy of the real values:
 * ~/.config/tgpc/admin-links.txt
 */

import type { LinkGroup } from '#lib/types.js';

const base = 'https://www.pharmacycouncil.telangana.gov.in';

export function adminLinkGroups(platform: App.Platform | undefined): LinkGroup[] {
  const env = platform?.env;

  const pharmacistUrl =
    env?.['ADMIN_LINK_PHARMACIST_URL'] ||
    `${base}/pharmacy/viewpharmacist?referenceid=REFERENCEID-HERE&random_no1=RANDOMNO1-HERE`;

  const emailVerifyUrl =
    env?.['ADMIN_LINK_EMAIL_VERIFY_URL'] ||
    `${base}/pharmacy/getemailverify?rid1=RID1-HERE&rid2=RID2-HERE&rid3=RID3-UUID-HERE`;

  const all: LinkGroup[] = [
    {
      name: 'Search & Profile',
      items: [
        ...(pharmacistUrl
          ? [{ heading: 'Pharmacist Detail View', url: pharmacistUrl, desc: 'View individual pharmacist profile with full details' }]
          : []),
        { heading: 'Pharmacist Search (POST)', url: `${base}/pharmacy/getsearchpharmacist`, desc: 'Search endpoint — POST registration_no to get results' },
        { heading: 'Admin Dashboard', url: `${base}/pharmacy/dashboard`, desc: 'TGPC admin dashboard' },
      ],
    },
    {
      name: 'Document Management',
      items: [
        { heading: 'Upload Rejected Docs', url: `${base}/pharmacy/editupload_rejected_docs`, desc: 'Form to upload documents that were rejected' },
        { heading: 'Rejected Docs API (POST)', url: `${base}/pharmacy/getrejecteddocsupload`, desc: 'API endpoint for rejected document uploads' },
        { heading: 'BillDesk TID Excel Upload', url: `${base}/site/billdesk_tid_excelreport_upload`, desc: 'Upload BillDesk TID Excel reports' },
      ],
    },
    {
      name: 'Workflow & Tracking',
      items: [
        { heading: 'Workflow Status', url: `${base}/pharmacy/workflowstatus`, desc: 'Detailed workflow tracking for applications' },
        { heading: 'Workflow Info API (POST)', url: `${base}/pharmacy/workflowstatusinfo.action`, desc: 'API endpoint for workflow status information' },
      ],
    },
    {
      name: 'Payments & Verification',
      items: [
        { heading: 'Payment Status Check', url: `${base}/pharmacy/getpmentstatusmeseva`, desc: 'Check payment status via Meseva' },
        ...(emailVerifyUrl
          ? [{ heading: 'Email Verify', url: emailVerifyUrl, desc: 'Verify pharmacist email with verification tokens' }]
          : []),
      ],
    },
    {
      name: 'Reports & Admin',
      items: [
        { heading: 'Dispatch List Report', url: `${base}/pharmacy/dispatchlistreprt`, desc: 'View dispatch list report' },
        { heading: 'Admin Console', url: `${base}/aconsole/adminconsole`, desc: 'TGPC admin console panel' },
      ],
    },
  ];

  return all.filter((g) => g.items.length > 0);
}
