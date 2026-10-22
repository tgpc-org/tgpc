
// Last-known dispatch PDFs, used ONLY when the R2 listing fails or the
// binding is missing (local dev without --r2). Regenerated from the live
// bucket (tgpc, prefix dispatch/) on 2026-10-22 - 220 objects. Regenerate
// with the R2 S3 credentials rather than editing by hand: a stale entry
// here is a file the page offers and cannot serve (audit F13).
const FALLBACK_FILES = [
  'DL01062021.pdf', 'DL01062024.pdf', 'DL01062026.pdf', 'DL01072019.pdf',
  'DL01112019.pdf', 'DL01112023.pdf', 'DL01122021.pdf', 'DL02022018.pdf',
  'DL02022019.pdf', 'DL02032019.pdf', 'DL02042024.pdf', 'DL02062020.pdf',
  'DL02082019.pdf', 'DL02092025.pdf', 'DL02122023.pdf', 'DL03012025.pdf',
  'DL03032025.pdf', 'DL03042021.pdf', 'DL03052023.pdf', 'DL03062020.pdf',
  'DL03062020C.pdf', 'DL03062020D.pdf', 'DL03062024.pdf', 'DL03062025.pdf',
  'DL03082020.pdf', 'DL03082021.pdf', 'DL03082023.pdf', 'DL03092021.pdf',
  'DL03112021.pdf', 'DL04042026.pdf', 'DL04052024.pdf', 'DL04052026.pdf',
  'DL04072020.pdf', 'DL04072025.pdf', 'DL04082025.pdf', 'DL04112022.pdf',
  'DL04112023.pdf', 'DL05022022.pdf', 'DL05032024.pdf', 'DL05052025.pdf',
  'DL05102019.pdf', 'DL05112024.pdf', 'DL06022021.pdf', 'DL06042023.pdf',
  'DL07012026.pdf', 'DL07062021.pdf', 'DL07062023.pdf', 'DL07092019.pdf',
  'DL07092023.pdf', 'DL07102020.pdf', 'DL08022024.pdf', 'DL08032022.pdf',
  'DL08072026.pdf', 'DL08082023.pdf', 'DL08092020.pdf', 'DL09022018.pdf',
  'DL09022023.pdf', 'DL09022026.pdf', 'DL09062020.pdf', 'DL09062026.pdf',
  'DL09102024.pdf', 'DL09122020.pdf', 'DL10012023.pdf', 'DL10032021.pdf',
  'DL10032025.pdf', 'DL10032026.pdf', 'DL10042021.pdf', 'DL10052019.pdf',
  'DL10062022.pdf', 'DL10092022.pdf', 'DL11022025.pdf', 'DL11082021.pdf',
  'DL11092023.pdf', 'DL11102021.pdf', 'DL11102021AD.pdf', 'DL11102024.pdf',
  'DL11122021.pdf', 'DL12052023.pdf', 'DL12092023.pdf', 'DL12102022.pdf',
  'DL12112020.pdf', 'DL12112024.pdf', 'DL12122025.pdf', 'DL13012024.pdf',
  'DL13022021.pdf', 'DL13052022.pdf', 'DL13052025.pdf', 'DL13072020.pdf',
  'DL13082024.pdf', 'DL13092024.pdf', 'DL13122022.pdf', 'DL14032020.pdf',
  'DL14032023.pdf', 'DL14072026.pdf', 'DL14112025.pdf', 'DL15022020.pdf',
  'DL15042025.pdf', 'DL15062020.pdf', 'DL15072024.pdf', 'DL15072025.pdf',
  'DL15102024.pdf', 'DL15112023.pdf', 'DL16022019.pdf', 'DL16022026.pdf',
  'DL16032019.pdf', 'DL16042022.pdf', 'DL16072022.pdf', 'DL16092023.pdf',
  'DL16092025.pdf', 'DL16112023.pdf', 'DL16122019.pdf', 'DL16122023.pdf',
  'DL17012025.pdf', 'DL17032021.pdf', 'DL17032025.pdf', 'DL17062025.pdf',
  'DL18042019.pdf', 'DL18052026.pdf', 'DL18062021.pdf', 'DL18072019.pdf',
  'DL18082023.pdf', 'DL18122025.pdf', 'DL19022021.pdf', 'DL19062026.pdf',
  'DL19082025.pdf', 'DL19122020.pdf', 'DL19122024.pdf', 'DL20012020.pdf',
  'DL20022023.pdf', 'DL20032021.pdf', 'DL20032024.pdf', 'DL20042026.pdf',
  'DL20052022.pdf', 'DL20052024.pdf', 'DL20062023.pdf', 'DL20102021.pdf',
  'DL20102022.pdf', 'DL20122021.pdf', 'DL21012023.pdf', 'DL21022018.pdf',
  'DL21042025.pdf', 'DL21072020.pdf', 'DL21092019.pdf', 'DL21092022.pdf',
  'DL21102019.pdf', 'DL21102022.pdf', 'DL21112024.pdf', 'DL21122022.pdf',
  'DL22012021.pdf', 'DL22012025.pdf', 'DL22012026.pdf', 'DL22022024.pdf',
  'DL22022025.pdf', 'DL22052025.pdf', 'DL22072021.pdf', 'DL22082019.pdf',
  'DL22112021.pdf', 'DL22112023.pdf', 'DL23022026.pdf', 'DL23052023.pdf',
  'DL23082022.pdf', 'DL23082023.pdf', 'DL23092020.pdf', 'DL23112019.pdf',
  'DL23112022.pdf', 'DL24042024.pdf', 'DL24062025.pdf', 'DL24062026.pdf',
  'DL24082023.pdf', 'DL25022023.pdf', 'DL25032023.pdf', 'DL25032025.pdf',
  'DL25072022.pdf', 'DL25082021.pdf', 'DL26042023.pdf', 'DL26092023.pdf',
  'DL26092024.pdf', 'DL27022021.pdf', 'DL27022026.pdf', 'DL27042026.pdf',
  'DL27052026.pdf', 'DL27062022.pdf', 'DL27092025.pdf', 'DL27112025.pdf',
  'DL28042021.pdf', 'DL28042025.pdf', 'DL28052020.pdf', 'DL28052022.pdf',
  'DL28062024.pdf', 'DL28102020.pdf', 'DL28102023.pdf', 'DL28112024.pdf',
  'DL29022020.pdf', 'DL29032025.pdf', 'DL29052020.pdf', 'DL29052021.pdf',
  'DL29072025.pdf', 'DL29092025.pdf', 'DL29102022.pdf', 'DL29102024.pdf',
  'DL29112022.pdf', 'DL30012026.pdf', 'DL30032019.pdf', 'DL30042019.pdf',
  'DL30052019.pdf', 'DL30062020.pdf', 'DL30092021.pdf', 'DL30092022.pdf',
  'DL30092024.pdf', 'DL30092025.pdf', 'DL30102021.pdf', 'DL30112019.pdf',
  'DL31012023.pdf', 'DL31012025.pdf', 'DL31052019.pdf', 'DL31072024.pdf',
  'DL31082020.pdf', 'DL31082024.pdf', 'DL31122021.pdf', 'DL31122025.pdf'
];

import { dispatchBucket } from '#lib/server/appEnv.js';
import { DISPATCH_PREFIX, dispatchNameFromKey } from '#lib/dispatchNames.js';

const CACHE = { 'Cache-Control': 'public, max-age=60' };
const PAGE_SIZE = 1000;
/** 20k entries: far above the 221 real files, but still a bounded loop. */
const MAX_PAGES = 20;

type DispatchFile = { name: string; size?: number };

/**
 * Every dispatch PDF the bucket holds, oldest listing API page included.
 * Keys that are not dispatch PDFs are dropped here so the listing can never
 * advertise a file the download route would reject.
 */
async function listFromBucket(bucket: App.DispatchBucket): Promise<DispatchFile[]> {
  const files: DispatchFile[] = [];
  let cursor: string | undefined;

  for (let page = 0; page < MAX_PAGES; page++) {
    const listing = await bucket.list({ prefix: DISPATCH_PREFIX, limit: PAGE_SIZE, cursor });
    for (const obj of listing.objects) {
      const name = dispatchNameFromKey(obj.key);
      if (!name) continue;
      files.push(typeof obj.size === 'number' ? { name, size: obj.size } : { name });
    }
    if (!listing.truncated || !listing.cursor) break;
    cursor = listing.cursor;
  }

  return files.sort((a, b) => a.name.localeCompare(b.name));
}

export async function GET({ platform }) {
  const bucket = dispatchBucket(platform);

  if (bucket) {
    try {
      // Once the binding is reachable R2 is the source of truth: return what
      // it actually holds, even zero files. Reporting the hardcoded names in
      // that case is what made the F13 outage invisible.
      return Response.json(await listFromBucket(bucket), { headers: CACHE });
    } catch {
      // Listing failed: fall through to the flagged last-known list.
    }
  }

  // Binding missing (local dev without --r2) or the listing call failed:
  // return the last-known file list instead of 503 so the page stays usable,
  // but flag every entry stale and omit sizes, because those files cannot be
  // served (CODE_REVIEW.md M3).
  const files = FALLBACK_FILES.map((n) => ({ name: n, stale: true }));
  return Response.json(files, { headers: CACHE });
}
