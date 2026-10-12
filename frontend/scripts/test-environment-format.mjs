import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/environmentFormat.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports });

const cases = [
  { input: null, temperature: '-', salinity: '-' },
  { input: undefined, temperature: '-', salinity: '-' },
  { input: '', temperature: '-', salinity: '-' },
  { input: '   ', temperature: '-', salinity: '-' },
  { input: 'invalid', temperature: '-', salinity: '-' },
  { input: NaN, temperature: '-', salinity: '-' },
  { input: 'NaN', temperature: '-', salinity: '-' },
  { input: 0, temperature: '0.0°C', salinity: '0.0' },
  { input: '0', temperature: '0.0°C', salinity: '0.0' },
  { input: '0.00', temperature: '0.0°C', salinity: '0.0' },
  { input: -0, temperature: '0.0°C', salinity: '0.0' },
  { input: 21.25, temperature: '21.3°C', salinity: '21.3' },
  { input: '21.25', temperature: '21.3°C', salinity: '21.3' },
  { input: -2.25, temperature: '-2.3°C', salinity: '-2.3' },
  { input: ' 31.50 ', temperature: '31.5°C', salinity: '31.5' },
  { input: '1e2', temperature: '100.0°C', salinity: '100.0' },
  { input: '12.5suffix', temperature: '12.5°C', salinity: '12.5' },
  { input: '12,5', temperature: '12.0°C', salinity: '12.0' },
  { input: Infinity, temperature: '-', salinity: 'Infinity' },
  { input: -Infinity, temperature: '-', salinity: '-Infinity' },
  { input: 'Infinity', temperature: '-', salinity: 'Infinity' },
  { input: '-Infinity', temperature: '-', salinity: '-Infinity' },
  { input: '1e309', temperature: '-', salinity: 'Infinity' },
];

for (const { input, temperature, salinity } of cases) {
  test(`environment display preserves formatting for ${typeof input} ${String(input)}`, () => {
    assert.equal(exports.formatTemperature(input), temperature);
    assert.equal(exports.formatTemperatureValue(input), temperature);
    assert.equal(exports.formatSalinity(input), salinity);
  });
}

const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const appAst = ts.createSourceFile('App.tsx', appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);

test('App imports its display formatters from the utility without retaining local implementations', () => {
  const importNode = appAst.statements.find(node => ts.isImportDeclaration(node)
    && node.moduleSpecifier.text === './utils/environmentFormat');
  assert.ok(importNode);
  const bindings = importNode.importClause.namedBindings.elements;
  assert.deepEqual(bindings.map(binding => binding.name.text).sort(), ['formatSalinity', 'formatTemperature']);
  assert.ok(bindings.every(binding => !binding.propertyName));
  assert.ok(appAst.statements.every(node => !ts.isFunctionDeclaration(node)
    || !['formatTemperature', 'formatTemperatureValue', 'formatSalinity'].includes(node.name?.text)));
});
