import type { PageServerLoad } from './$types';
import { error } from '@sveltejs/kit';
import { getRecord } from '#lib/api.js';
import { PUBLIC_R2_PHOTO_BASE } from '$app/env/public';

export const load: PageServerLoad = async ({ params }: { params: { registration_number: string } }) => {
  const regNo = params.registration_number?.toUpperCase().trim();
  if (!regNo) {
    error(404, 'Registration number required');
  }

  const record = await getRecord(regNo);
  if (!record) {
    error(404, `No record found for ${regNo}`);
  }

  const photo = record.photo_url || `${PUBLIC_R2_PHOTO_BASE}/${regNo}.webp`;
  const title = `${record.name} — RPC ${record.registration_number} | TGPC RPh Index`;

  const description = `Registered Pharmacist: ${record.name} (${record.registration_number}), ${record.category}, ${record.status || 'Status unknown'}, Valid till ${record.validity_date || 'unknown'}. Telangana State Pharmacy Council Index.`;

  return {
    record,
    photo,
    seo: {
      title,
      description,
      ogImage: photo,
      canonical: `https://tgpc.pages.dev/rph/${regNo}`
    }
  };
};
