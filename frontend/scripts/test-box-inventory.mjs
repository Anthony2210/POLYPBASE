import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/boxInventory.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports, URLSearchParams });

const baseFilters = {
  ageMonths: '6',
  creationYear: '',
  location: '',
  measurementFilter: '',
  referenceDate: '2026-05-31',
  search: '',
  status: '',
};

test('inventory query serializes filters identically for list and global selection', () => {
  const filters = {
    ...baseFilters,
    ageMonths: '9',
    creationYear: '2021',
    location: '7',
    measurementFilter: 'older_than',
    search: ' ALA ',
    status: 'pending_review',
  };
  const selection = exports.buildBoxInventoryQuery(filters);
  const list = exports.buildBoxInventoryQuery(filters, { limit: 24, offset: 48 });

  assert.equal(selection, 'status=pending_review&location=7&q=ALA&creation_year=2021&reference_date=2026-05-31&measurement_filter=older_than&age_months=9');
  assert.equal(list, `limit=24&offset=48&${selection}`);
});

test('no-measurement filter never sends an age threshold', () => {
  const query = exports.buildBoxInventoryQuery({
    ...baseFilters,
    measurementFilter: 'none',
  });
  assert.equal(query, 'reference_date=2026-05-31&measurement_filter=none');
});

test('qualification filters are active only for pending-review boxes', () => {
  assert.equal(exports.getActiveInventoryMeasurementFilter('pending_review', 'older_than'), 'older_than');
  assert.equal(exports.getActiveInventoryMeasurementFilter('pending_review', 'none'), 'none');
  assert.equal(exports.getActiveInventoryMeasurementFilter('', 'older_than'), '');
  assert.equal(exports.getActiveInventoryMeasurementFilter('active', 'older_than'), '');
  assert.equal(exports.getActiveInventoryMeasurementFilter('inactive', 'none'), '');
});

test('measurement age uses completed calendar months and rejects future dates', () => {
  assert.equal(exports.getMeasurementAgeInMonths('2026-02-28', '2026-05-31'), 3);
  assert.equal(exports.getMeasurementAgeInMonths('2026-03-31', '2026-05-30'), 1);
  assert.equal(exports.getMeasurementAgeInMonths('2026-05-31', '2026-05-31'), 0);
  assert.equal(exports.getMeasurementAgeInMonths('2026-06-01', '2026-05-31'), null);
  assert.equal(exports.getMeasurementAgeInMonths('invalid', '2026-05-31'), null);
});

test('suggestion eligibility mirrors the strict calendar cutoff used by the API', () => {
  assert.equal(exports.isMeasurementOlderThanThreshold('2026-02-27', '2026-08-31', 6), true);
  assert.equal(exports.isMeasurementOlderThanThreshold('2026-02-28', '2026-08-31', 6), false);
  assert.equal(exports.isMeasurementOlderThanThreshold('2026-08-20', '2026-09-03', 6), false);
  assert.equal(exports.isMeasurementOlderThanThreshold('invalid', '2026-09-03', 6), false);
});

test('deactivation suggestions require a pending-review candidate', () => {
  const measurement = { measured_on: '2026-02-27' };
  assert.equal(exports.getDeactivationSuggestionAge(
    'pending_review', measurement, 'older_than', '2026-08-31', 6,
  ), 6);
  assert.equal(exports.getDeactivationSuggestionAge(
    'pending_review', { measured_on: '2026-08-20' }, 'older_than', '2026-09-03', 6,
  ), null);
  assert.equal(exports.getDeactivationSuggestionAge(
    'active', measurement, 'older_than', '2026-08-31', 6,
  ), null);
  assert.equal(exports.getDeactivationSuggestionAge(
    'inactive', measurement, 'older_than', '2026-08-31', 6,
  ), null);
  assert.equal(exports.getDeactivationSuggestionAge(
    'pending_review', measurement, '', '2026-08-31', 6,
  ), null);
});

test('zero-zero is a real measurement and missing remains distinct', () => {
  assert.equal(exports.isZeroZeroMeasurement({ polyp_count: 0, ephyrae_count: 0 }), true);
  assert.equal(exports.isZeroZeroMeasurement({ polyp_count: 0, ephyrae_count: 1 }), false);
  assert.equal(exports.isZeroZeroMeasurement(null), false);
});
