import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function moduleAt(path, modules = {}) {
  const exports = {};
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  vm.runInNewContext(outputText, { exports, FormData, URL, window: { location: { origin: 'https://polypbase.test' } }, require: name => {
    assert.ok(Object.hasOwn(modules, name), name);
    return modules[name];
  } });
  return exports;
}
const fr = moduleAt('../src/i18n/fr.ts').fr;
const en = moduleAt('../src/i18n/en.ts').en;

test('FR/EN parity covers import, retention, receipt and canonical/planner issues', () => {
  for (const key of Object.keys(fr).filter(key => key.startsWith('csv'))) {
    assert.ok(fr[key]); assert.ok(en[key]); assert.notEqual(fr[key], key); assert.notEqual(en[key], key);
  }
  assert.match(fr.csvRetentionHelp, /privée et permanente/);
  assert.match(en.csvRetentionHelp, /even if no measurements are created/);
  assert.match(en.csvConfirmMapping, /does not authorize any import/);
});
