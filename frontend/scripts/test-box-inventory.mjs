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

// Execute the production row callbacks to compare current-stock and last-reading consumers.
function boxRowRenderer(componentName) {
  const component = readFileSync(new URL(`../src/components/${componentName}.tsx`, import.meta.url), 'utf8');
  const ast = ts.createSourceFile(`${componentName}.tsx`, component, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let callback;
  function visit(node) {
    if (ts.isCallExpression(node) && ts.isPropertyAccessExpression(node.expression)
      && node.expression.name.text === 'map') {
      const candidate = node.arguments[0];
      if (candidate && ts.isArrowFunction(candidate)
        && candidate.parameters[0]?.name.getText(ast) === 'box'
        && candidate.body.getText(ast).includes('const measurement = box.latest_measurement;')) {
        assert.equal(callback, undefined, 'Expected one box row callback');
        callback = candidate;
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(callback, `Missing production box row in ${componentName}`);
  const jsx = (type, props, key) => ({ type, props, key });
  const rowExports = {};
  const { outputText } = ts.transpileModule(`export const render = ${callback.getText(ast)};`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  vm.runInNewContext(outputText, {
    exports: rowExports,
    require(name) {
      assert.equal(name, 'react/jsx-runtime');
      return { jsx, jsxs: jsx, Fragment: Symbol('Fragment') };
    },
    ...exports,
    language: 'en', t: key => key, formatDisplayDate: date => date,
    getBoxStatusPresentation: status => ({ tone: status, label: status }),
    isSelectionMode: false, selectedBoxes: new Map(), isLoading: false,
    activeMeasurementFilter: 'older_than', referenceDate: '2026-08-31', ageMonths: '6',
    onOpenBox() {}, onOpenZone() {}, openLifecycleAction() {},
    BoxTrackingPreview() {}, BoxInventoryRowMenu() {}, BoxInventorySuggestion() {},
  });
  return rowExports.render;
}

function rowNodes(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(child => rowNodes(child, predicate));
  if (!node || typeof node !== 'object' || !node.props) return [];
  return [...(predicate(node) ? [node] : []), ...rowNodes(node.props.children, predicate)];
}

function rowText(node) {
  if (Array.isArray(node)) return node.map(rowText).join('');
  if (node == null || typeof node === 'boolean') return '';
  return typeof node === 'object' ? rowText(node.props?.children) : String(node);
}

function rowCell(row, className) {
  const nodes = rowNodes(row, node => node.props.className === className);
  assert.equal(nodes.length, 1, `Expected one ${className}`);
  return nodes[0];
}

const renderZoneRow = boxRowRenderer('ZonesView');
const renderInventoryRow = boxRowRenderer('BoxInventoryAdminSection');
const rowBox = {
  id: 7, global_code: 'ALA-7', status: 'pending_review',
  species: { scientific_name: 'Aurelia aurita' }, thermal_zone: null,
  inventory_created_on: '2024-01-01', current_location_started_at: '2026-01-01T12:00:00Z',
  latest_measurement: { measured_on: '2026-02-27', polyp_count: 120, ephyrae_count: 4 },
};
const currentState = (polypCount, kind, revision) => ({
  polyp_count: polypCount, revision: `box:7:${revision}`,
  source: kind ? { kind, id: revision, timestamp: '2026-08-31T12:00:00Z' } : null,
});

function assertRowCounts(box, current, measured, ephyrae) {
  const zone = renderZoneRow(box);
  assert.equal(rowText(rowCell(zone, 'is-polyps')), `${current} polyps`);
  assert.equal(rowText(rowCell(zone, 'is-ephyrae')), `${ephyrae} ephyrae`);
  const inventory = renderInventoryRow(box);
  assert.equal(rowText(rowCell(inventory, 'box-inventory-cell box-inventory-counts')),
    measured === null ? 'boxInventoryNoMeasurement' : `${measured}polyps${ephyrae}ephyraeFull`);
  return { zone, inventory };
}

test('zone stock follows current state while inventory counts and dates follow only actual readings', () => {
  const before = JSON.stringify(rowBox);
  for (const [count, kind, revision] of [
    [120, 'measurement', 1], [35, 'subculture', 2], [0, 'subculture', 3], [62, 'measurement', 4],
  ]) {
    const measurement = revision === 4
      ? { measured_on: '2026-08-31', polyp_count: 62, ephyrae_count: 2 }
      : rowBox.latest_measurement;
    const box = { ...rowBox, latest_measurement: measurement, current_polyp_state: currentState(count, kind, revision) };
    const { zone, inventory } = assertRowCounts(box, count, measurement.polyp_count, measurement.ephyrae_count);
    assert.equal(rowText(rowCell(zone, 'zone-directory-dates')),
      `zoneCurrentStaySince2026-01-01T12:00:00ZlatestReadingDate${measurement.measured_on}`);
    assert.equal(rowText(rowCell(inventory, 'box-inventory-cell box-inventory-dates')),
      `boxInventoryCreatedOn2024-01-01boxInventoryLastMeasurement${measurement.measured_on}`);
    const suggestions = rowNodes(inventory, node => typeof node.type === 'function'
      && node.type.name === 'BoxInventorySuggestion');
    assert.equal(suggestions.length, revision === 4 ? 0 : 1);
    if (suggestions.length) {
      assert.equal(suggestions[0].props.ageMonths, 6);
      assert.equal(suggestions[0].props.zeroZero, false, 'Current zero is not a measured 0/0');
    }
  }
  assert.equal(JSON.stringify(rowBox), before);
});

test('unknown current stock never falls back to a positive or zero last measurement', () => {
  assertRowCounts({ ...rowBox, current_polyp_state: currentState(null, null, 1) }, '-', 120, 4);
  const { inventory } = assertRowCounts({
    ...rowBox, current_polyp_state: currentState(null, null, 2),
    latest_measurement: { ...rowBox.latest_measurement, polyp_count: 0, ephyrae_count: 0 },
  }, '-', 0, 0);
  const suggestion = rowNodes(inventory, node => node.type?.name === 'BoxInventorySuggestion')[0];
  assert.equal(suggestion.props.zeroZero, true, 'Measured 0/0 remains a real qualification fact');
});

test('child initialization supplies only current polyps, never a reading date or ephyrae', () => {
  for (const count of [18, 0, null]) {
    const { zone, inventory } = assertRowCounts({
      ...rowBox, latest_measurement: null,
      current_polyp_state: currentState(count, count === null ? null : 'subculture_initialization', 1),
    }, count ?? '-', null, '-');
    assert.equal(rowText(rowCell(zone, 'zone-directory-dates')),
      'zoneCurrentStaySince2026-01-01T12:00:00ZlatestReadingDate-');
    assert.equal(rowText(rowCell(inventory, 'box-inventory-cell box-inventory-dates')),
      'boxInventoryCreatedOn2024-01-01boxInventoryLastMeasurementboxInventoryNoData');
    assert.equal(rowNodes(inventory, node => node.type?.name === 'BoxInventorySuggestion').length, 0);
  }
});

test('inventory selection is an explicit row-checkbox mode with compact actions', () => {
  const component = readFileSync(
    new URL('../src/components/BoxInventoryAdminSection.tsx', import.meta.url),
    'utf8',
  );

  assert.match(component, /const \[isSelectionMode, setIsSelectionMode\] = useState\(false\);/);
  assert.match(component, /aria-pressed=\{isSelectionMode\}/);
  assert.match(component, /isSelectionMode \? \(\s*<div className="box-inventory-cell box-inventory-selection-cell"/s);
  assert.match(component, /box\.status === 'pending_review' \? \(/);
  assert.match(component, /onChange=\{\(event\) => toggleBoxSelection\(box, event\.target\.checked\)\}/);
  assert.match(component, /selectedBoxes\.size === 1 \? 'boxInventoryBatchSelectedOne'/);
  assert.match(component, /boxInventoryBatchMakeActive/);
  assert.match(component, /boxInventoryBatchMakeInactive/);
  assert.doesNotMatch(component, /selectAllFilteredResults/);
  assert.doesNotMatch(component, /togglePageSelection/);
  assert.doesNotMatch(component, /box-inventory-selected-boxes/);
  assert.doesNotMatch(component, /boxInventorySelectVisiblePage/);
  assert.doesNotMatch(component, /boxInventorySelectAllFiltered/);
});
