import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import './test-chart-readability.mjs';
import './test-biological-timeline.mjs';

const source = readFileSync(new URL('../src/utils/chartBiology.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports, Date });

const reading = (id, date, polyps, ephyrae) => ({ id, date, polypCount: polyps, ephyraeCount: ephyrae });

test('zero/zero is a real plotted reading, unlike a missing period', () => {
  const first = reading(1, '2026-02-01', 0, 0);
  const second = reading(2, '2026-02-19', 2, 1);
  const segments = exports.splitMeasurementsOnGaps([first, second]);
  assert.equal(segments.length, 2);
  assert.equal(segments[0][0].polypCount, 0);
  assert.equal(segments[0][0].ephyraeCount, 0);
  assert.equal(exports.splitMeasurementsOnGaps([]).length, 0);
  assert.equal(exports.splitMeasurementsOnGaps([first, reading(3, '2026-02-11', 0, 4)]).length, 1);
});

test('gap threshold counts calendar days even across daylight saving', () => {
  const values = [reading(1, '2026-03-22', 0, 1), reading(2, '2026-04-02', 0, 1)];
  assert.equal(exports.splitMeasurementsOnGaps(values).length, 2);
});

test('legend keeps one series on, without changing readings', () => {
  const initial = { polyps: true, ephyrae: true };
  const onlyEphyrae = exports.toggleChartSeries(initial, 'polyps');
  assert.equal(initial.polyps, true);
  assert.equal(onlyEphyrae.polyps, false);
  assert.equal(exports.toggleChartSeries(onlyEphyrae, 'ephyrae'), onlyEphyrae);
  assert.equal(exports.toggleChartSeries(onlyEphyrae, 'polyps').polyps, true);
});

test('pinned detail takes precedence over focus and hover until explicitly replaced or cleared', () => {
  const first = reading(1, '2026-02-01', 0, 0);
  const second = reading(2, '2026-02-03', 2, 3);
  assert.equal(exports.resolveChartDetail(first, second, second), first);
  assert.equal(exports.resolveChartDetail(second, first, first), second);
  assert.equal(exports.resolveChartDetail(null, first, second), first);
  assert.equal(exports.resolveChartDetail(null, null, second), second);
  assert.equal(exports.resolveChartDetail(null, null, null), null);
});

test('Escape clears only the pinned measurement, event or location detail', () => {
  for (const pinned of [
    { id: 'measurement-1' },
    { id: 'event-transfer-1' },
    { id: 'location-zone-1' },
  ]) {
    const result = exports.dismissPinnedChartDetail('Escape', pinned);
    assert.equal(result.handled, true);
    assert.equal(result.pinned, null);
  }
  const pinned = { id: 'event-transfer-1' };
  const enter = exports.dismissPinnedChartDetail('Enter', pinned);
  assert.equal(enter.handled, false);
  assert.equal(enter.pinned, pinned);
  const space = exports.dismissPinnedChartDetail(' ', pinned);
  assert.equal(space.handled, false);
  assert.equal(space.pinned, pinned);
  const empty = exports.dismissPinnedChartDetail('Escape', null);
  assert.equal(empty.handled, true);
  assert.equal(empty.pinned, null);
});

test('selection only resolves to an observed point in the visible window', () => {
  const visible = [reading(1, '2026-02-01', 0, 0)];
  assert.equal(exports.selectedVisibleReading(visible, 1)?.polypCount, 0);
  assert.equal(exports.selectedVisibleReading(visible, 2), null);
  assert.equal(exports.resolveChartDetail(exports.selectedVisibleReading([], 1), null, null), null);
});

test('detail values retain zeros and omit absent optional salinity', () => {
  const labels = { polyps: 'Polypes', ephyrae: 'Éphyrules', salinity: 'PSU' };
  const values = exports.chartBiologicalValues({ polypCount: 0, ephyraeCount: 0, salinity: 0 }, labels);
  assert.deepEqual(Array.from(values, ({ value }) => value), ['0', '0', '0']);
  assert.equal(exports.chartBiologicalValues({ polypCount: 0, ephyraeCount: 0, salinity: null }, labels).length, 2);
});

test('detail placement prefers above, flips below and clamps to chart bounds', () => {
  const bounds = { width: 240, height: 200 };
  const panel = { width: 100, height: 68 };
  const upper = exports.placeChartDetail({ x: 8, y: 16 }, bounds, panel);
  assert.equal(upper.placement, 'below');
  assert.equal(upper.left, 8);
  assert.equal(upper.top, 24);
  const lower = exports.placeChartDetail({ x: 235, y: 185 }, bounds, panel);
  assert.equal(lower.placement, 'above');
  assert.equal(lower.left, 132);
  assert.equal(lower.top, 109);
  const crowded = exports.placeChartDetail({ x: 120, y: 100 }, bounds, { width: 224, height: 210 });
  assert.ok(crowded.left >= 8);
  assert.ok(crowded.left + 224 <= bounds.width - 8);
  assert.ok(crowded.top >= 8);
  assert.ok(crowded.top + crowded.maxHeight <= bounds.height - 8);
  assert.ok(crowded.top >= 108 || crowded.top + crowded.maxHeight <= 92);
});
