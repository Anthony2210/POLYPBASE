import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/probeChart.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const probeChart = {};
vm.runInNewContext(outputText, { exports: probeChart });

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const NOW = Date.UTC(2026, 9, 29, 1, 0);

test('zoom selections are ordered, at least 30 minutes and never past now', () => {
  assert.deepEqual(
    { ...probeChart.clampProbeWindow(NOW - 2 * HOUR, NOW - 3 * HOUR, NOW) },
    { start: NOW - 3 * HOUR, end: NOW - 2 * HOUR },
  );
  const narrow = probeChart.clampProbeWindow(NOW - 10 * MINUTE, NOW - 9 * MINUTE, NOW);
  assert.equal(narrow.end - narrow.start, 30 * MINUTE);
  assert.ok(narrow.end <= NOW);
});

test('missing periods break the line instead of joining distant points', () => {
  const points = [[0, 1], [MINUTE, 2], [2 * MINUTE, 0], [30 * MINUTE, 3]];
  const segments = probeChart.splitProbeSegments(points, 1);
  assert.equal(segments.length, 2);
  assert.deepEqual([...segments[0].map((point) => point[1])], [1, 2, 0]);
  assert.equal(probeChart.splitProbeSegments(points, 15).length, 2);
});

test('nearest point search picks the closest instant', () => {
  const points = [[0, 1], [10, 2], [20, 3]];
  assert.equal(probeChart.nearestProbePointIndex(points, 14), 1);
  assert.equal(probeChart.nearestProbePointIndex(points, 16), 2);
  assert.equal(probeChart.nearestProbePointIndex([], 16), -1);
});

test('the shared domain includes zero, the target and stays readable when flat', () => {
  const [low, high] = probeChart.probeTemperatureDomain([0, 4], 5);
  assert.ok(low < 0 && high > 5);
  const [flatLow, flatHigh] = probeChart.probeTemperatureDomain([25, 25], null);
  assert.ok(flatHigh - flatLow >= 1);
  assert.equal(probeChart.probeTemperatureDomain([], null), null);
});

for (const minutes of [1, 15, 60]) {
  test('one missing expected reading breaks the line at cadence ' + minutes, () => {
    const cadence = minutes * MINUTE;
    const segments = probeChart.splitProbeSegments([[0, 0], [cadence, 1], [3 * cadence, 0]], minutes);
    assert.equal(segments.length, 2);
    assert.deepEqual([...segments[0].map((point) => point[1])], [0, 1]);
    assert.equal(segments[1][0][1], 0);
  });
}

test('monitoring schedules a server read just after its freshness boundary', () => {
  const monitoring = { last_reading_at: new Date(NOW - 6 * HOUR + 10000).toISOString(), fallback_after_hours: 6, manual_entry_available: false };
  assert.equal(probeChart.probeMonitoringDelay(monitoring, NOW), 11000);
  assert.equal(probeChart.probeMonitoringDelay(monitoring, NOW + MINUTE), 1000);
  assert.equal(probeChart.probeMonitoringDelay({ ...monitoring, manual_entry_available: true }, NOW), MINUTE);
  assert.equal(probeChart.probeMonitoringDelay({ ...monitoring, last_reading_at: null }, NOW), MINUTE);
  assert.equal(probeChart.probeMonitoringDelay({ ...monitoring, last_reading_at: new Date(NOW).toISOString() }, NOW), MINUTE);
});
