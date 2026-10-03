import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';
import { appHarness, tick } from './app-operation-test-harness.mjs';

for (const method of ['createMeasurement', 'updateMeasurement']) {
  test(`${method}: committed measurement survives refresh failure and recovery only reads`, async () => {
    const h = appHarness();
    const detail = { id: 7, biological_measurements: [], latest_measurement: null };
    h.state.data.boxes = [detail];
    h.state.data.boxDetails[7] = detail;
    const measurement = { id: 8, measured_on: '2026-09-16', polyp_count: 0, ephyrae_count: 0, salinity_psu: '32.15' };
    const pending = method === 'createMeasurement' ? h.context[method](7, {}) : h.context[method](7, 8, {});
    h.requests[0].resolve(measurement);
    await tick();
    assert.equal(h.state.data.boxDetails[7].biological_measurements[0], measurement);
    h.requests[1].reject(new Error('refresh offline'));
    assert.equal(await pending, measurement);
    assert.ok(h.state.recovery);
    const recovery = h.state.recovery();
    assert.equal(h.requests[2].method, 'apiGet');
    h.requests[2].resolve({ ...detail, biological_measurements: [measurement] });
    await recovery;
    assert.equal(h.state.recovery, null);
    assert.equal(h.requests.filter(request => request.method !== 'apiGet').length, 1);
  });
}
for (const method of ['createMeasurement', 'updateMeasurement']) {
  test(`${method}: a failed conflict refresh preserves the original mutation error`, async () => {
    const h = appHarness();
    const original = { status: 409, data: { code: 'measurement_week_conflict' } };
    const pending = method === 'createMeasurement' ? h.context[method](7, {}) : h.context[method](7, 8, {});
    const rejected = assert.rejects(pending, candidate => candidate === original);
    h.requests[0].reject(original);
    await tick();
    h.requests[1].reject(new Error('refresh offline'));
    await rejected;
    assert.equal(h.state.recovery, null, 'a rejected mutation is not presented as saved');
  });
}
test('refresh-only recovery cannot issue a request after its organization is replaced', async () => {
  const h = appHarness();
  const pending = h.context.createSubculture(7, {});
  h.requests[0].resolve({ children: [{ id: 9 }] });
  await tick();
  h.requests[1].reject(new Error('refresh offline'));
  await pending;
  h.switchOrganization();
  await assert.rejects(h.state.recovery(), h.context.ApiResourceCancelledError);
  assert.equal(h.requests.length, 2);
});
test('movement applies authoritative location before a failed zones refresh', async () => {
  const h = appHarness();
  const detail = { id: 7, thermal_zone: { id: 12 } };
  const pending = h.context.moveBox(7, {});
  h.requests[0].resolve(detail);
  await tick();
  assert.equal(h.state.data.boxDetails[7], detail);
  h.requests[1].reject(new Error('zones offline'));
  assert.equal(await pending, detail);
  const recovery = h.state.recovery();
  h.requests[2].resolve({ results: [{ id: 12 }], next: null });
  await recovery;
  assert.equal(h.requests.filter(request => request.method === 'apiPost').length, 1);
});
for (const method of ['createMeasurement', 'moveBox', 'createSubculture']) {
  test(`${method}: organization change during refresh does not recover or report old success`, async () => {
    const h = appHarness();
    const detail = { id: 7, biological_measurements: [] };
    h.state.data.boxDetails[7] = detail;
    const pending = h.context[method](7, {});
    const rejected = assert.rejects(pending, h.context.ApiResourceCancelledError);
    h.requests[0].resolve(method === 'createSubculture' ? { children: [{ id: 9 }] } : { id: 8, measured_on: '2026-09-16' });
    await tick();
    h.switchOrganization();
    const replacement = { boxes: [], boxDetails: {}, zones: [] };
    h.state.data = replacement;
    h.requests[1].reject(new Error('refresh failed'));
    await rejected;
    assert.equal(h.state.data, replacement);
    assert.equal(h.state.recovery, null);
  });
}

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
