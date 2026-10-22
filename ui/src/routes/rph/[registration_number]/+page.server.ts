import type { PageServerLoad } from './$types';
import { error } from '@sveltejs/kit';
import { lookupRecord } from '#lib/server/records.js';
import { PUBLIC_R2_PHOTO_BASE } from '$app/env/public';

export const load: PageServerLoad = async ({ params, platform }) => {
	const regNo = params.registration_number?.toUpperCase().trim();
	if (!regNo) {
		error(404, 'Registration number required');
	}

	// Read the registry directly with the service key: no self-HTTP round-trip
	// to /api/record (relative fetches throw in the Workers runtime) and no
	// coupling to that route's per-IP rate limit.
	const result = await lookupRecord(platform, regNo);

	if (result.kind === 'invalid' || result.kind === 'not-found') {
		error(404, `No record found for ${regNo}`);
	}
	if (result.kind === 'unconfigured' || result.kind === 'upstream') {
		// A backend failure is not a missing record: report it as a temporary
		// outage so crawlers retry instead of dropping the page from the index.
		error(503, 'Registry temporarily unavailable');
	}

	const record = result.record;
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
