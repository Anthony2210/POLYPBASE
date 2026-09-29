import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function load(path, require) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, require, Date });
  return exports;
}

const chartWindow = load('../src/utils/chartWindow.ts');
const scrubber = load('../src/utils/chartScrubber.ts', () => chartWindow);
const { buildScrubberRange, chartDay, chartDayString, isFullChartHistory, moveScrubberRange, resizeScrubberRange } = scrubber;
const dates = (range) => [chartDayString(range.start), chartDayString(range.end)];

test('calendar days remain whole across DST and leap days', () => {
  const previous = process.env.TZ;
  try {
    process.env.TZ = 'Europe/Paris';
    for (const [first, second] of [
      ['2024-02-28', '2024-02-29'],
      ['2024-03-30', '2024-03-31'],
      ['2024-10-26', '2024-10-27'],
    ]) {
      assert.equal(chartDay(second) - chartDay(first), 1);
      assert.equal(chartDayString(chartDay(second)), second);
    }
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});

test('range clamps dates and provides a one-day geometry fallback', () => {
  const range = buildScrubberRange('2024-01-01', '2024-01-01', '2023-01-01', '2023-01-01');
  assert.deepEqual(dates(range), ['2024-01-01', '2024-01-02']);
  assert.equal(range.extentEnd - range.extentStart, 1);
  assert.deepEqual(dates(buildScrubberRange('2024-01-01', '2024-01-10', '2023-12-01', '2025-01-01')),
    ['2024-01-01', '2024-01-10']);
});

test('full history matches both normalized extent endpoints exactly', () => {
  assert.equal(isFullChartHistory(buildScrubberRange('2026-04-15', '2026-09-29', '2026-04-15', '2026-09-29')), true);
  assert.equal(isFullChartHistory(buildScrubberRange('2026-04-15', '2026-09-29', '2026-04-16', '2026-09-29')), false);
  assert.equal(isFullChartHistory(buildScrubberRange('2026-04-15', '2026-09-29', '2026-04-15', '2026-09-28')), false);
  assert.equal(isFullChartHistory(buildScrubberRange('2026-04-15', '2026-09-29', '2026-04-14', '2026-09-30')), true);
  assert.equal(isFullChartHistory(buildScrubberRange('2026-04-15', '2026-04-15', '2026-04-15', '2026-04-15')), true);
});

test('move snaps whole days, preserves duration, and stops at both boundaries', () => {
  const range = buildScrubberRange('2024-01-01', '2024-01-10', '2024-01-03', '2024-01-06');
  assert.deepEqual(dates(moveScrubberRange(range, 2.6)), ['2024-01-06', '2024-01-09']);
  assert.deepEqual(dates(moveScrubberRange(range, 100)), ['2024-01-07', '2024-01-10']);
  assert.deepEqual(dates(moveScrubberRange(range, -100)), ['2024-01-01', '2024-01-04']);
  assert.deepEqual(dates(range), ['2024-01-03', '2024-01-06']);
});

test('resize clamps to extent and enforces one calendar day', () => {
  const range = buildScrubberRange('2024-01-01', '2024-01-10', '2024-01-03', '2024-01-06');
  assert.deepEqual(dates(resizeScrubberRange(range, 'start', range.end + 50)), ['2024-01-05', '2024-01-06']);
  assert.deepEqual(dates(resizeScrubberRange(range, 'end', range.start - 50)), ['2024-01-03', '2024-01-04']);
  assert.deepEqual(dates(resizeScrubberRange(range, 'start', range.extentStart - 50)), ['2024-01-01', '2024-01-06']);
  assert.deepEqual(dates(resizeScrubberRange(range, 'end', range.extentEnd + 50)), ['2024-01-03', '2024-01-10']);
});
