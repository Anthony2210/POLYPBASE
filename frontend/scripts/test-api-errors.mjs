import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

function loadTypeScript(relativePath, globals = {}) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, ...globals });
  return exports;
}

for (const [status, body, expected] of [
  [400, { thermal_zone_id: ['The thermal zone must belong to the box organization.'] },
    'The thermal zone must belong to the box organization.'],
  [400, ['Only a box pending review can be qualified.'], 'Only a box pending review can be qualified.'],
  [403, { detail: 'This user cannot qualify this box.' }, 'This user cannot qualify this box.'],
  [401, { detail: 'Authentication credentials were not provided.' }, 'Authentication credentials were not provided.'],
]) {
  test(`preserves the backend reason for HTTP ${status}: ${expected}`, async () => {
    const client = loadTypeScript('../src/api/client.ts', {
      Headers,
      window: { localStorage: { getItem: () => null } },
      document: { cookie: '' },
      fetch: async () => new Response(JSON.stringify(body), {
        status, headers: { 'Content-Type': 'application/json' },
      }),
    });
    const { getErrorMessage } = loadTypeScript('../src/utils/errors.ts', {
      require: () => client,
    });
    await assert.rejects(client.apiPost('/api/boxes/1/qualify/', {}), error => {
      assert.equal(getErrorMessage(error), expected);
      assert.equal(error.status, status);
      return true;
    });
  });
}

test('inventory uses API summary counts including zero without extra requests', async () => {
  const { getBoxInventoryCounters } = loadTypeScript('../src/api/boxInventory.ts', {
    require: () => ({ apiGet: () => { throw new Error('Unexpected count request'); } }),
  });
  const result = await getBoxInventoryCounters({summary: {pending_review_count: 0, active_without_location_count: 12}});
  assert.equal(result.pending_review_count, 0);
  assert.equal(result.active_without_location_count, 12);
});

test('inventory without summary uses server totals, independent of the displayed page', async () => {
  const paths = [];
  const { getBoxInventoryCounters } = loadTypeScript('../src/api/boxInventory.ts', {
    require: () => ({ apiGet: async path => {
      paths.push(path);
      return {count: path.includes('pending_review') ? 87 : 0, results: []};
    } }),
  });
  const result = await getBoxInventoryCounters({count: 1, results: [{}]});
  assert.equal(result.pending_review_count, 87);
  assert.equal(result.active_without_location_count, 0);
  assert.deepEqual(paths, [
    '/api/admin/box-inventory/?limit=1&offset=0&status=pending_review',
    '/api/admin/box-inventory/?limit=1&offset=0&status=active&location=none',
  ]);
});

test('inventory fetches only the missing summary counter', async () => {
  const paths = [];
  const { getBoxInventoryCounters } = loadTypeScript('../src/api/boxInventory.ts', {
    require: () => ({ apiGet: async path => { paths.push(path); return {count: 5}; } }),
  });
  const result = await getBoxInventoryCounters({summary:{pending_review_count:0}});
  assert.equal(result.pending_review_count,0);
  assert.equal(result.active_without_location_count,5);
  assert.deepEqual(paths,['/api/admin/box-inventory/?limit=1&offset=0&status=active&location=none']);
});

test('inventory count failures propagate instead of inventing a zero', async () => {
  const error = new Error('Counter request failed');
  const { getBoxInventoryCounters } = loadTypeScript('../src/api/boxInventory.ts', {
    require: () => ({ apiGet: async () => { throw error; } }),
  });
  await assert.rejects(getBoxInventoryCounters({}), candidate => candidate === error);
});
