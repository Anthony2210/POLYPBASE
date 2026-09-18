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

const { filterBoxes } = loadTypeScript('../src/utils/boxLookup.ts');

function box(id, {
  globalCode,
  localCode = '',
  boxNumber,
  species = 'Aurelia aurita',
  strain = 'AA',
  zone = 'Nurserie',
}) {
  return {
    id,
    global_code: globalCode,
    local_code: localCode,
    box_number: boxNumber,
    species: { scientific_name: species },
    strain: { code: strain },
    thermal_zone: zone ? { name: zone } : null,
  };
}

const boxes = [
  box(1, { globalCode: 'AQP-AA-012', localCode: 'LOCAL-12', boxNumber: '12' }),
  box(2, { globalCode: 'AQP-AA-012-BIS', boxNumber: '112', species: 'Chrysaora quinquecirrha', strain: 'CQ' }),
  box(3, { globalCode: 'LAB-777', boxNumber: '77', species: 'Cassiopea andromeda', strain: 'CA', zone: 'Zone froide' }),
  box(4, { globalCode: 'PREFIX-42', boxNumber: '42', species: 'Aurelia coerulea', strain: 'BLUE', zone: 'Réserve' }),
];

test('matching is case-insensitive and trims surrounding whitespace', () => {
  assert.deepEqual(filterBoxes(boxes, '  aqp-AA-012  ').map(({ id }) => id), [1, 2]);
});

test('an exact global code ranks before partial matches', () => {
  const candidates = [boxes[1], boxes[0]];
  assert.deepEqual(filterBoxes(candidates, 'AQP-AA-012').map(({ id }) => id), [1, 2]);
});

test('an exact local box number ranks before partial matches', () => {
  assert.deepEqual(filterBoxes(boxes, '12').map(({ id }) => id), [1, 2]);
});

test('prefix matches rank before generic substring matches', () => {
  const candidates = [
    box(10, { globalCode: 'LAB-PREFIX-42', boxNumber: '8' }),
    box(11, { globalCode: 'PREFIX-99', boxNumber: '9' }),
  ];
  assert.deepEqual(filterBoxes(candidates, 'prefix').map(({ id }) => id), [11, 10]);
});

test('species, strain, and zone fields remain searchable', () => {
  assert.deepEqual(filterBoxes(boxes, 'cassiopea').map(({ id }) => id), [3]);
  assert.deepEqual(filterBoxes(boxes, 'blue').map(({ id }) => id), [4]);
  assert.deepEqual(filterBoxes(boxes, 'froide').map(({ id }) => id), [3]);
});
