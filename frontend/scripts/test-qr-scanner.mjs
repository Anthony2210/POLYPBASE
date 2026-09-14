import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/qrScanner.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports, decodeURIComponent });

const boxes = [
  { id: 17, global_code: 'AUR-17', local_code: 'LAB-0042' },
  { id: 29, global_code: 'PEL 29', local_code: 'LOCAL-29' },
];

test('resolves stable numeric scan routes', () => {
  assert.equal(exports.getBoxIdFromQrValue('https://polypbase.test/bac/17/', boxes), 17);
});

test('resolves encoded and case-insensitive box-code routes', () => {
  assert.equal(exports.getBoxIdFromQrValue('https://polypbase.test/boxes/PEL%2029/', boxes), 29);
  assert.equal(exports.getBoxIdFromQrValue('/boxes/aur-17', boxes), 17);
});

test('resolves direct global and local codes', () => {
  assert.equal(exports.getBoxIdFromQrValue(' aur-17 ', boxes), 17);
  assert.equal(exports.getBoxIdFromQrValue('lab-0042', boxes), 17);
});

test('ignores unsupported QR values', () => {
  assert.equal(exports.getBoxIdFromQrValue('https://example.test/unrelated', boxes), null);
});
