import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

function loadTypeScript(relativePath) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports });
  return exports;
}

const { PHONE_NAVIGATION_ITEMS } = loadTypeScript('../src/utils/phoneNavigation.ts');
const { filterBoxes } = loadTypeScript('../src/utils/boxLookup.ts');
const { getBoxIdFromQrValue } = loadTypeScript('../src/utils/qrScanner.ts');

const boxes = [
  {
    id: 17,
    global_code: '1-ATL.017',
    local_code: 'ATL-17',
    box_number: '17',
    species: { scientific_name: 'Aurelia aurita' },
    strain: { code: 'ATL' },
    thermal_zone: { name: 'Nursery' },
  },
  {
    id: 23,
    global_code: '1-PAC.023',
    local_code: 'PAC-23',
    box_number: '23',
    species: { scientific_name: 'Chrysaora pacifica' },
    strain: { code: 'PAC' },
    thermal_zone: null,
  },
];

test('phone navigation keeps four destinations around a non-route QR action', () => {
  assert.equal(
    PHONE_NAVIGATION_ITEMS.map((item) => item.kind === 'destination' ? item.tab : item.action).join(','),
    'overview,zones,qr,labels,profile',
  );
  const qrItem = PHONE_NAVIGATION_ITEMS[2];
  assert.equal(qrItem.kind, 'action');
  assert.equal('tab' in qrItem, false);
  assert.equal('path' in qrItem, false);
});

test('manual lookup retains the existing box fields and ordering', () => {
  assert.deepEqual(filterBoxes(boxes, 'aurelia').map((box) => box.id), [17]);
  assert.deepEqual(filterBoxes(boxes, 'PAC-23').map((box) => box.id), [23]);
  assert.deepEqual(filterBoxes(boxes, 'nursery').map((box) => box.id), [17]);
  assert.deepEqual(filterBoxes(boxes, '').map((box) => box.id), [17, 23]);
});

test('QR lookup accepts only the currently supported box values', () => {
  assert.equal(getBoxIdFromQrValue('https://example.test/bac/17/', boxes), 17);
  assert.equal(getBoxIdFromQrValue('/boxes/1-ATL.017', boxes), 17);
  assert.equal(getBoxIdFromQrValue('ATL-17', boxes), 17);
  assert.equal(getBoxIdFromQrValue('unknown-format:17', boxes), null);
});

test('profile no longer renders the phone-only labels shortcut', () => {
  const profileSource = readFileSync(new URL('../src/components/ProfileView.tsx', import.meta.url), 'utf8');
  assert.equal(profileSource.includes('profile-mobile-labels-link'), false);
  assert.equal(profileSource.includes('onOpenLabels'), false);
});
