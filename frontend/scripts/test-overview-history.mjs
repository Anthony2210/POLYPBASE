import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as scales from 'd3-scale';
import * as shapes from 'd3-shape';
import { appHarness, ast, deferred, source as appSource } from './app-operation-test-harness.mjs';
import { resourceClient } from './resource-test-harness.mjs';

const compilerOptions = { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 };
function loadModule(relativePath, dependencies = {}) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { ...compilerOptions, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports, Date, AbortController, URLSearchParams,
    require(name) {
      assert.ok(Object.hasOwn(dependencies, name), `Unexpected dependency: ${name}`);
      return dependencies[name];
    },
  }, { filename: relativePath });
  return exports;
}
// Only DOM-facing hooks are stubbed; geometry, JSX, utilities and D3 execute unchanged.
const chartJsx = (type, props, key) => ({ type, props, key });
const trendChart = loadModule('../src/components/BiologicalTrendChart.tsx', {
  react: {
    useState: initial => [initial, () => {}],
    useMemo: factory => factory(),
    useRef: initial => ({ current: initial }),
    useId: () => 'synthetic-overview-chart',
    useEffect() {}, useLayoutEffect() {},
  },
  'react/jsx-runtime': { jsx: chartJsx, jsxs: chartJsx, Fragment: Symbol('Fragment') },
  'd3-scale': scales, 'd3-shape': shapes,
  '../utils/userIdentity': loadModule('../src/utils/userIdentity.ts'),
  '../utils/dateFormat': loadModule('../src/utils/dateFormat.ts'),
  '../utils/chartBiology': loadModule('../src/utils/chartBiology.ts'),
  '../utils/chartLocations': loadModule('../src/utils/chartLocations.ts'),
});
const chartWindow = loadModule('../src/utils/chartWindow.ts');
const chartLocations = loadModule('../src/utils/chartLocations.ts');
const { overviewDefaultRange, normalizeOverviewMeasurements, scopeOverviewTemperatures, createOverviewHistory } = loadModule(
  '../src/utils/overviewHistory.ts', { './chartWindow': chartWindow, './chartLocations': chartLocations },
);
// VM objects have different prototypes; compare their plain, serializable values.
const plain = value => JSON.parse(JSON.stringify(value));

const recentRange = { startDate: '2026-07-03', endDate: '2026-10-03' };
const olderRange = { startDate: '2024-02-01', endDate: '2024-05-01' };
const otherOlderRange = { startDate: '2025-01-15', endDate: '2025-04-15' };
const loadedRange = { startDate: '2026-08-01', endDate: '2026-09-01' };
// Entirely invented fixtures: no operational records, names, or organization data.
const measurements = Array.from({ length: 140 }, (_, index) => {
  const date = new Date(Date.UTC(2024, 1, 1 + index * 7)).toISOString().slice(0, 10);
  return {
    id: index + 1, measured_on: date,
    polyp_count: index % 5 === 0 ? 0 : index * 13,
    ephyrae_count: index % 3 === 0 ? 0 : index * 7,
    strobila_count: index % 4, salinity_psu: index % 3 === 0 ? null : index % 3 === 1 ? '0.00' : '31.25',
    culture_status: 'synthetic', needs_attention: false, notes: 'Synthetic test only', user: null,
    created_at: `${date}T12:00:00Z`, can_edit: false, edit_deadline: null, edit_restriction: 'role_read_only',
  };
});
const expectedMeasurements = measurements.map(point => ({
  date: point.measured_on, polyp_count: point.polyp_count,
  ephyrae_count: point.ephyrae_count, salinity_psu: point.salinity_psu,
}));
const locations = [{
  id: 1, thermal_zone: { id: 1, name: 'Synthetic zone' }, starts_at: '2024-02-01T12:00:00Z',
  ends_at: null, end_date_unknown: false, notes: 'Synthetic test only',
}];
const detailTemperatures = [
  { date: '2026-07-03', average_temperature_c: 0, zone_id: 2, zone_name: 'Previous synthetic zone' },
  { date: '2026-10-03', average_temperature_c: 18, zone_id: 1, zone_name: 'Synthetic zone' },
];
const detail = { id: 901, biological_measurements: [...measurements].reverse(), locations, temperature_history: detailTemperatures };
function initialState() {
  return {
    range: { ...recentRange },
    measurements: expectedMeasurements.filter(point => point.date >= recentRange.startDate),
    locations: [], temperatures: [{ date: '2026-10-03', average_temperature_c: 20 }],
    complete: false, loading: false, failedRange: null,
  };
}
function historyHarness(load = () => Promise.resolve(detail)) {
  const initial = initialState();
  const publications = [];
  let state = initial;
  let loads = 0;
  const controller = createOverviewHistory(initial, recentRange.startDate, () => {
    loads += 1;
    return load();
  }, next => { state = next; publications.push(next); });
  return { controller, initial, publications, get state() { return state; }, get loads() { return loads; } };
}

// Extract the actual callback, not a reimplementation of its organization guard.
// The shared harness supports declarations; handle useCallback arrows locally.
function overviewCallback(harness) {
  let callback;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === 'loadOverviewHistory') callback = node;
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'loadOverviewHistory') {
      const initializer = node.initializer;
      callback = initializer && ts.isCallExpression(initializer)
        ? initializer.arguments[0] : initializer;
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(callback, 'Missing App loadOverviewHistory callback');
  const expression = ts.isFunctionDeclaration(callback)
    ? `(() => { ${callback.getText(ast)}; return loadOverviewHistory; })()`
    : `(${callback.getText(ast)})`;
  return harness.evaluate(`((operationGeneration) => ${expression})(organizationRequestGenerationRef.current)`);
}

test('initial temperatures follow recorded residence and gaps without inventing unknown ends', () => {
  const location = (id, zone, start, end, unknown = false) => ({
    id, thermal_zone: { id: zone, name: `Synthetic ${zone}` },
    starts_at: `${start}T12:00:00Z`, ends_at: end ? `${end}T12:00:00Z` : null,
    end_date_unknown: unknown,
  });
  const box = {
    thermal_zone: { id: 1, name: 'Synthetic 1' }, history_end_date: '2026-10-03',
    locations: [
      location(1, 1, '2026-07-01', null, true),
      location(2, 2, '2026-07-10', '2026-08-01'),
      location(3, 1, '2026-09-01', null),
    ],
    temperatures: ['2026-06-30', '2026-07-01', '2026-07-09', '2026-07-10',
      '2026-08-01', '2026-08-15', '2026-09-01', '2026-10-03', '2026-10-04']
      .map(date => ({ date, average_temperature_c: 0 })),
  };
  const before = plain(box);
  assert.deepEqual(plain(scopeOverviewTemperatures(box)), box.temperatures.filter(point =>
    ['2026-07-01', '2026-09-01', '2026-10-03'].includes(point.date))
    .map(point => ({ ...point, zone_id: 1, zone_name: 'Synthetic 1' })));
  assert.deepEqual(box, before, 'Do not mutate source periods or temperatures');
  assert.deepEqual(plain(scopeOverviewTemperatures({ ...box, thermal_zone: null })), []);
  assert.deepEqual(plain(scopeOverviewTemperatures({ ...box, locations: [] })), []);
  assert.deepEqual(plain(scopeOverviewTemperatures({ ...box, temperatures: [] })), []);
  assert.deepEqual(plain(scopeOverviewTemperatures({ ...box, locations: [
    location(4, 1, '2026-09-01', null, true),
  ] })), [{ ...box.temperatures.find(point => point.date === '2026-09-01'), zone_id: 1, zone_name: 'Synthetic 1' }],
  'An unknown terminal end must not imply continuing residence');
});

test('initial temperatures do not extend an unknown occupancy end up to its successor', () => {
  const box = {
    thermal_zone: { id: 1, name: 'Synthetic A' }, history_end_date: '2026-10-03',
    locations: [
      { id: 1, thermal_zone: { id: 1, name: 'Synthetic A' }, starts_at: '2026-07-01T12:00:00Z', ends_at: null, end_date_unknown: true },
      { id: 2, thermal_zone: { id: 2, name: 'Synthetic B' }, starts_at: '2026-07-10T12:00:00Z', ends_at: '2026-08-01T12:00:00Z' },
      { id: 3, thermal_zone: { id: 1, name: 'Synthetic A' }, starts_at: '2026-09-01T12:00:00Z', ends_at: null, end_date_unknown: false },
    ],
    temperatures: ['2026-07-01', '2026-07-09', '2026-08-15', '2026-09-01']
      .map(date => ({ date, average_temperature_c: 0 })),
  };
  assert.deepEqual([...scopeOverviewTemperatures(box)].map(point => point.date), ['2026-07-01', '2026-09-01']);
});

test('initial current-zone values retain inclusive departure days but reject other residence and gaps', () => {
  const box = {
    thermal_zone: { id: 1, name: 'Synthetic A' }, history_end_date: '2026-06-30',
    locations: [
      { id: 1, thermal_zone: { id: 1 }, starts_at: '2026-06-01T12:00:00Z', ends_at: '2026-06-15T10:00:00Z', end_date_unknown: false },
      { id: 2, thermal_zone: { id: 2 }, starts_at: '2026-06-15T10:00:00Z', ends_at: '2026-06-20T12:00:00Z', end_date_unknown: false },
      { id: 3, thermal_zone: { id: 1 }, starts_at: '2026-06-25T12:00:00Z', ends_at: null, end_date_unknown: false },
    ],
    temperatures: ['2026-05-31', '2026-06-15', '2026-06-16', '2026-06-21', '2026-06-25', '2026-07-01']
      .map(date => ({ date, average_temperature_c: 0 })),
  };
  assert.deepEqual(plain(scopeOverviewTemperatures(box)), ['2026-06-15', '2026-06-25']
    .map(date => ({ date, average_temperature_c: 0, zone_id: 1, zone_name: 'Synthetic A' })));
});

test('default range is three calendar months, including month-end clamping and year boundaries', () => {
  for (const [year, month, day, startDate, endDate] of [
    [2026, 10, 3, '2026-07-03', '2026-10-03'],
    [2024, 5, 31, '2024-02-29', '2024-05-31'],
    [2025, 5, 31, '2025-02-28', '2025-05-31'],
    [2024, 2, 29, '2023-11-29', '2024-02-29'],
    [2026, 1, 31, '2025-10-31', '2026-01-31'],
  ]) {
    assert.deepEqual(plain(overviewDefaultRange(new Date(year, month - 1, day, 23, 30))), { startDate, endDate });
  }
});

test('normalization preserves all 140 exact dates, both counts, zero and null salinity without mutating input', () => {
  const input = [...measurements].reverse().map(point => Object.freeze({ ...point }));
  Object.freeze(input);
  const before = plain(input);
  const result = plain(normalizeOverviewMeasurements(input));
  assert.equal(result.length, 140);
  assert.deepEqual(result, expectedMeasurements);
  assert.deepEqual(plain(input), before);
  assert.ok(result.some(point => point.polyp_count === 0 && point.ephyrae_count === 0));
  assert.ok(result.some(point => point.polyp_count > 0 && point.ephyrae_count === 0));
  assert.ok(result.some(point => point.polyp_count === 0 && point.ephyrae_count > 0));
  assert.ok(result.some(point => point.salinity_psu === null));
  assert.ok(result.some(point => point.salinity_psu === '0.00'));
  assert.ok(result.some(point => point.date === '2024-02-29'));
  assert.deepEqual(plain(normalizeOverviewMeasurements([])), []);
});

test('overview normalization and actual mini-chart retain source environments without canonical fallback', () => {
  const sourceLocations = [{ id: 11, thermal_zone: { id: 2, name: 'JKA zone 2' },
    starts_at: '2024-02-19', ends_at: null, end_date_unknown: true }];
  const input = [
    { ...measurements[0], environment_source_box: 2141, environment_locations: [] },
    { ...measurements[1], environment_source_box: 2141, environment_locations: sourceLocations },
    { ...measurements[2], environment_source_box: null, environment_locations: null },
    { ...measurements[3], duplicate_of: measurements[0].id },
  ];
  const normalized = normalizeOverviewMeasurements(input);
  assert.equal(normalized.length, 3);
  assert.deepEqual(plain(normalized[0].environment_locations), []);
  assert.deepEqual(plain(normalized[1].environment_locations), sourceLocations);
  assert.equal(normalized[2].environment_locations, null);
  const view = miniChartHarness(componentBox(input, true));
  try {
    assert.deepEqual(plain(view.chart.measurements[0].environmentLocations), []);
    assert.equal(view.chart.measurements[1].environmentLocations[0].name, 'JKA zone 2');
    assert.equal(view.chart.measurements[1].environmentLocations[0].endDateUnknown, true);
    assert.equal(view.chart.measurements[2].environmentLocations, null);
    assert.equal(view.chart.labels.temperature, 'temperature');
  } finally { view.unmount(); }
});

test('recent-only initial data makes no GET; older selection commits complete App detail and retains selected range', async () => {
  const app = appHarness();
  const load = overviewCallback(app);
  const history = historyHarness(() => load(detail.id));
  assert.equal(app.requests.length, 0);
  assert.ok(history.state.measurements.length > 0 && history.state.measurements.length < 100);
  assert.ok(history.state.measurements.every(point => point.date >= recentRange.startDate));
  await history.controller.select(recentRange);
  assert.equal(app.requests.length, 0);
  const selection = history.controller.select(olderRange);
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].method, 'apiGet');
  assert.equal(app.requests[0].url, '/api/boxes/901/');
  app.requests[0].resolve(detail);
  await selection;
  assert.deepEqual(plain(history.state), {
    range: olderRange, measurements: expectedMeasurements, locations, temperatures: detailTemperatures,
    complete: true, loading: false, failedRange: null,
  });
  assert.ok(history.state.measurements.some(point => point.date > olderRange.endDate), 'Commit all history, not just the visible range');
  for (const range of [otherOlderRange, loadedRange, recentRange, olderRange]) {
    await history.controller.select(range);
    assert.deepEqual(plain(history.state.range), range);
    assert.deepEqual(plain(history.state.measurements), expectedMeasurements);
    assert.deepEqual(plain(history.state.temperatures), detailTemperatures);
  }
  assert.equal(app.requests.length, 1);
  assert.equal(history.loads, 1);
});

test('deferred loading keeps the last valid selected range, measurements and locations', async () => {
  const request = deferred();
  const history = historyHarness(() => request.promise);
  await history.controller.select(loadedRange);
  const valid = history.state;
  const selection = history.controller.select(olderRange);
  assert.deepEqual(plain(history.state.range), loadedRange);
  assert.equal(history.state.measurements, valid.measurements);
  assert.equal(history.state.locations, valid.locations);
  assert.equal(history.state.temperatures, valid.temperatures);
  assert.equal(history.state.complete, false);
  assert.equal(history.state.loading, true);
  assert.equal(history.state.failedRange, null);
  request.resolve(detail);
  await selection;
  assert.deepEqual(plain(history.state.range), olderRange);
  assert.equal(history.state.loading, false);
});

test('failure preserves valid data and range, exposes failedRange, and retry commits full history', async () => {
  const requests = [deferred(), deferred()];
  let index = 0;
  const history = historyHarness(() => requests[index++].promise);
  await history.controller.select(loadedRange);
  const valid = history.state;
  const selection = history.controller.select(olderRange);
  requests[0].reject(new Error('Synthetic GET failure'));
  await selection;
  assert.deepEqual(plain(history.state.range), loadedRange);
  assert.equal(history.state.measurements, valid.measurements);
  assert.equal(history.state.locations, valid.locations);
  assert.equal(history.state.temperatures, valid.temperatures);
  assert.equal(history.state.complete, false);
  assert.equal(history.state.loading, false);
  assert.deepEqual(plain(history.state.failedRange), olderRange);
  const retry = history.controller.select(history.state.failedRange);
  assert.equal(history.loads, 2);
  assert.equal(history.state.failedRange, null);
  assert.equal(history.state.loading, true);
  assert.deepEqual(plain(history.state.range), loadedRange);
  requests[1].resolve(detail);
  await retry;
  assert.deepEqual(plain(history.state.measurements), expectedMeasurements);
  assert.deepEqual(plain(history.state.range), olderRange);
  assert.equal(history.state.complete, true);
  assert.equal(history.state.failedRange, null);
});

for (const outcome of ['success', 'failure']) {
  test(`dispose suppresses late ${outcome} publications`, async () => {
    const request = deferred();
    const history = historyHarness(() => request.promise);
    const selection = history.controller.select(olderRange);
    history.controller.dispose();
    const publications = history.publications.length;
    const state = history.state;
    if (outcome === 'success') request.resolve(detail);
    else request.reject(new Error('Synthetic late failure'));
    await selection;
    assert.equal(history.publications.length, publications);
    assert.equal(history.state, state);
  });
}

test('competing older ranges share one pending GET and only the latest selection commits', async () => {
  const request = deferred();
  const history = historyHarness(() => request.promise);
  const first = history.controller.select(olderRange);
  const second = history.controller.select(otherOlderRange);
  assert.equal(history.loads, 1);
  assert.deepEqual(plain(history.state.range), recentRange);
  request.resolve(detail);
  await Promise.all([first, second]);
  const commits = history.publications.filter(state => state.complete);
  assert.equal(commits.length, 1);
  assert.deepEqual(plain(commits[0].range), otherOlderRange);
  assert.deepEqual(plain(commits[0].measurements), expectedMeasurements);
});

for (const outcome of ['success', 'failure']) {
  test(`choosing an already-loaded range cancels an older selection despite late ${outcome}`, async () => {
    const request = deferred();
    const history = historyHarness(() => request.promise);
    const older = history.controller.select(olderRange);
    await history.controller.select(loadedRange);
    const valid = history.state;
    const publications = history.publications.length;
    assert.deepEqual(plain(valid.range), loadedRange);
    assert.equal(valid.loading, false);
    assert.equal(valid.failedRange, null);
    if (outcome === 'success') request.resolve(detail);
    else request.reject(new Error('Synthetic cancelled failure'));
    await older;
    assert.equal(history.state, valid);
    assert.equal(history.publications.length, publications);
    assert.equal(history.loads, 1);
  });
}

test('normalization preserves date-only values and leap day in multiple real process time zones', () => {
  const childCode = `
    import assert from 'node:assert/strict';
    import { readFileSync } from 'node:fs';
    import vm from 'node:vm';
    import ts from 'typescript';
    const compilerOptions = ${JSON.stringify(compilerOptions)};
    ${loadModule.toString()}
    const chartWindow = loadModule('../src/utils/chartWindow.ts');
    const utility = loadModule('../src/utils/overviewHistory.ts', {
      './chartWindow': chartWindow, './chartLocations': loadModule('../src/utils/chartLocations.ts'),
    });
    const input = ${JSON.stringify([...measurements].reverse())};
    const result = utility.normalizeOverviewMeasurements(input);
    assert.deepEqual(JSON.parse(JSON.stringify(result)), ${JSON.stringify(expectedMeasurements)});
    const range = utility.overviewDefaultRange(new Date(2024, 4, 31, 0, 15));
    assert.deepEqual(JSON.parse(JSON.stringify(range)), { startDate: '2024-02-29', endDate: '2024-05-31' });
    console.log(new Date(2024, 1, 29, 12).getTimezoneOffset());
  `.replaceAll('new URL(relativePath, import.meta.url)', `new URL(relativePath, ${JSON.stringify(import.meta.url)})`);
  const offsets = new Set();
  for (const TZ of ['UTC', 'Europe/Paris', 'America/Los_Angeles', 'Pacific/Kiritimati']) {
    const result = spawnSync(process.execPath, ['--input-type=module'], {
      input: childCode, cwd: new URL('..', import.meta.url), env: { ...process.env, TZ }, encoding: 'utf8', timeout: 15000,
    });
    assert.equal(result.status, 0, `${TZ}: ${result.error ?? result.stderr}`);
    offsets.add(result.stdout.trim());
  }
  assert.ok(offsets.size >= 3, 'Child processes must actually use different time zones');
});

for (const outcome of ['success', 'failure']) {
  test(`actual App callback rejects an organization-stale ${outcome} without publishing foreign history`, async () => {
    const app = appHarness();
    const load = overviewCallback(app);
    const history = historyHarness(() => load(detail.id));
    const selection = history.controller.select(olderRange);
    assert.equal(app.requests.length, 1);
    app.switchOrganization();
    if (outcome === 'success') app.requests[0].resolve(detail);
    else app.requests[0].reject(new Error('Synthetic old organization failure'));
    await selection;
    assert.equal(history.state.measurements, history.initial.measurements);
    assert.equal(history.state.locations, history.initial.locations);
    assert.deepEqual(plain(history.state.range), recentRange);
    assert.equal(history.state.complete, false);
    assert.ok(!history.publications.some(state => state.complete));
    await assert.rejects(load(detail.id), error => error instanceof app.context.ApiResourceCancelledError);
    assert.equal(app.requests.length, 1, 'A stale callback must not issue a new GET');
    const freshLoad = overviewCallback(app);
    const fresh = freshLoad(detail.id);
    assert.equal(app.requests.length, 2);
    app.requests[1].resolve(detail);
    assert.equal(await fresh, detail);
  });
}

test('actual App callback exposes cancellation rather than returning a stale detail', async () => {
  const app = appHarness();
  const load = overviewCallback(app);
  const result = load(detail.id);
  const rejected = assert.rejects(result, error => error instanceof app.context.ApiResourceCancelledError);
  app.switchOrganization();
  app.requests[0].resolve(detail);
  await rejected;
});

test('supplemental component wiring uses the controller, cleanup, retry and organization remount', () => {
  const view = readFileSync(new URL('../src/components/OverviewView.tsx', import.meta.url), 'utf8');
  assert.match(appSource, /<OverviewView\s+key=\{activeOrganizationId\}\s+loadHistory=\{loadOverviewHistory\}/);
  assert.match(view, /createOverviewHistory\(initialState, box\.history_start_date/);
  assert.match(view, /\(\) => loadHistory\(box\.id\)/);
  assert.match(view, /current\.dispose\(\)/);
  assert.match(view, /controller\.current\?\.select\(history\.failedRange\)/);
  assert.match(view, /const \{ startDate, endDate \} = history\.range/);
});

function miniChartHarness(box, loadHistory, t = key => key) {
  const viewSource = readFileSync(new URL('../src/components/OverviewView.tsx', import.meta.url), 'utf8');
  const viewAst = ts.createSourceFile('OverviewView.tsx', viewSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const componentNode = viewAst.statements.find(node =>
    ts.isFunctionDeclaration(node) && node.name?.text === 'OverviewMiniChart');
  assert.ok(componentNode, 'Missing actual OverviewMiniChart component');
  const slots = [];
  const requests = [];
  const temperatureRequests = [];
  const selections = [];
  let cursor = 0;
  let dirty = false;
  let effects = new Map();
  let tree;
  const sameDeps = (left, right) => left && right && left.length === right.length
    && left.every((value, index) => Object.is(value, right[index]));
  const hooks = {
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, next => {
        const value = typeof next === 'function' ? next(slots[index].value) : next;
        if (!Object.is(value, slots[index].value)) {
          slots[index].value = value;
          dirty = true;
        }
      }];
    },
    useMemo(factory, deps) {
      const index = cursor++;
      if (!sameDeps(slots[index]?.deps, deps)) slots[index] = { deps, value: factory() };
      return slots[index].value;
    },
    useRef(initial) {
      const index = cursor++;
      slots[index] ??= { value: { current: initial } };
      return slots[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!sameDeps(slots[index]?.deps, deps)) effects.set(index, { effect, deps });
    },
  };
  const temperatureClient = resourceClient({ fetch: (url, options) => {
    const request = { ...deferred(), url, options };
    temperatureRequests.push(request);
    return request.promise;
  } });
  const temperatureUtility = loadModule('../src/utils/temperatureHistory.ts', { '../api/client': temperatureClient });
  const { useTemperatureHistory } = loadModule('../src/hooks/useTemperatureHistory.ts', {
    react: hooks, '../api/client': temperatureClient, '../utils/temperatureHistory': temperatureUtility,
  });
  const TemperatureHistoryStatus = function TemperatureHistoryStatus() {};
  // Mock leaf components only; execute the production component's full JSX and hooks.
  const ChartWindowControls = function ChartWindowControls() {};
  const BiologicalTrendChart = function BiologicalTrendChart() {};
  const jsx = (type, props, key) => ({ type, props, key });
  const exports = {};
  const { outputText } = ts.transpileModule(`
    import { useState, useMemo, useRef, useEffect } from 'react';
    ${componentNode.getText(viewAst)}
    export { OverviewMiniChart };
  `, { compilerOptions: { ...compilerOptions, jsx: ts.JsxEmit.ReactJSX } });
  vm.runInNewContext(outputText, {
    exports, Date, ChartWindowControls, BiologicalTrendChart, overviewDefaultRange, scopeOverviewTemperatures,
    useTemperatureHistory, TemperatureHistoryStatus,
    createOverviewHistory(...args) {
      const controller = createOverviewHistory(...args);
      return { ...controller, select(range) {
        selections.push(plain(range));
        return controller.select(range);
      } };
    },
    require(name) {
      if (name === 'react') return hooks;
      if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: Symbol('Fragment') };
      assert.fail(`Unexpected component dependency: ${name}`);
    },
  }, { filename: 'OverviewMiniChart.tsx' });
  const props = {
    box, language: 'en', t,
    loadHistory: loadHistory ?? (boxId => {
      const request = { ...deferred(), boxId };
      requests.push(request);
      return request.promise;
    }),
  };
  function render() {
    let iterations = 0;
    do {
      assert.ok(++iterations < 20, 'Component renders and effects must settle');
      dirty = false;
      cursor = 0;
      effects = new Map();
      tree = exports.OverviewMiniChart(props);
      for (const [index, { effect, deps }] of effects) {
        slots[index]?.cleanup?.();
        slots[index] = { deps };
        slots[index].cleanup = effect();
      }
    } while (dirty);
  }
  function find(predicate) {
    function visit(node) {
      if (Array.isArray(node)) return node.flatMap(visit);
      if (!node || typeof node !== 'object' || !node.props) return [];
      return [...(predicate(node) ? [node] : []), ...visit(node.props.children), ...visit(node.props.temperatureStatus)];
    }
    return visit(tree);
  }
  function single(type) {
    const nodes = find(node => node.type === type);
    assert.equal(nodes.length, 1, `Expected one ${type.name}`);
    return nodes[0].props;
  }
  render();
  return {
    requests, temperatureRequests, selections, find, render,
    async settleTemperature(request, results) {
      request.resolve({ ok: true, headers: new Headers({ 'Content-Type': 'application/json' }),
        json: async () => ({ results, count: results.length, next: null, previous: null }) });
      await new Promise(resolve => setImmediate(resolve));
      render();
    },
    async rejectTemperature(request) {
      request.reject(new Error('Synthetic temperature failure'));
      await new Promise(resolve => setImmediate(resolve));
      render();
    },
    updateBox(nextBox) { props.box = nextBox; render(); },
    get temperatureStatus() { return single(TemperatureHistoryStatus); },
    get controls() { return single(ChartWindowControls); },
    get chart() { return single(BiologicalTrendChart); },
    select(range) { single(ChartWindowControls).onChange(range.startDate, range.endDate); render(); },
    async settle(request, fullDetail) {
      request.resolve(fullDetail);
      await new Promise(resolve => setImmediate(resolve));
      render();
    },
    unmount() { slots.forEach(slot => slot?.cleanup?.()); },
  };
}

const serverRange = { startDate: '2026-03-03', endDate: '2026-06-03' };
const fullComponentRange = { startDate: '2024-02-19', endDate: '2026-06-03' };
const componentLocations = [{ ...locations[0], starts_at: '2024-02-18T12:00:00Z' }];
const componentMeasurements = measurements.slice(0, 120).map((point, index) => {
  const date = new Date(Date.UTC(2024, 1, 19 + index * 7)).toISOString().slice(0, 10);
  return { ...point, measured_on: date, created_at: `${date}T12:00:00Z` };
});
function componentBox(points, complete = false) {
  return {
    id: 902, history_complete: complete, history_start_date: serverRange.startDate, history_end_date: serverRange.endDate,
    earliest_biological_measurement_on: '2024-02-19', locations: componentLocations,
    measurements: plain(normalizeOverviewMeasurements(points)),
  };
}
const chartPoints = points => points.map(point => ({
  id: point.measured_on, date: point.measured_on, polypCount: point.polyp_count,
  ephyraeCount: point.ephyrae_count, salinity: point.salinity_psu, environmentLocations: null,
}));
function assertComponentRange(view, range) {
  for (const props of [view.controls, view.chart]) {
    assert.equal(props.startDate, range.startDate);
    assert.equal(props.endDate, range.endDate);
  }
  assert.equal(view.controls.extentStart, '2024-02-19');
  assert.equal(view.controls.extentEnd, '2026-06-03');
}

test('actual mini-chart JSX defaults to the full history, loads it at once and keeps shorter periods explorable', async () => {
  const recent = componentMeasurements.filter(point => point.measured_on >= serverRange.startDate);
  const view = miniChartHarness(componentBox(recent));
  try {
    assertComponentRange(view, fullComponentRange);
    assert.notEqual(view.chart.startDate, '2024-02-18', 'An old location must not anchor the default graph');
    assert.notEqual(view.controls.extentStart, '2024-02-18', 'Slider starts at biology, not location');
    assert.deepEqual(plain(view.chart.measurements), chartPoints(recent), 'Loaded readings stay until the rest arrives');
    assert.equal(view.chart.locations[0].startsAt, '2024-02-18T12:00:00Z');
    assert.equal(view.requests.length, 1, 'A partial payload loads the full history on mount');
    assert.equal(view.requests[0].boxId, 902);
    assert.equal(view.find(node => node.props.role === 'status').length, 1);
    await view.settle(view.requests[0], {
      id: 902, biological_measurements: [...componentMeasurements].reverse(), locations: componentLocations,
    });
    assertComponentRange(view, fullComponentRange);
    assert.deepEqual(plain(view.chart.measurements), chartPoints(componentMeasurements));
    assert.equal(view.chart.measurements.length, 120);
    assert.ok(view.chart.measurements.some(point => point.polypCount === 0 && point.ephyraeCount === 0));
    assert.ok(view.chart.measurements.some(point => point.salinity === null));
    assert.ok(view.chart.measurements.some(point => point.salinity === '0.00'));
    assert.equal(view.find(node => node.props.role === 'status').length, 0);
    view.select(serverRange);
    assert.equal(view.chart.startDate, serverRange.startDate);
    assert.equal(view.controls.extentStart, '2024-02-19', 'Shorter period keeps the complete extent');
    assert.equal(view.chart.measurements.length, 120, 'Readings stay loaded for navigation');
    view.select(fullComponentRange);
    assertComponentRange(view, fullComponentRange);
    assert.equal(view.requests.length, 1);
  } finally { view.unmount(); }
});

test('mini-chart loads in-range temperatures independently without bounded payload fallback', async () => {
  const initialTemperatures = [
    { date: '2024-01-01', average_temperature_c: 99 },
    { date: '2026-06-01', average_temperature_c: 0 },
  ];
  const box = { ...componentBox(componentMeasurements), thermal_zone: { id: 1, name: 'Synthetic zone' }, temperatures: initialTemperatures };
  const view = miniChartHarness(box, undefined, key => `translated:${key}`);
  const loadedTemperatures = [
    { date: '2026-03-03', average_temperature_c: 0, zone_id: 2, zone_name: 'Previous synthetic zone' },
    { date: '2026-06-01', average_temperature_c: 17, zone_id: 1, zone_name: 'Synthetic zone' },
  ];
  try {
    assert.deepEqual(plain(view.chart.temperatures), []);
    assert.equal(view.temperatureRequests.length, 1);
    assert.equal(view.chart.labels.temperature, 'translated:temperature');
    await view.settle(view.requests[0], {
      id: box.id, biological_measurements: componentMeasurements, locations: componentLocations,
      temperature_history: loadedTemperatures,
    });
    assert.deepEqual(plain(view.chart.temperatures), [], 'Detail temperatures are not the chart data source');
    for (const range of [serverRange, olderRange]) {
      view.select(range);
      assert.deepEqual(plain(view.chart.temperatures), []);
      const request = view.temperatureRequests.at(-1);
      const query = new URL(request.url, 'https://test').searchParams;
      assert.equal(query.get('start'), range.startDate);
      assert.equal(query.get('end'), range.endDate);
      const inRange = loadedTemperatures.filter(point => point.date >= range.startDate && point.date <= range.endDate);
      await view.settleTemperature(request, inRange);
      assert.deepEqual(plain(view.chart.temperatures), inRange);
      assert.equal(view.temperatureStatus.loading, false);
      assert.equal(view.temperatureStatus.failed, false);
      assert.deepEqual(plain(view.temperatureStatus.points), inRange);
    }
    assert.equal(view.requests.length, 1);
    view.updateBox({ ...box, id: 903, history_complete: true, temperatures: [] });
    assert.deepEqual(plain(view.chart.temperatures), [], 'A new box must not retain the previous detail temperatures');
  } finally { view.unmount(); }
});

test('an Overview box without biology keeps temperature-only loading, inline failure/retry and successful empty coverage', async () => {
  const box = { ...componentBox([], true), earliest_biological_measurement_on: null };
  const view = miniChartHarness(box);
  const observed = [{ date: '2026-05-15', average_temperature_c: 0, min_temperature_c: null,
    max_temperature_c: null, zone_id: 11, zone_name: 'Synthetic historical zone' }];
  try {
    assert.deepEqual(plain(view.chart.measurements), []);
    assert.equal(view.chart.labels.empty, 'overviewNoHistoryEver');
    assert.ok(view.chart.temperatureStatus);
    assert.equal(view.temperatureStatus.loading, true);
    assert.equal(view.temperatureStatus.failed, false);
    assert.equal(view.requests.length, 0, 'Temperature loading is independent of biological history');
    await view.rejectTemperature(view.temperatureRequests[0]);
    assert.equal(view.temperatureStatus.loading, false);
    assert.equal(view.temperatureStatus.failed, true);
    assert.equal(view.find(node => node.props.role === 'alert').length, 0, 'Temperature failures are not standalone alerts');
    view.temperatureStatus.retry(); view.render();
    assert.equal(view.temperatureStatus.loading, true);
    assert.equal(view.temperatureStatus.failed, false);
    await view.settleTemperature(view.temperatureRequests[1], observed);
    assert.deepEqual(plain(view.chart.temperatures), observed);
    assert.deepEqual(plain(view.temperatureStatus.points), observed);
    assert.deepEqual(plain(view.chart.measurements), [], 'A temperature zero does not invent a biological zero');
    assert.equal(view.temperatureStatus.loading, false);
    assert.equal(view.temperatureStatus.failed, false);
    const requestCount = view.temperatureRequests.length;
    view.select({ startDate: '2026-04-01', endDate: '2026-04-02' });
    assert.deepEqual(plain(view.chart.temperatures), []);
    assert.deepEqual(plain(view.temperatureStatus.points), []);
    assert.equal(view.temperatureStatus.loading, false);
    assert.equal(view.temperatureStatus.failed, false);
    assert.equal(view.temperatureRequests.length, requestCount, 'Covered empty days do not reload');
    view.select({ startDate: '2026-05-14', endDate: '2026-05-16' });
    assert.deepEqual(plain(view.chart.temperatures), observed);
    assert.equal(view.temperatureRequests.length, requestCount);
  } finally { view.unmount(); }
});

test('A to B detail keeps both occupied zones, including both daily aggregates on the movement day', async () => {
  const movementDay = '2026-06-15';
  const occupiedLocations = [
    { id: 11, thermal_zone: { id: 11, name: 'Synthetic A' }, starts_at: '2026-05-01T12:00:00Z', ends_at: `${movementDay}T10:00:00Z`, end_date_unknown: false },
    { id: 22, thermal_zone: { id: 22, name: 'Synthetic B' }, starts_at: `${movementDay}T10:00:00Z`, ends_at: null, end_date_unknown: false },
  ];
  const temperatures = [
    { date: '2026-06-08', average_temperature_c: 0, min_temperature_c: null, max_temperature_c: null, zone_id: 11, zone_name: 'Synthetic A' },
    { date: movementDay, average_temperature_c: 16, zone_id: 11, zone_name: 'Synthetic A' },
    { date: movementDay, average_temperature_c: 22, zone_id: 22, zone_name: 'Synthetic B' },
    { date: '2026-06-22', average_temperature_c: 23, zone_id: 22, zone_name: 'Synthetic B' },
  ];
  const box = { ...componentBox(componentMeasurements), history_end_date: '2026-06-30', thermal_zone: { id: 22, name: 'Synthetic B' },
    locations: occupiedLocations, temperatures: [
      { date: '2026-06-08', average_temperature_c: 99 },
      { date: movementDay, average_temperature_c: 22 },
      { date: '2026-06-22', average_temperature_c: 23 },
    ] };
  const view = miniChartHarness(box);
  try {
    assert.deepEqual(plain(view.chart.temperatures), [], 'Never backfill the range from current-zone temperatures');
    await view.settle(view.requests[0], { id: box.id, biological_measurements: componentMeasurements,
      locations: occupiedLocations, temperature_history: temperatures });
    view.select({ startDate: '2026-06-01', endDate: '2026-06-30' });
    await view.settleTemperature(view.temperatureRequests.at(-1), temperatures);
    assert.deepEqual(plain(view.chart.temperatures), temperatures,
      'The server has scoped detail points per occupancy; never filter detail to current B');
    const details = [];
    const jsx = (type, props, key) => ({ type, props, key });
    const chart = loadModule('../src/components/BiologicalTrendChart.tsx', {
      react: { useState: initial => [initial, value => details.push(value)], useMemo: factory => factory(),
        useRef: current => ({ current }), useId: () => 'occupancy-chart', useEffect() {}, useLayoutEffect() {} },
      'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: Symbol('Fragment') },
      'd3-scale': scales, 'd3-shape': shapes,
      '../utils/userIdentity': loadModule('../src/utils/userIdentity.ts'),
      '../utils/dateFormat': loadModule('../src/utils/dateFormat.ts'),
      '../utils/chartBiology': loadModule('../src/utils/chartBiology.ts'),
      '../utils/chartLocations': chartLocations,
    });
    const tree = chart.default({ ...view.chart, startDate: '2026-06-01', endDate: '2026-06-30' });
    const points = [];
    function visit(node) {
      if (Array.isArray(node)) return node.forEach(visit);
      if (!node || typeof node !== 'object') return;
      if (node.props?.className === 'bio-trend-temperature-point') points.push(node);
      visit(node.props?.children);
    }
    visit(tree);
    assert.equal(points.length, 4);
    points.forEach((node, index) => {
      const expected = temperatures[index];
      node.props.onFocus();
      const detail = details.at(-1);
      assert.equal(detail.date, expected.date);
      assert.ok(detail.lines.some(line => line.kind === 'temperature' && line.value === String(expected.average_temperature_c)));
      assert.ok(detail.lines.some(line => line.kind === 'location' && line.value === expected.zone_name));
      assert.ok(node.props['aria-label'].includes(expected.zone_name));
    });
    assert.equal(points[1].props.children[0].props.cx, points[2].props.children[0].props.cx,
      'Two daily aggregates share a date coordinate, not invented before/after measurement times');
    view.select(serverRange);
    assert.deepEqual(plain(view.chart.temperatures), []);
    await view.settleTemperature(view.temperatureRequests.at(-1), []);
    assert.deepEqual(plain(view.chart.temperatures), [], 'June observations are outside a period ending June 3');
    const requestCount = view.temperatureRequests.length;
    view.select({ startDate: '2026-06-01', endDate: '2026-06-30' });
    assert.deepEqual(plain(view.chart.temperatures), temperatures, 'Cached occupancy points retain both historical zones');
    assert.equal(view.temperatureRequests.length, requestCount);
    assert.equal(view.requests.length, 1);
  } finally { view.unmount(); }
});

test('an empty detail temperature history replaces initial temperatures without fallback', async () => {
  const history = historyHarness(() => Promise.resolve({ ...detail, temperature_history: [] }));
  await history.controller.select(olderRange);
  assert.deepEqual(plain(history.state.temperatures), []);
  await history.controller.select(recentRange);
  assert.deepEqual(plain(history.state.temperatures), []);
});

test('complete overview payload shows the whole multi-year history with real zeros and no request', () => {
  const view = miniChartHarness(componentBox(componentMeasurements, true));
  try {
    assertComponentRange(view, fullComponentRange);
    assert.deepEqual(plain(view.chart.measurements), chartPoints(componentMeasurements));
    assert.ok(view.chart.measurements.some(point => point.polypCount === 0 && point.ephyraeCount === 0));
    assert.equal(view.requests.length, 0);
    assert.equal(view.find(node => node.props.role === 'status').length, 0);
  } finally { view.unmount(); }
});

test('complete overview payload of a box with only recent readings keeps at least the recent extent', () => {
  const recentOnly = { ...componentBox([{ ...componentMeasurements[0], measured_on: '2026-05-20' }], true),
    earliest_biological_measurement_on: '2026-05-20' };
  const view = miniChartHarness(recentOnly);
  try {
    assert.equal(view.controls.extentStart, serverRange.startDate);
    assert.equal(view.chart.startDate, serverRange.startDate);
    assert.equal(view.requests.length, 0);
  } finally { view.unmount(); }
});

test('current stock transitions do not replace historical counts, ephyrae or salinity', () => {
  const readings = [
    { ...componentMeasurements[0], measured_on: '2026-03-04', polyp_count: 0, ephyrae_count: 0, salinity_psu: null },
    { ...componentMeasurements[1], measured_on: '2026-06-02', polyp_count: 120, ephyrae_count: 4, salinity_psu: '35.00' },
  ];
  const box = componentBox(readings, true);
  const before = JSON.stringify(box.measurements);
  const view = miniChartHarness(box);
  try {
    for (const [count, kind, revision] of [
      [120, 'measurement', 1], [35, 'subculture', 2], [0, 'subculture', 3], [null, null, 4],
    ]) {
      view.updateBox({ ...box, current_polyp_state: {
        polyp_count: count, revision: `box:902:${revision}`,
        source: kind ? { kind, id: revision, timestamp: '2026-06-03T12:00:00Z' } : null,
      } });
      assert.deepEqual(plain(view.chart.measurements), chartPoints(readings));
      assertComponentRange(view, fullComponentRange);
      assert.equal(view.requests.length, 0);
    }
    const laterReading = { ...readings[1], measured_on: '2026-06-03', polyp_count: 62, ephyrae_count: 2, salinity_psu: null };
    view.updateBox({ ...componentBox([...readings, laterReading], true), current_polyp_state: {
      polyp_count: 62, revision: 'box:902:5',
      source: { kind: 'measurement', id: 5, timestamp: '2026-06-03T13:00:00Z', measured_on: '2026-06-03' },
    } });
    assert.deepEqual(plain(view.chart.measurements), chartPoints([...readings, laterReading]));
    assert.equal(JSON.stringify(box.measurements), before);
  } finally { view.unmount(); }
});

test('child current stock including zero does not invent overview history', () => {
  const box = { ...componentBox([]), earliest_biological_measurement_on: null };
  const view = miniChartHarness(box);
  try {
    for (const count of [18, 0, null]) {
      view.updateBox({ ...box, current_polyp_state: {
        polyp_count: count, revision: 'box:902:1',
        source: count === null ? null : { kind: 'subculture_initialization', id: 1, timestamp: '2026-06-03T12:00:00Z' },
      } });
      assert.deepEqual(plain(view.chart.measurements), [], 'Stock, including zero, does not invent biological readings');
      assert.equal(view.chart.labels.empty, 'overviewNoHistoryEver');
      assert.equal(view.temperatureStatus.loading, true, 'Temperature loading remains available without biology');
      assert.equal(view.requests.length, 0);
    }
  } finally { view.unmount(); }
});

test('overview freshness, weekly counters and sorting remain based on readings, not current stock', () => {
  const jsx = (type, props, key) => ({ type, props, key });
  const overview = loadModule('../src/components/OverviewView.tsx', {
    react: {
      useMemo: factory => factory(), useState: initial => [initial, () => {}],
      useRef: initial => ({ current: initial }), useEffect() {},
    },
    'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: Symbol('Fragment') },
    '../utils/overviewHistory': { createOverviewHistory, overviewDefaultRange, scopeOverviewTemperatures },
    '../hooks/useTemperatureHistory': { useTemperatureHistory() { return { points: [], loading: false, failed: false, retry() {} }; } },
    './TemperatureHistoryStatus': { default() {} },
    './BiologicalTrendChart': { default() {} }, './ChartWindowControls': { default() {} },
    './PageLoader': { default() {} }, './PolypbaseIcon': { default() {} },
  }).default;
  const today = new Date();
  const date = [today.getFullYear(), String(today.getMonth() + 1).padStart(2, '0'), String(today.getDate()).padStart(2, '0')].join('-');
  const point = { date, polyp_count: 0, ephyrae_count: 0, salinity_psu: null };
  const base = { ...componentBox([]), species_name: 'Aurelia aurita', strain_code: 'ALA', thermal_zone: { id: 1, name: 'Zone 1' } };
  const boxes = [
    { ...base, id: 1, global_code: 'ALA-1', measurements: [point] },
    { ...base, id: 2, global_code: 'ALA-2', measurements: [{ ...point, date: '2020-01-01', polyp_count: 120 }] },
    { ...base, id: 3, global_code: 'ALA-3', measurements: [] },
  ];
  let expected;
  for (const [count, kind] of [[35, 'subculture'], [0, 'subculture'], [null, null], [18, 'subculture_initialization']]) {
    const tree = overview({
      boxes: boxes.map(box => ({ ...box, current_polyp_state: {
        polyp_count: count, revision: `box:${box.id}:1`,
        source: kind ? { kind, id: 1, timestamp: `${date}T12:00:00Z` } : null,
      } })),
      isLoading: false, language: 'en', t: key => key,
      loadHistory() { assert.fail('Freshness must not load history'); }, onSelectBox() {}, onOpenZone() {},
    });
    const articles = jsxNodes(tree, node => node.type === 'article');
    assert.deepEqual(articles.map(node => Number(node.key)), [3, 2, 1]);
    assert.ok(articles[0].props.className.endsWith('is-due'));
    assert.ok(articles[1].props.className.endsWith('is-due'));
    assert.ok(articles[2].props.className.endsWith('is-ok'), 'A measured zero is still a fresh reading');
    const counters = jsxNodes(tree, node => node.type === 'button' && node.props['aria-label'])
      .map(node => node.props['aria-label']);
    assert.deepEqual(counters, ['overviewRecordedBoxes : 1 boxes', 'weeklyDueSoon : 0 boxes', 'weeklyDueNow : 2 boxes']);
    const snapshot = plain({
      order: articles.map(node => [node.key, node.props.className]), counters,
      ages: jsxNodes(tree, node => node.props.className === 'overview-reading-age').map(node => node.props),
      zones: jsxNodes(tree, node => node.props.className?.startsWith('overview-zone-progress-card')).map(node => node.props.children),
    });
    if (expected) assert.deepEqual(snapshot, expected, 'Current state alone must not change weekly/history UI');
    else expected = snapshot;
  }
});

test('actual mini-chart JSX with only older readings renders controls and an empty graph while the full detail loads', async () => {
  const olderPoints = componentMeasurements.filter(point => point.measured_on < serverRange.startDate);
  const view = miniChartHarness(componentBox([]));
  try {
    assertComponentRange(view, fullComponentRange);
    assert.deepEqual(plain(view.chart.measurements), []);
    assert.equal(view.chart.labels.empty, 'overviewNoHistory');
    assert.equal(view.find(node => node.props.className === 'overview-chart overview-chart-empty').length, 0,
      'Older biology must not be mistaken for no history ever');
    assert.equal(view.requests.length, 1);
    await view.settle(view.requests[0], {
      id: 902, biological_measurements: [...olderPoints].reverse(), locations: componentLocations,
    });
    assertComponentRange(view, fullComponentRange);
    assert.deepEqual(plain(view.chart.measurements), chartPoints(olderPoints));
    assert.ok(view.chart.measurements.length >= 100);
    assert.ok(view.chart.measurements.some(point => point.polypCount === 0 && point.ephyraeCount === 0));
  } finally { view.unmount(); }
});

test('old-only complete payload shows readings and an empty box retains the no-history label with temperature status', async () => {
  const olderPoints = componentMeasurements.filter(point => point.measured_on < serverRange.startDate);
  const old = miniChartHarness(componentBox(olderPoints, true));
  try {
    assertComponentRange(old, fullComponentRange);
    assert.deepEqual(plain(old.chart.measurements), chartPoints(olderPoints));
    assert.equal(old.requests.length, 0);
  } finally { old.unmount(); }
  const empty = miniChartHarness({ ...componentBox([], true), earliest_biological_measurement_on: null });
  try {
    assert.deepEqual(plain(empty.chart.measurements), []);
    assert.equal(empty.chart.labels.empty, 'overviewNoHistoryEver');
    assert.equal(empty.temperatureStatus.loading, true);
    assert.equal(empty.requests.length, 0);
    await empty.settleTemperature(empty.temperatureRequests[0], []);
    assert.equal(empty.temperatureStatus.loading, false);
    assert.equal(empty.temperatureStatus.failed, false);
    assert.deepEqual(plain(empty.temperatureStatus.points), []);
  } finally { empty.unmount(); }
});

test('cancelled older load failure is cleared so a new older selection retries with a fresh load', async () => {
  const requests = [deferred(), deferred()];
  let index = 0;
  const history = historyHarness(() => requests[index++].promise);
  const cancelled = history.controller.select(olderRange);
  await history.controller.select(loadedRange);
  const valid = history.state;
  const publications = history.publications.length;
  requests[0].reject(new Error('Synthetic cancelled load failure'));
  await cancelled;
  assert.equal(history.state, valid);
  assert.equal(history.publications.length, publications);
  assert.equal(history.state.failedRange, null);
  const next = history.controller.select(otherOlderRange);
  assert.equal(history.loads, 2, 'Do not reuse the cancelled rejected promise');
  assert.equal(history.state.loading, true);
  assert.deepEqual(plain(history.state.range), loadedRange);
  requests[1].resolve(detail);
  await next;
  assert.deepEqual(plain(history.state.range), otherOlderRange);
  assert.deepEqual(plain(history.state.measurements), expectedMeasurements);
  assert.equal(history.state.complete, true);
  assert.equal(history.state.loading, false);
  assert.equal(history.state.failedRange, null);
});

test('select after dispose publishes nothing and never starts a load, for loaded and older ranges', async () => {
  const history = historyHarness();
  history.controller.dispose();
  const state = history.state;
  for (const range of [loadedRange, olderRange, recentRange, otherOlderRange]) {
    await history.controller.select(range);
    assert.equal(history.state, state);
    assert.equal(history.publications.length, 0);
    assert.equal(history.loads, 0);
  }
});

function memoizedOverviewCallbackHarness() {
  const app = appHarness();
  let initializer;
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === 'loadOverviewHistory') {
      initializer = node.initializer;
    }
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(initializer && ts.isCallExpression(initializer), 'Expected actual useCallback initializer');
  assert.equal(initializer.expression.getText(ast), 'useCallback');
  assert.equal(initializer.arguments.length, 2, 'Execute the source callback and its source dependency array');
  let memo;
  app.context.useCallback = (callback, deps) => {
    if (!memo || memo.deps.length !== deps.length
      || !deps.every((value, index) => Object.is(value, memo.deps[index]))) {
      memo = { callback, deps: [...deps] };
    }
    return memo.callback;
  };
  return {
    app,
    render(organizationId) {
      app.context.activeOrganizationId = organizationId;
      // Capture the generation as a render-local binding, just as App does.
      return app.evaluate(`((activeOrganizationId, operationGeneration) => ${initializer.getText(ast)})(
        activeOrganizationId, organizationRequestGenerationRef.current
      )`);
    },
  };
}

test('actual useCallback dependencies refresh a remembered organization callback on startup generation change', async () => {
  const { app, render } = memoizedOverviewCallbackHarness();
  const rememberedOrganization = 77;
  const startup = render(rememberedOrganization);
  assert.equal(render(rememberedOrganization), startup, 'Same organization and generation must reuse the callback');
  assert.equal(app.context.organizationRequestGenerationRef.current, 0);
  app.context.organizationRequestGenerationRef.current = 1;
  const refreshed = render(rememberedOrganization);
  assert.notEqual(refreshed, startup, 'Same organization with a new generation must recreate the callback');
  assert.equal(render(rememberedOrganization), refreshed);
  await assert.rejects(startup(detail.id), error => error instanceof app.context.ApiResourceCancelledError);
  assert.equal(app.requests.length, 0, 'Startup callback must refuse GET after generation advances');
  const result = refreshed(detail.id);
  assert.equal(app.requests.length, 1);
  assert.equal(app.requests[0].method, 'apiGet');
  assert.equal(app.requests[0].url, '/api/boxes/901/');
  assert.equal(app.requests[0].generation, 1);
  app.requests[0].resolve(detail);
  assert.equal(await result, detail);
});

function scrubberControlsHarness(initialProps) {
  const scrubber = loadModule('../src/utils/chartScrubber.ts', { './chartWindow': chartWindow });
  const source = readFileSync(new URL('../src/components/ChartWindowControls.tsx', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { ...compilerOptions, jsx: ts.JsxEmit.ReactJSX },
  });
  const refs = [];
  let cursor = 0;
  let tree;
  let props = initialProps;
  const captures = new Set();
  const range = scrubber.buildScrubberRange(props.extentStart, props.extentEnd, props.startDate, props.endDate);
  // One pixel per calendar day makes pointer deltas deterministic; production math is unchanged.
  const dom = {
    getBoundingClientRect: () => ({ left: 0, width: range.extentEnd - range.extentStart }),
    setPointerCapture: id => captures.add(id),
    hasPointerCapture: id => captures.has(id),
    releasePointerCapture: id => captures.delete(id),
  };
  const text = {
    chartDisplayedPeriod: 'Displayed period', chartFullHistory: 'Full history', chartPeriodTo: 'to',
    chartMovePeriod: 'Move period', chartStartPeriod: 'Start period', chartEndPeriod: 'End period',
    chartTotalPeriod: 'Total period',
  };
  const jsx = (type, props, key) => ({ type, props, key });
  const dependencies = {
    react: { useRef(initial) { const index = cursor++; return refs[index] ??= { current: initial }; } },
    'react/jsx-runtime': { jsx, jsxs: jsx, Fragment: Symbol('Fragment') },
    '../i18n': { translations: { en: text, fr: text } },
    '../utils/chartScrubber': scrubber,
  };
  const exports = {};
  vm.runInNewContext(outputText, {
    exports, Date, Intl,
    require(name) {
      assert.ok(Object.hasOwn(dependencies, name), `Unexpected controls dependency: ${name}`);
      return dependencies[name];
    },
  }, { filename: 'ChartWindowControls.tsx' });
  function find(className) {
    function visit(node) {
      if (Array.isArray(node)) return node.flatMap(visit);
      if (!node || typeof node !== 'object' || !node.props) return [];
      return [...(node.props.className === className ? [node] : []), ...visit(node.props.children)];
    }
    const nodes = visit(tree);
    assert.equal(nodes.length, 1, `Expected one actual JSX node: ${className}`);
    return nodes[0];
  }
  function render(nextProps = props) {
    props = nextProps;
    cursor = 0;
    tree = exports.default(props);
    find('chart-scrubber-track').props.ref.current = dom;
  }
  function pointer(handler, clientX) {
    const node = find('chart-scrubber-thumb is-start');
    node.props[handler]({
      pointerId: 1, pointerType: 'mouse', button: 0, isPrimary: true,
      clientX, currentTarget: dom, target: dom, stopPropagation() {},
    });
  }
  render();
  return { render, pointer, captures };
}

for (const outcome of ['success', 'failure']) {
  test(`actual controls narrowing to the loaded range cancels the initial full load before late ${outcome}`, async () => {
    const recent = componentMeasurements.filter(point => point.measured_on >= serverRange.startDate);
    const view = miniChartHarness(componentBox(recent));
    try {
      assert.equal(view.controls.notifyUnchanged, true, 'Overview must opt into unchanged notifications');
      assert.equal(view.requests.length, 1);
      assert.equal(view.find(node => node.props.role === 'status').length, 1);
      const controls = scrubberControlsHarness(view.controls);
      // One pixel per day: 743 days moves the start thumb from the earliest reading to the loaded start.
      controls.pointer('onPointerDown', 0);
      controls.pointer('onPointerMove', 743);
      view.render();
      controls.render(view.controls);
      assert.equal(view.selections.length, 2, 'Mount selection plus one drag selection');
      assert.equal(view.selections[1].startDate, serverRange.startDate);
      assert.equal(view.selections[1].endDate, serverRange.endDate);
      assertComponentRange(view, serverRange);
      assert.equal(view.find(node => node.props.role === 'status').length, 0);
      controls.pointer('onPointerUp', 743);
      assert.equal(controls.captures.size, 0);
      if (outcome === 'success') {
        await view.settle(view.requests[0], {
          id: 902, biological_measurements: [...componentMeasurements].reverse(), locations: componentLocations,
        });
      } else {
        view.requests[0].reject(new Error('Synthetic cancelled scrubber load failure'));
        await new Promise(resolve => setImmediate(resolve));
        view.render();
      }
      assertComponentRange(view, serverRange);
      assert.deepEqual(plain(view.chart.measurements), chartPoints(recent));
      assert.equal(view.find(node => node.props.role === 'status' || node.props.role === 'alert').length, 0);
      assert.equal(view.selections.length, 2);
    } finally { view.unmount(); }
  });
}

test('actual controls default notifyUnchanged=false suppresses unchanged pointer emissions', () => {
  const emitted = [];
  const controls = scrubberControlsHarness({
    language: 'en', extentStart: '2024-02-19', extentEnd: serverRange.endDate,
    ...serverRange, onChange: (startDate, endDate) => emitted.push({ startDate, endDate }),
  });
  controls.pointer('onPointerDown', 100);
  controls.pointer('onPointerMove', 100);
  controls.pointer('onPointerMove', 100);
  assert.equal(emitted.length, 0, 'Default callers do not receive unchanged range events');
  controls.pointer('onPointerMove', 0);
  assert.equal(emitted.length, 1, 'Changed range still emits');
  assert.ok(emitted[0].startDate < serverRange.startDate);
  controls.pointer('onPointerMove', 100);
  assert.equal(emitted.length, 1, 'Returning to committed dates remains suppressed without opt-in');
  controls.pointer('onPointerUp', 100);
  assert.equal(controls.captures.size, 0);
});

const continuityLocations = [
  { id: 31, thermal_zone: { id: 31, name: 'Synthetic previous zone' },
    starts_at: '2024-02-01T12:00:00Z', ends_at: null, end_date_unknown: true, notes: '' },
  { id: 33, thermal_zone: { id: 33, name: 'Synthetic current zone' },
    starts_at: '2026-08-14T12:00:00Z', ends_at: null, end_date_unknown: false, notes: '' },
];
const continuityMeasurements = [
  ['2024-02-19', 8, 3], ['2026-07-02', 90, 40],
  ['2026-07-03', 0, 0], ['2026-07-10', 17, 0], ['2026-08-13', 0, 9],
  ['2026-08-14', 31, 12], ['2026-09-10', 110, 70], ['2026-10-03', 4, 0],
].map(([date, polyps, ephyrae], index) => ({
  ...measurements[index], measured_on: date, polyp_count: polyps, ephyrae_count: ephyrae,
  created_at: `${date}T12:00:00Z`,
}));
const continuityRecent = continuityMeasurements.filter(point => point.measured_on >= recentRange.startDate);
const continuityFullRange = { startDate: '2024-02-19', endDate: recentRange.endDate };
function continuityBox(locationHistory = continuityLocations) {
  return {
    id: 903, history_start_date: recentRange.startDate, history_end_date: recentRange.endDate,
    earliest_biological_measurement_on: continuityFullRange.startDate,
    measurements: plain(normalizeOverviewMeasurements(continuityRecent)), locations: locationHistory,
  };
}
function continuityDetail(locationHistory = continuityLocations) {
  return { id: 903, biological_measurements: [...continuityMeasurements].reverse(), locations: locationHistory };
}
const continuityText = key => key === 'movementEvent' ? 'Transfert' : key;
function chartGeometry(view) {
  const props = view.chart;
  return trendChart.buildGeometry(props.measurements, props.locations, props.events ?? [],
    props.startDate, props.endDate, props.compact);
}
function graphSnapshot(view) {
  const geometry = chartGeometry(view);
  return plain({
    start: geometry.start, end: geometry.end,
    bands: geometry.locationBands, events: geometry.eventPoints,
    measurements: geometry.plottedMeasurements, segments: geometry.measurementSegments,
    hitAreas: geometry.hitAreas,
    paths: geometry.measurementSegments.map(segment => ({
      polyps: geometry.countLine(point => point.polypCount)(segment),
      ephyrae: geometry.countLine(point => point.ephyraeCount)(segment),
    })),
  });
}
function jsxNodes(tree, predicate) {
  if (Array.isArray(tree)) return tree.flatMap(node => jsxNodes(node, predicate));
  if (!tree || typeof tree !== 'object' || !tree.props) return [];
  return [...(predicate(tree) ? [tree] : []), ...jsxNodes(tree.props.children, predicate)];
}
function assertDetailGet(app, count) {
  assert.equal(app.requests.length, count);
  for (const request of app.requests) {
    assert.equal(request.method, 'apiGet');
    assert.equal(request.url, '/api/boxes/903/');
  }
}
function assertRecentBiology(view) {
  const geometry = chartGeometry(view);
  assert.deepEqual(plain(geometry.plottedMeasurements), chartPoints(continuityRecent));
  assert.equal(geometry.plottedMeasurements.length, 6, 'Every recent reading, including both boundary days, is eligible');
  assert.deepEqual(plain(geometry.measurementSegments.flat()), chartPoints(continuityRecent));
  const zero = geometry.plottedMeasurements[0];
  assert.equal(zero.polypCount, 0);
  assert.equal(zero.ephyraeCount, 0);
  assert.equal(geometry.yCount(zero.polypCount), geometry.countHeight - geometry.padding.bottom);
  assert.equal(geometry.yCount(zero.ephyraeCount), geometry.countHeight - geometry.padding.bottom);
  for (const segment of geometry.measurementSegments) {
    for (const field of ['polypCount', 'ephyraeCount']) {
      const path = geometry.countLine(point => point[field])(segment);
      assert.ok(path.startsWith('M'), 'Real D3 must produce a path for each biological series');
      assert.doesNotMatch(path, /NaN|Infinity/);
    }
  }
}

test('actual overview geometry has complete previous/current context before GET and is identical after full-history round trip', async () => {
  const app = appHarness();
  const view = miniChartHarness(continuityBox(), overviewCallback(app), continuityText);
  view.select(recentRange); // narrow to the loaded period; the full load stays pending
  try {
    const first = graphSnapshot(view);
    assert.deepEqual({ startDate: first.start, endDate: first.end }, recentRange);
    assert.deepEqual(first.bands.map(({ id, name, startDate, endDate }) => ({ id, name, startDate, endDate })), [
      { id: 31, name: 'Synthetic previous zone', startDate: '2026-07-03', endDate: '2026-08-14' },
      { id: 33, name: 'Synthetic current zone', startDate: '2026-08-14', endDate: '2026-10-03' },
    ]);
    assert.equal(first.events.length, 1);
    assert.deepEqual(first.events[0].event, {
      id: 'zone-transition-31-33', date: '2026-08-14', instant: '2026-08-14T12:00:00Z',
      detail: 'Synthetic previous zone -> Synthetic current zone', kind: 'location_change', title: '',
    });
    assert.equal(first.events[0].x, chartGeometry(view).xPosition('2026-08-14'));
    const rendered = trendChart.default(view.chart);
    const transfers = jsxNodes(rendered, node => node.props.className === 'bio-trend-event-label');
    assert.equal(transfers.length, 0, 'period attribution is retained without noisy derived markers');
    assertRecentBiology(view);
    assertDetailGet(app, 1);
    const initialLocations = plain(view.chart.locations);
    const initialMeasurements = plain(view.chart.measurements);
    const withinLoaded = { startDate: '2026-08-01', endDate: '2026-09-01' };
    const slider = scrubberControlsHarness(view.controls);
    slider.pointer('onPointerDown', 100);
    slider.pointer('onPointerMove', 120);
    slider.pointer('onPointerUp', 120);
    view.render();
    assert.equal(view.chart.startDate, '2026-07-23');
    assertDetailGet(app, 1);
    view.select(withinLoaded);
    assert.deepEqual(plain(view.chart.locations), initialLocations, 'Loaded-window slider never enriches location context');
    assert.deepEqual(plain(view.chart.measurements), initialMeasurements);
    assert.equal(chartGeometry(view).eventPoints[0].event.date, '2026-08-14');
    assertDetailGet(app, 1);
    view.select(recentRange);
    assert.deepEqual(graphSnapshot(view), first);
    view.select(continuityFullRange);
    assertDetailGet(app, 1);
    assert.deepEqual(graphSnapshot(view), first, 'Pending extension keeps the committed graph');
    await view.settle(app.requests[0], continuityDetail());
    assert.equal(view.chart.startDate, continuityFullRange.startDate);
    assert.deepEqual(plain(chartGeometry(view).plottedMeasurements), chartPoints(continuityMeasurements));
    assert.deepEqual(plain(view.chart.locations), initialLocations, 'Detail returns the same complete location history');
    view.select(recentRange);
    assertDetailGet(app, 1);
    assertRecentBiology(view);
    assert.deepEqual(graphSnapshot(view), first, 'Bands, generated events, eligible readings and D3 paths are unchanged');
  } finally { view.unmount(); }
});

test('actual overview geometry resolves unknown end through a known ended predecessor without extending across a gap', async () => {
  const chain = [continuityLocations[0], {
    id: 32, thermal_zone: { id: 32, name: 'Synthetic ended zone' },
    starts_at: '2025-01-01T12:00:00Z', ends_at: '2026-06-01T12:00:00Z',
    end_date_unknown: false, notes: '',
  }, continuityLocations[1]];
  const app = appHarness();
  const view = miniChartHarness(continuityBox(chain), overviewCallback(app), continuityText);
  view.select(recentRange); // narrow to the loaded period; the full load stays pending
  try {
    const first = graphSnapshot(view);
    assert.deepEqual(first.bands.map(({ id, startDate, endDate }) => ({ id, startDate, endDate })), [
      { id: 33, startDate: '2026-08-14', endDate: '2026-10-03' },
    ]);
    assert.deepEqual(first.events, [], 'No invented transfer from the unknown period across the known end and gap');
    assert.equal(jsxNodes(trendChart.default(view.chart), node => node.props.className === 'bio-trend-event-label').length, 0);
    assertRecentBiology(view);
    assertDetailGet(app, 1);
    view.select(continuityFullRange);
    assertDetailGet(app, 1);
    await view.settle(app.requests[0], continuityDetail(chain));
    const full = graphSnapshot(view);
    assert.deepEqual(full.bands.map(({ id, endDate }) => ({ id, endDate })), [
      { id: 31, endDate: '2025-01-01' }, { id: 32, endDate: '2026-06-01' }, { id: 33, endDate: '2026-10-03' },
    ]);
    assert.deepEqual(full.events.map(({ event }) => event.date), ['2025-01-01'], 'Only the contiguous old transition is generated');
    view.select(recentRange);
    assertDetailGet(app, 1);
    assert.deepEqual(graphSnapshot(view), first);
  } finally { view.unmount(); }
});

test('actual history loading is an sr-only status; rejection renders error and retry GET without changing committed geometry', async () => {
  const app = appHarness();
  const view = miniChartHarness(continuityBox(), overviewCallback(app), continuityText);
  view.select(recentRange); // narrow to the loaded period; the full load stays pending
  const assertLoading = () => {
    const statuses = view.find(node => node.props.role === 'status');
    assert.equal(statuses.length, 1);
    assert.equal(statuses[0].type, 'p');
    assert.equal(statuses[0].props.className, 'sr-only');
    assert.equal(statuses[0].props.children, 'overviewHistoryLoading');
    assert.equal(statuses[0].props['aria-hidden'], undefined);
    assert.equal(view.find(node => node.props.role === 'alert').length, 0);
  };
  try {
    const first = graphSnapshot(view);
    assert.equal(view.find(node => node.props.role === 'status' || node.props.role === 'alert').length, 0);
    view.select(continuityFullRange);
    assertDetailGet(app, 1);
    assertLoading();
    assert.deepEqual(graphSnapshot(view), first);
    app.requests[0].reject(new Error('Synthetic detail GET failure'));
    await new Promise(resolve => setImmediate(resolve));
    view.render();
    assert.equal(view.find(node => node.props.role === 'status').length, 0);
    const alerts = view.find(node => node.props.role === 'alert');
    assert.equal(alerts.length, 1);
    const errors = jsxNodes(alerts[0], node => node.type === 'p');
    assert.equal(errors.length, 1);
    assert.equal(errors[0].props.children, 'overviewHistoryFailed');
    const retries = jsxNodes(alerts[0], node => node.type === 'button');
    assert.equal(retries.length, 1);
    assert.equal(retries[0].props.children, 'overviewHistoryRetry');
    assert.equal(retries[0].props.type, 'button');
    assert.notEqual(retries[0].props.disabled, true);
    assert.notEqual(retries[0].props.className, 'sr-only');
    assert.deepEqual(graphSnapshot(view), first);
    retries[0].props.onClick();
    view.render();
    assertDetailGet(app, 2);
    assertLoading();
    assert.deepEqual(graphSnapshot(view), first, 'Retry also leaves the committed graph untouched while pending');
    await view.settle(app.requests[1], continuityDetail());
    assert.equal(view.find(node => node.props.role === 'status' || node.props.role === 'alert').length, 0);
    assert.equal(view.chart.startDate, continuityFullRange.startDate, 'Successful retry commits the requested older window');
    assert.deepEqual(plain(chartGeometry(view).plottedMeasurements), chartPoints(continuityMeasurements));
    view.select(recentRange);
    assertDetailGet(app, 2);
    assert.deepEqual(graphSnapshot(view), first);
  } finally { view.unmount(); }
});

test('shared primitives sr-only contract is global and remains accessible at every responsive breakpoint', () => {
  const indexUrl = new URL('../src/styles/index.css', import.meta.url);
  const indexCss = readFileSync(indexUrl, 'utf8');
  assert.match(indexCss, /@import '\.\/components\/primitives\.css' layer\(components\)/);
  const occurrences = [];
  for (const match of indexCss.matchAll(/@import\s+['"]([^'"]+)['"]/g)) {
    const css = readFileSync(new URL(match[1], indexUrl), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    for (const rule of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      if (!/\.sr-only\b/.test(rule[1])) continue;
      occurrences.push(match[1]);
      assert.equal(rule[1].trim(), '.sr-only');
      const prefix = css.slice(0, rule.index);
      assert.equal((prefix.match(/\{/g) ?? []).length, (prefix.match(/\}/g) ?? []).length,
        'sr-only must be unconditional, not inside a viewport or pointer media query');
      const declarations = Object.fromEntries(rule[2].trim().split(';').filter(value => value.trim()).map(value => {
        const colon = value.indexOf(':');
        return [value.slice(0, colon).trim(), value.slice(colon + 1).trim()];
      }));
      for (const [property, value] of Object.entries({
        position: 'absolute', width: '1px', height: '1px', padding: '0', overflow: 'hidden',
        clip: 'rect(0, 0, 0, 0)', 'white-space': 'nowrap', border: '0',
      })) assert.equal(declarations[property], `${value} !important`, property);
      assert.equal(declarations.display, undefined, 'Do not remove the live region from the accessibility tree');
      assert.equal(declarations.visibility, undefined);
    }
  }
  assert.deepEqual(occurrences, ['./components/primitives.css'], 'No page, phone, tablet or print override of sr-only');
});
