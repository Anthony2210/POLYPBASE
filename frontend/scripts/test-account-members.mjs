import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/accountMembers.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports });

test('active members expose only deactivation', () => {
  assert.equal(exports.getMemberRowAction({ is_active: true, is_self: false }), 'deactivate');
});

test('inactive members expose only reactivation', () => {
  assert.equal(exports.getMemberRowAction({ is_active: false, is_self: false }), 'reactivate');
});

test('self-protection leaves no access-toggle action available', () => {
  assert.equal(exports.getMemberRowAction({ is_active: true, is_self: true }), null);
  assert.equal(exports.getMemberRowAction({ is_active: false, is_self: true }), null);
});
