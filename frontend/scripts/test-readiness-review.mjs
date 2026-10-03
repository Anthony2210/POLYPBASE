import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { appHarness, functionNode, tick } from './app-operation-test-harness.mjs';

function loadUtility(path) {
  const exports = {};
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  vm.runInNewContext(outputText, { exports });
  return exports;
}
const salinity = loadUtility('../src/utils/biologicalSalinity.ts');
const measurement = loadUtility('../src/utils/boxMeasurement.ts');

for (const value of ['32.15', '0', '0.00', '']) {
  test(`biological salinity prefill, correction and payload preserve ${JSON.stringify(value)}`, () => {
    const h = appHarness();
    Object.assign(h.context, salinity);
    for (const name of ['getCurrentThermalZone', 'getZoneSalinityValue', 'getDefaultMeasurementSalinity']) {
      h.evaluate(functionNode(name).getText());
    }
    const box = { latest_salinity_psu: value, thermal_zone: { id: 12, salinity_psu: value } };
    const prefill = h.context.getDefaultMeasurementSalinity(box, []);
    assert.equal(prefill, value === '0.00' ? '0' : value);
    const correction = measurement.getMeasurementFormValues({
      measured_on: '2026-10-03', polyp_count: 0, ephyrae_count: 0, salinity_psu: value,
    });
    assert.equal(correction.salinity, value);
    const payload = h.context.buildMeasurementPayload(correction);
    assert.equal(payload.salinity_psu, value || null);
    box.latest_salinity_psu = null;
    assert.equal(h.context.getDefaultMeasurementSalinity(box, [box.thermal_zone]), prefill);
  });
}
for (const [value, step, expected] of [
  ['32.15', 5, '37.15'], ['32.15', -5, '27.15'],
  ['0', 5, '5'], ['0.00', -5, '0'], ['', 5, '5'], ['', -5, '0'],
  ['32.155', 5, '32.155'], ['invalid', 5, 'invalid'],
]) {
  test(`biological salinity step ${JSON.stringify(value)} by ${step} preserves precision`, () => {
    assert.equal(salinity.stepBiologicalSalinity(value, step), expected);
  });
}
test('shared one-decimal stepping remains unchanged', () => {
  const shared = loadUtility('../src/utils/stepValue.ts');
  assert.equal(shared.incrementDecimalValue('32.15', 5), '37.2');
});

test('initial location commits authoritative detail before failed refresh and recovery only GETs', async () => {
  const h = appHarness();
  const detail = { id: 7, thermal_zone: { id: 12 } };
  const pending = h.context.assignBoxInitialLocation(7, { thermal_zone_id: 12 });
  h.requests[0].resolve(detail);
  await tick();
  assert.equal(h.state.data.boxDetails[7], detail);
  assert.equal(h.state.data.boxes[0], detail);
  h.requests[1].reject(new Error('zones offline'));
  await pending;
  assert.equal(h.state.data.boxDetails[7], detail);
  assert.ok(h.state.recovery);
  const recovery = h.state.recovery();
  assert.equal(h.requests[2].method, 'apiGet');
  assert.equal(h.requests[2].url, '/api/thermal-zones/?limit=80');
  h.requests[2].resolve({ results: [{ id: 12 }] });
  await recovery;
  assert.equal(h.state.recovery, null);
  assert.equal(h.state.data.zones[0].id, 12);
  assert.equal(h.requests.filter(request => request.method === 'apiPost').length, 1);
});
test('initial location POST rejection remains a mutation failure without refresh or recovery', async () => {
  const h = appHarness();
  const pending = h.context.assignBoxInitialLocation(7, {});
  const failure = new Error('POST rejected');
  const rejected = assert.rejects(pending, error => error === failure);
  h.requests[0].reject(failure);
  await rejected;
  assert.equal(h.requests.length, 1);
  assert.equal(h.state.recovery, null);
  assert.equal(h.state.data.boxDetails[7], undefined);
});
for (const stage of ['POST', 'refresh', 'recovery']) {
  test(`initial location ignores obsolete Organization ${stage}`, async () => {
    const h = appHarness();
    const pending = h.context.assignBoxInitialLocation(7, {});
    if (stage !== 'POST') {
      h.requests[0].resolve({ id: 7, thermal_zone: { id: 12 } });
      await tick();
    }
    if (stage === 'recovery') {
      h.requests[1].reject(new Error('offline'));
      await pending;
    }
    const operation = stage === 'recovery' ? h.state.recovery() : pending;
    const rejected = assert.rejects(operation, h.context.ApiResourceCancelledError);
    h.switchOrganization();
    const replacement = { boxes: [], boxDetails: {}, zones: [] };
    h.state.data = replacement;
    h.requests.at(-1).resolve(stage === 'POST' ? { id: 7 } : { results: [{ id: 12 }] });
    await rejected;
    assert.equal(h.state.data, replacement);
    if (stage !== 'recovery') assert.equal(h.state.recovery, null);
  });
}
test('overlapping Box pages keep numeric IDs exactly once in first-occurrence order', async () => {
  const h = appHarness();
  const first = { id: 2, global_code: 'FIRST' };
  const pending = h.context.fetchAllPages('/api/boxes/?limit=2');
  h.requests[0].resolve({ results: [{ id: 1 }, first], next: '/api/boxes/?limit=2&offset=2' });
  await tick();
  h.requests[1].resolve({ results: [{ id: 2, global_code: 'LATER' }, { id: 3 }], next: null });
  const boxes = await pending;
  assert.deepEqual(Array.from(boxes, box => box.id), [1, 2, 3]);
  assert.equal(boxes[1], first);
});
test('pagination retains actual page failures instead of returning partial data', async () => {
  const h = appHarness();
  const pending = h.context.fetchAllPages('/api/boxes/');
  const failure = new Error('second page failed');
  const rejected = assert.rejects(pending, error => error === failure);
  h.requests[0].resolve({ results: [{ id: 1 }], next: '/api/boxes/?offset=1' });
  await tick();
  h.requests[1].reject(failure);
  await rejected;
});
test('pagination rejects malformed nonnumeric IDs instead of hiding them as duplicates', async () => {
  const h = appHarness();
  const pending = h.context.fetchAllPages('/api/boxes/');
  const rejected = assert.rejects(pending, /Invalid pagination item ID/);
  h.requests[0].resolve({ results: [{ id: '1' }], next: null });
  await rejected;
});
