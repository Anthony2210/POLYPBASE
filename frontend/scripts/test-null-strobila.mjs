import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

function loadModuleWithRequire(relativePath, requireMap) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  const require = (specifier) => {
    if (specifier in requireMap) return requireMap[specifier];
    throw new Error(`Unexpected require: ${specifier}`);
  };
  vm.runInNewContext(outputText, { exports, require, URLSearchParams });
  return exports;
}

const dateFormat = loadModuleWithRequire('../src/utils/dateFormat.ts', {});
const audit = loadModuleWithRequire('../src/utils/auditPresentation.ts', { './dateFormat': dateFormat, './userIdentity': loadModuleWithRequire('../src/utils/userIdentity.ts', {}) });
const t = (key) => key;

test('the API type admits null strobilae, distinct from a measured zero', () => {
  const types = readFileSync(new URL('../src/types.ts', import.meta.url), 'utf8');
  assert.match(types, /strobila_count: number \| null;/);
});

test('audit values show an unmeasured strobila count differently from zero', () => {
  assert.equal(audit.formatAuditMetadataValue(null, t), '-');
  assert.equal(audit.formatAuditMetadataValue(0, t), '0');
  assert.notEqual(audit.formatAuditMetadataValue(null, t), audit.formatAuditMetadataValue(0, t));
  assert.equal(audit.formatAuditChange({ avant: null, apres: 0 }, t), '- -> 0');
});

function hasStrobilaReference(path, source) {
  // Exempt only the CSV translation entry, never numerical readers in the same file.
  if (path === 'api/measurementImport.ts') {
    source = source.replace(/\bstrobila_count:\s*'csvFieldStrobila'(?=\s*[,}])/, '');
  }
  return /strobila_count|strobiles/.test(source);
}

test('the CSV field label is metadata but numerical strobila readers remain detectable', () => {
  const path = 'api/measurementImport.ts';
  const source = readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
  assert.equal(hasStrobilaReference(path, source), false);
  for (const expression of [
    'Number(row.strobila_count)',
    '+row.strobila_count',
    'row.strobila_count ?? 0',
    'row.strobila_count || 0',
    'Number(row.strobiles)',
  ]) {
    assert.equal(hasStrobilaReference(path, `${source}\nconst count = ${expression};`), true, expression);
  }
  assert.equal(hasStrobilaReference('other.ts', "strobila_count: 'csvFieldStrobila',"), true);
  assert.equal(hasStrobilaReference(path, 'strobila_count: Number(null),'), true);
});

test('no unreviewed strobila references exist outside the typed API and audit presentation', () => {
  const root = new URL('../src/', import.meta.url);
  const offenders = [];
  const walk = (directory) => {
    for (const name of readdirSync(directory)) {
      const path = new URL(`${name}${statSync(new URL(`${directory.href}${name}`)).isDirectory() ? '/' : ''}`, directory);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.(ts|tsx)$/.test(name) && hasStrobilaReference(path.pathname.split('/src/')[1], readFileSync(path, 'utf8'))) {
        offenders.push(path.pathname.split('/src/')[1]);
      }
    }
  };
  walk(root);
  assert.deepEqual(offenders.sort(), ['types.ts', 'utils/auditPresentation.ts']);
});
