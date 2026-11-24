import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/ZonesView.tsx', import.meta.url), 'utf8');
const parsed = ts.createSourceFile('ZonesView.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
class ApiResourceCancelledError extends Error {}

// Compile the actual nested handlers with only their surrounding state/callbacks stubbed.
function loadHandler(name, context) {
  let handler;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) handler = node.getText(parsed);
    ts.forEachChild(node, visit);
  }
  visit(parsed);
  assert.ok(handler, `Missing ${name}`);
  const { outputText } = ts.transpileModule(`${handler}\nexports.handler = ${name};`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, ApiResourceCancelledError, ...context });
  return exports.handler;
}

for (const outcome of ['cancelled', 'failure', 'success']) {
    test(`delayed temperature ${outcome} preserves editor state`, async () => {
      let resolve, reject;
      const pending = new Promise((res, rej) => { resolve = res; reject = rej; });
      const calls = [];
      const state = { saving: false, error: null, editing: true, value: '0' };
      const context = {

        zone: { id: 7 }, isSavingTemperature: false, temperatureDate: '2026-10-03', manualTemperature: '0',
        onRecordManualTemperature: () => pending,
        getErrorMessage: (error) => { calls.push('format-error'); return error.message; },

        setIsSavingTemperature: (value) => { state.saving = value; },

        setTemperatureError: (value) => { state.error = value; },
        setManualTemperature: (value) => { state.value = value; },
        setIsEditingTemperature: (value) => { state.editing = value; },
      };
      const completion = loadHandler('handleManualTemperatureSubmit', context)({ preventDefault() {} });
      assert.equal(state.saving, true);
      assert.deepEqual(calls, []);
      if (outcome === 'cancelled') reject(new ApiResourceCancelledError('stale organization'));
      else if (outcome === 'failure') reject(new Error('ordinary failure'));
      else resolve();
      await completion;
      assert.equal(state.saving, false);
      assert.equal(state.error, outcome === 'failure' ? 'ordinary failure' : null);
      assert.deepEqual(calls, outcome === 'failure' ? ['format-error'] : []);
      assert.equal(state.editing, outcome !== 'success');
      assert.equal(state.value, outcome === 'success' ? '' : '0');
    });
}

test('organization and zone route remount keys isolate pending zone editor state', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const keyDefinition = app.match(/const workspacePageKey = ([^;]+);/)?.[1];
  assert.ok(keyDefinition);
  assert.match(keyDefinition, /activeOrganizationId/);
  assert.match(keyDefinition, /route\.zoneId/);
  assert.match(app, /className="workspace-page" key=\{workspacePageKey\}/);
  assert.match(source, /<ZoneFunctionalSections\s+key=\{zone\.id\}/);
});
