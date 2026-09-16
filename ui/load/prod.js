import http from 'k6/http';
import { check, sleep } from 'k6';

// Production load profile for tgpc.pages.dev.
//
// Polite by design: pages + /api/notice + /api/dispatch are edge-cached and
// cheap. /api/health hits Supabase (2 queries), so it stays at low weight —
// do NOT raise its share without checking Supabase free-tier quota first
// (`make quota`). Direct-Supabase search (the RPC path) is deliberately
// excluded for the same reason.
//
// Usage:
//   k6 run load/prod.js                        # smoke (default, CI-safe)
//   k6 run load/prod.js -e PROFILE=load        # heavier ramp (manual/weekly)
//   k6 run load/prod.js -e PROD_URL=https://<preview>.pages.dev
const BASE = __ENV.PROD_URL || 'https://tgpc.pages.dev';
const PROFILE = __ENV.PROFILE || 'smoke';

const PROFILES = {
	smoke: {
		scenarios: {
			browse: { executor: 'constant-vus', vus: 3, duration: '30s', exec: 'browse' },
			api: { executor: 'constant-vus', vus: 2, duration: '30s', exec: 'api' }
		}
	},
	load: {
		scenarios: {
			browse: {
				executor: 'ramping-vus',
				startVUs: 2,
				stages: [
					{ duration: '1m', target: 10 },
					{ duration: '2m', target: 20 },
					{ duration: '30s', target: 0 }
				],
				exec: 'browse'
			},
			api: {
				executor: 'ramping-vus',
				startVUs: 1,
				stages: [
					{ duration: '1m', target: 5 },
					{ duration: '2m', target: 8 },
					{ duration: '30s', target: 0 }
				],
				exec: 'api'
			}
		}
	}
};

export const options = {
	scenarios: (PROFILES[PROFILE] || PROFILES.smoke).scenarios,
	thresholds: {
		http_req_failed: ['rate<0.01'],
		http_req_duration: ['p(95)<3000']
	}
};

function page(path, marker) {
	const res = http.get(`${BASE}${path}`, { tags: { kind: 'page' } });
	check(res, {
		[`${path} 200`]: (r) => r.status === 200,
		[`${path} html`]: (r) => (r.body || '').includes(marker)
	});
}

function json(path, key) {
	const res = http.get(`${BASE}${path}`, { tags: { kind: 'api' } });
	check(res, {
		[`${path} 200`]: (r) => r.status === 200,
		[`${path} json`]: (r) => {
			try {
				const b = JSON.parse(r.body || '');
				return key in b || Array.isArray(b);
			} catch {
				return false;
			}
		}
	});
}

export function browse() {
	const r = Math.random();
	if (r < 0.5) page('/', '<title>TGPC RPh Index</title>');
	else if (r < 0.75) page('/notice', '<title>Notices');
	else page('/dispatch', '<title>Dispatch List');
	sleep(1 + Math.random() * 2);
}

export function api() {
	const r = Math.random();
	if (r < 0.4) json('/api/notice?limit=5', 'title');
	else if (r < 0.7) json('/api/dispatch?limit=1', 'name');
	else json('/api/health', 'status');
	sleep(1 + Math.random() * 2);
}
