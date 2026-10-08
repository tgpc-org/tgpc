import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { coveragePct, fmtInt, driftTone, parseCtlBody } from './ops.ts';

describe('ops helpers', () => {
  it('coveragePct clamps 0..100', () => {
    assert.equal(coveragePct(10, 5, 20), 75);
    assert.equal(coveragePct(0, 0, 0), 0);
    assert.equal(coveragePct(30, 0, 20), 100);
  });

  it('fmtInt handles non-finite', () => {
    assert.equal(fmtInt(null), '—');
    assert.equal(fmtInt(undefined), '—');
    assert.equal(fmtInt(1234), '1,234');
  });

  it('driftTone maps drift', () => {
    assert.equal(driftTone(0), 'ok');
    assert.equal(driftTone(3), 'bad');
    assert.equal(driftTone(null), 'unknown');
  });

  it('parseCtlBody validates halt boolean and trims note', () => {
    assert.deepEqual(parseCtlBody({ halt: true, note: '  hi  ' }), { halt: true, note: 'hi' });
    assert.throws(() => parseCtlBody({ halt: 'yes' }), /halt must be boolean/);
    assert.throws(() => parseCtlBody(null), /Bad request/);
    const long = parseCtlBody({ halt: false, note: 'x'.repeat(200) });
    assert.equal(long.note.length, 140);
  });
});
