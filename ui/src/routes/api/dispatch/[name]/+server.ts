import type { RequestHandler } from './$types';
import { dispatchBucket } from '#lib/server/appEnv.js';
import { DISPATCH_PREFIX, isDispatchName } from '#lib/dispatchNames.js';

export const GET: RequestHandler = async ({ params, platform }) => {
	const name = params.name;

	// Strict validation: a bare dispatch PDF name only — no path separators,
	// no `..`, bounded length, matching the schema. Shared with the listing
	// route (audit F13) so the two cannot disagree about what is servable.
	if (!isDispatchName(name)) {
		return new Response('Not found', { status: 404 });
	}

	const bucket = dispatchBucket(platform);

	if (bucket) {
		try {
			const obj = await bucket.get(DISPATCH_PREFIX + name);
			if (obj) {
				const buf = await obj.arrayBuffer();
				// Serve bytes untouched: rewriting /Title in place changes the
				// file length without rebuilding the PDF xref table,
				// corrupting downloads for strict readers. The
				// Content-Disposition filename already gives the browser tab a
				// sane title.
				const bytes = new Uint8Array(buf);
				const headers = new Headers();
				headers.set('Content-Type', 'application/pdf');
				headers.set(
					'Content-Disposition',
					`inline; filename="${name}"; filename*=UTF-8''${encodeURIComponent(name)}`
				);
				headers.set('Cache-Control', 'public, max-age=60');
				headers.set('Pragma', 'no-cache');
				headers.set('Content-Length', String(bytes.byteLength));
				return new Response(bytes, { headers });
			}
		} catch {
			// Missing binding object or R2 error: fall through to 404.
		}
	}

	return new Response('Not found', { status: 404 });
};
