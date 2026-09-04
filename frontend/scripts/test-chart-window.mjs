import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/chartWindow.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
class FixedDate extends Date {
  constructor(...args) { super(...(args.length ? args : ['2026-08-20T12:00:00'])); }
}
vm.runInNewContext(outputText, { exports, Date: FixedDate });

const locationSource = readFileSync(new URL('../src/utils/chartLocations.ts', import.meta.url), 'utf8');
const { outputText: locationOutputText } = ts.transpileModule(locationSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const locationExports = {};
vm.runInNewContext(locationOutputText, { exports: locationExports });

test('preview window includes the latest reading, recent, historical or future', () => {
  for (const latest of ['2026-08-19', '2026-07-31', '2024-02-29', '2023-01-31', '2027-01-01']) {
    const dates = ['2022-01-01', latest];
    const offset = exports.getLatestChartWindowOffset(dates);
    const window = exports.buildChartWindow(dates, offset, 6);
    assert.ok(window.startDate <= latest && window.endDate >= latest, latest);
    assert.equal(dates[0], '2022-01-01');
  }
});
test('preview offset handles empty, invalid and unordered dates', () => {
  assert.equal(exports.getLatestChartWindowOffset([]), 0);
  assert.equal(exports.getLatestChartWindowOffset(['invalid']), 0);
  assert.equal(exports.getLatestChartWindowOffset(['2026-07-31', '2024-01-01']), 0);
});
test('recent movements do not hide an older latest biological reading', () => {
  const sourceDates = ['2024-02-29', '2026-08-01'];
  const offset = exports.getLatestChartWindowOffset(sourceDates, ['2024-02-29'], 6);
  const window = exports.buildChartWindow(sourceDates, offset, 6);
  assert.ok(window.startDate <= '2024-02-29' && window.endDate >= '2024-02-29');
});
test('existing chart windows still clamp before the first reading', () => {
  const window = exports.buildChartWindow(['2026-07-01'], 9999, 6);
  assert.equal(window.offset, 0);
  assert.equal(window.hasOlderWindow, false);
});
test('an inactive location with an unknown end remains visible through its last reading', () => {
  const locations = [{
    id: 1,
    name: 'Armoire 10 C',
    startsAt: '2023-01-01T08:00:00Z',
    endsAt: null,
    endDateUnknown: true,
  }];
  const periods = locationExports.resolveChartLocationPeriods(
    locations,
    '2026-08-20',
    '2023-05-14',
  );

  assert.equal(periods[0].startDate, '2023-01-01');
  assert.equal(periods[0].endDate, '2023-05-14');
  assert.equal(
    locationExports.findChartLocationAtDate(locations, '2023-05-14'),
    'Armoire 10 C',
  );
});
test('an unknown location end is not extended when no reading supports it', () => {
  const periods = locationExports.resolveChartLocationPeriods([{
    id: 1,
    name: 'Armoire 10 C',
    startsAt: '2023-01-01T08:00:00Z',
    endsAt: null,
    endDateUnknown: true,
  }], '2026-08-20', '');

  assert.equal(periods[0].endDate, '2023-01-01');
});
