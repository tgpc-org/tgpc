/**
 * Tests for the admin contact lookup helpers.
 * Run with:
 *
 *   npm run test:unit
 */

import assert from 'node:assert/strict';
import { describe, it } from 'node:test';

import {
  isContactEmpty,
  normalizeReg,
  shapeBase,
  shapeContact
} from './contacts.ts';

describe('normalizeReg', () => {
  it('accepts valid prefixes case-insensitively with whitespace', () => {
    assert.equal(normalizeReg(' ts000097 '), 'TS000097');
    assert.equal(normalizeReg('tgdr006066'), 'TGDR006066');
    assert.equal(normalizeReg('TSDR001793'), 'TSDR001793');
  });

  it('rejects non-registration input', () => {
    assert.equal(normalizeReg(''), null);
    assert.equal(normalizeReg('TS'), null);
    assert.equal(normalizeReg('XX000123'), null);
    assert.equal(normalizeReg('TS12A34'), null);
    assert.equal(normalizeReg('TS 0001'), null);
    assert.equal(normalizeReg(null), null);
    assert.equal(normalizeReg(123), null);
    assert.equal(normalizeReg("TS0001' OR '1'='1"), null);
  });
});

describe('shapeBase', () => {
  it('projects only the allowlisted base columns', () => {
    const out = shapeBase({
      registration_number: 'TS1',
      name: 'N',
      extra_evil: 'x',
      mobile_no: 'should-not-pass'
    });
    assert.equal(out.registration_number, 'TS1');
    assert.equal(out.name, 'N');
    assert.ok(!('extra_evil' in out));
    assert.ok(!('mobile_no' in out));
    assert.equal(out.father_name, null);
  });
});

describe('shapeContact', () => {
  it('projects only the nine DG contact columns', () => {
    const out = shapeContact({
      dob: '02-01-1991',
      mobile_no: '9999999999',
      registration_number: 'TS1',
      serial_number: 1
    });
    assert.equal(out.dob, '02-01-1991');
    assert.equal(out.mobile_no, '9999999999');
    assert.ok(!('registration_number' in out));
    assert.ok(!('serial_number' in out));
    assert.equal(out.email_id, null);
  });

  it('detects empty contacts from missing or blank DG rows', () => {
    assert.equal(isContactEmpty(shapeContact({})), true);
    assert.equal(isContactEmpty(shapeContact({ dob: '', mobile_no: '' })), true);
    assert.equal(
      isContactEmpty(shapeContact({ dob: '02-01-1991', mobile_no: '' })),
      false
    );
  });
});
