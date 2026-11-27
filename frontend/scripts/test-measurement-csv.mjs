import assert from 'node:assert/strict';
import test from 'node:test';
import { loadI18n } from './i18n-runtime-test-helper.mjs';

const { fr, en } = loadI18n().translations;

test('FR/EN parity covers import, retention, receipt and canonical/planner issues', () => {
  for (const key of Object.keys(fr).filter((key) => key.startsWith('csv'))) {
    assert.ok(fr[key]);
    assert.ok(en[key]);
    assert.notEqual(fr[key], key);
    assert.notEqual(en[key], key);
  }
  assert.match(fr.csvRetentionHelp, /privée et permanente/);
  assert.match(en.csvRetentionHelp, /even if no measurements are created/);
  assert.match(en.csvConfirmMapping, /does not authorize any import/);
});
