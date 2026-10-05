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
const audit = loadModuleWithRequire('../src/utils/auditPresentation.ts', { './dateFormat': dateFormat });
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

test('no frontend reader coerces a strobila count outside the typed API and audit presentation', () => {
  const root = new URL('../src/', import.meta.url);
  const offenders = [];
  const walk = (directory) => {
    for (const name of readdirSync(directory)) {
      const path = new URL(`${name}${statSync(new URL(`${directory.href}${name}`)).isDirectory() ? '/' : ''}`, directory);
      if (statSync(path).isDirectory()) walk(path);
      else if (/\.(ts|tsx)$/.test(name) && /strobila_count|strobiles/.test(readFileSync(path, 'utf8'))) {
        offenders.push(path.pathname.split('/src/')[1]);
      }
    }
  };
  walk(root);
  assert.deepEqual(offenders.sort(), ['types.ts', 'utils/auditPresentation.ts']);
});
