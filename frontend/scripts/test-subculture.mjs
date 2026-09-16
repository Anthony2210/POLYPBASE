import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/subculture.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports });

function parent(globalCode) {
  return {
    global_code: globalCode,
    strain: { code: '1-ATL' },
  };
}

test('uses the strain prefix for a child of a legacy-coded parent', () => {
  const suggestion = exports.suggestChildIdentity(
    parent('AAU-1.001-ATL'),
    [{ global_code: 'AAU-1.001-ATL' }],
    [],
  );

  assert.deepEqual(
    { ...suggestion },
    { globalCode: '1-ATL.002', boxNumber: '002' },
  );
});

test('keeps canonical generation and advances past existing child identities', () => {
  const suggestion = exports.suggestChildIdentity(
    parent('1-ATL.001'),
    [{ global_code: '1-ATL.001' }, { global_code: '1-ATL.004' }],
    [{ global_code: '1-ATL.005' }],
  );

  assert.deepEqual(
    { ...suggestion },
    { globalCode: '1-ATL.006', boxNumber: '006' },
  );
});
