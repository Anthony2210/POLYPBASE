import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { resourceClient } from './resource-test-harness.mjs';

function load(path, dependencies, suffix = '') {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8') + suffix, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams, Date, setTimeout, clearTimeout,
    require(name) { assert.ok(name in dependencies, name); return dependencies[name]; } });
  return exports;
}
const plain = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const range = { startDate: '2020-01-01', endDate: '2020-12-31' };
const point = (date, zone_id = 1, average_temperature_c = 0) => ({ date, zone_id, average_temperature_c,
  zone_name: `Synthetic ${zone_id}`, min_temperature_c: null, max_temperature_c: null });
const response = (results, count = results.length, next = null) => ({ ok: true,
  headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ({ results, count, next, previous: null }) });
function setup(fetch) {
  const client = resourceClient({ fetch, document: { cookie: '' } });
  const utility = load('../src/utils/temperatureHistory.ts', { '../api/client': client });
  return { client, ...utility };
}

test('successful manual writes refresh mounted coverage, cancel old GETs and retain zero without replaying writes', async () => {
  const requests = [], states = [];
  const utility = setup((url, options) => {
    const request = { ...deferred(), url, options };
    requests.push(request); return request.promise;
  });
  const controller = utility.createTemperatureHistory(17, state => states.push(state));
  const initial = controller.select(range);
  requests[0].resolve(response([])); await initial;
  const write = utility.client.apiPost('/api/thermal-zones/1/temperature/', { temperature_c: 0 });
  assert.equal(requests.length, 2, 'coverage is not discarded before write success');
  requests[1].resolve(response([])); await write;
  assert.equal(requests.length, 3, 'successful empty coverage must be re-read');
  assert.equal(requests[2].options.cache, 'no-store');
  const secondWrite = utility.client.apiPost('/api/thermal-zones/1/temperature/', { temperature_c: 0 });
  requests[3].resolve(response([])); await secondWrite;
  assert.equal(requests[2].options.signal.aborted, true, 'pre-mutation read is cancelled');
  requests[4].resolve(response([point('2020-02-29', 1, 0)])); await tick();
  requests[2].resolve(response([point('2020-02-29', 1, 12)])); await tick();
  assert.deepEqual(plain(states.at(-1)), { points: [point('2020-02-29', 1, 0)], loading: false, failed: false });
  await controller.select(range);
  assert.equal(requests.length, 5, 'new coverage is cached');
  controller.dispose();
  const afterUnmount = utility.client.apiPost('/api/thermal-zones/1/temperature/', {});
  requests[5].resolve(response([])); await afterUnmount;
  assert.equal(requests.length, 6, 'disposed charts do not refetch');
  assert.equal(requests.filter(request => request.options.method === 'POST').length, 3, 'no write replay');
});

test('rejected and old-institution manual writes do not invalidate current chart coverage', async () => {
  const requests = [], states = [];
  const utility = setup((url, options) => {
    const request = { ...deferred(), url, options };
    requests.push(request); return request.promise;
  });
  const oldWrite = utility.client.apiPost('/api/thermal-zones/1/temperature/', {});
  utility.client.setActiveOrganizationContext(2);
  const controller = utility.createTemperatureHistory(17, state => states.push(state));
  const initial = controller.select(range);
  requests[1].resolve(response([point('2020-02-29')])); await initial;
  requests[0].resolve(response([])); await oldWrite;
  assert.equal(requests.length, 2, 'late institution response must not invalidate another institution');
  const rejected = utility.client.apiPost('/api/thermal-zones/1/temperature/', {});
  requests[2].reject(new Error('rejected'));
  await assert.rejects(rejected);
  assert.equal(requests.length, 3);
  assert.equal(states.at(-1).points[0].average_temperature_c, 0);
  controller.dispose();
});

test('inclusive chunks cover arbitrary historical windows, leap day and exact 366-day boundaries', () => {
  const { temperatureRangeChunks: chunks } = setup(() => {});
  assert.deepEqual(plain(chunks(range)), [range]);
  assert.deepEqual(plain(chunks({ ...range, endDate: '2021-01-01' })), [range, { startDate: '2021-01-01', endDate: '2021-01-01' }]);
  assert.deepEqual(plain(chunks({ startDate: '2020-02-29', endDate: '2020-02-29' })), [{ startDate: '2020-02-29', endDate: '2020-02-29' }]);
  const all = chunks({ startDate: '2019-01-01', endDate: '2024-03-01' });
  for (let i = 0; i < all.length; i++) {
    assert.ok((Date.parse(all[i].endDate) - Date.parse(all[i].startDate)) / 86400000 < 366);
    if (i) assert.equal(Date.parse(all[i].startDate) - Date.parse(all[i - 1].endDate), 86400000);
  }
  for (const invalid of [{ startDate: '2021-02-29', endDate: '2021-03-01' }, { startDate: '2020-03-01', endDate: '2020-02-29' }]) assert.throws(() => chunks(invalid));
});

test('loads every page and empty chunk beyond the old 180-day cutoff, preserving same-day zones, zero and null', async () => {
  const requests = [];
  const first = Array.from({ length: 500 }, (_, i) => point('2020-02-29', i + 1));
  const pages = [response(first, 501, 'https://ignored.test/?offset=500'), response([point('2020-12-31', 501)], 501), response([]), response([point('2022-01-02', 2, 18)])];
  const utility = setup(async (url, options) => { requests.push({ url, options }); return pages.shift(); });
  const points = await utility.loadTemperatureHistory(17, { startDate: '2020-01-01', endDate: '2022-01-02' }, new AbortController().signal);
  assert.equal(points.length, 502);
  assert.deepEqual(plain(points.slice(0, 500)), first);
  assert.equal(points[500].date, '2020-12-31');
  assert.equal(points[501].date, '2022-01-02');
  assert.deepEqual(requests.map(({ url }) => Object.fromEntries(new URL(url, 'https://test').searchParams)), [
    { start: '2020-01-01', end: '2020-12-31', limit: '500', offset: '0' },
    { start: '2020-01-01', end: '2020-12-31', limit: '500', offset: '500' },
    { start: '2021-01-01', end: '2022-01-01', limit: '500', offset: '0' },
    { start: '2022-01-02', end: '2022-01-02', limit: '500', offset: '0' },
  ]);
  requests.forEach(({ url, options }) => {
    assert.ok(url.startsWith('/api/boxes/17/temperatures/?'));
    assert.ok(options.signal); assert.equal(options.cache, 'no-store');
    assert.equal(options.headers.get('X-Organization-Id'), '1');
  });
});

test('overlapping navigation cancels explicitly and stale success/error cannot publish, including A-B-A range navigation', async () => {
  const { createTemperatureHistory } = setup(() => {});
  const requests = [], publications = [];
  const controller = createTemperatureHistory(17, state => publications.push(state), new AbortController().signal,
    (id, selected, signal) => { const request = { ...deferred(), id, selected, signal }; requests.push(request); return request.promise; });
  const a = controller.select(range);
  const b = controller.select({ startDate: '2024-01-01', endDate: '2024-04-01' });
  const latest = controller.select(range);
  assert.equal(requests[0].signal.aborted, true);
  assert.equal(requests[1].signal.aborted, true);
  requests[2].resolve([point('2020-02-29')]); await latest;
  const count = publications.length;
  requests[0].resolve([point('2020-03-01')]); requests[1].reject(new Error('late')); await Promise.all([a, b]);
  assert.equal(publications.length, count);
  assert.deepEqual(plain(publications.at(-1)), { points: [point('2020-02-29')], loading: false, failed: false });
  controller.dispose();
});

test('partial pagination failure publishes no partial curve; retry starts fresh and disposal blocks publication', async () => {
  const requests = [];
  const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
  const publications = [];
  const controller = utility.createTemperatureHistory(17, state => publications.push(state));
  const failed = controller.select(range);
  requests[0].resolve(response([point('2020-01-01')], 501, '?offset=500')); await tick();
  requests[1].reject(new Error('offline')); await failed;
  assert.deepEqual(plain(publications.at(-1)), { points: [], loading: false, failed: true });
  const retry = controller.select(range);
  assert.equal(new URL(requests[2].url, 'https://test').searchParams.get('offset'), '0');
  requests[2].resolve(response([])); await retry;
  assert.deepEqual(plain(publications.at(-1)), { points: [], loading: false, failed: false });
  const disposed = controller.select({ startDate: '2021-01-01', endDate: '2021-01-02' }); controller.dispose();
  const count = publications.length;
  assert.equal(requests[3].options.signal.aborted, true);
  requests[3].resolve(response([point('2020-01-01')])); await disposed;
  assert.equal(publications.length, count);
});

const settle = () => new Promise(resolve => setTimeout(resolve, 15));
const networkFailure = () => new TypeError('Failed to fetch');
const httpFailure = status => Object.assign(new Error('http'), { status });

test('transient failure recovers automatically, keeps valid chunks and never shows a failure', async () => {
  const requests = [];
  const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
  const states = [];
  const controller = utility.createTemperatureHistory(17, state => states.push(plain(state)), undefined, undefined, [0, 0, 0]);
  const wide = { startDate: '2020-01-01', endDate: '2021-12-31' };
  const done = controller.select(wide);
  requests[0].resolve(response([point('2020-02-29')])); await tick();
  requests[1].reject(networkFailure()); await settle();
  assert.equal(requests.length, 3, 'only the missing chunk is requested again');
  assert.match(requests[2].url, /start=2021-01-01/);
  assert.equal(states.some(state => state.failed), false);
  assert.deepEqual(states.at(-1).points.map(item => item.date), ['2020-02-29'], 'valid data stays during the retry');
  assert.equal(states.at(-1).loading, true);
  requests[2].resolve(response([point('2021-03-01', 1, 0)])); await done;
  assert.deepEqual(states.at(-1), { points: [point('2020-02-29'), point('2021-03-01')], loading: false, failed: false });
  assert.equal(states.at(-1).points[1].average_temperature_c, 0);
});

test('automatic retries are bounded and end quietly in the existing failed state', async () => {
  const requests = [];
  const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
  const states = [];
  const controller = utility.createTemperatureHistory(17, state => states.push(plain(state)), undefined, undefined, [0, 0, 0]);
  const done = controller.select(range);
  for (let index = 0; index < 4; index++) { await settle(); requests[index].reject(index % 2 ? httpFailure(503) : networkFailure()); }
  await done;
  assert.equal(requests.length, 4, 'one attempt plus three retries');
  assert.deepEqual(states.at(-1), { points: [], loading: false, failed: true });
  await new Promise(resolve => setTimeout(resolve, 20));
  assert.equal(requests.length, 4, 'no further retry after the limit');
});

test('persistent, authorization and validation failures are not retried', async () => {
  for (const error of [httpFailure(401), httpFailure(403), httpFailure(404), httpFailure(400), new Error('Invalid temperature pagination.')]) {
    const requests = [];
    const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
    const states = [];
    const controller = utility.createTemperatureHistory(17, state => states.push(plain(state)), undefined, undefined, [0, 0, 0]);
    const done = controller.select(range);
    requests[0].reject(error); await done;
    assert.equal(requests.length, 1);
    assert.deepEqual(states.at(-1), { points: [], loading: false, failed: true });
  }
});

test('cancelled, replaced, invalidated and disposed retries never request or publish', async () => {
  for (const stop of ['cancel', 'select', 'dispose', 'invalidate', 'context']) {
    const requests = [];
    const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
    const states = [];
    const controller = utility.createTemperatureHistory(17, state => states.push(plain(state)), undefined, undefined, [30]);
    const done = controller.select(range);
    requests[0].reject(networkFailure()); await tick();
    const count = states.length;
    if (stop === 'cancel') controller.cancel();
    if (stop === 'dispose') controller.dispose();
    if (stop === 'select') void controller.select({ startDate: '2024-01-01', endDate: '2024-02-01' });
    if (stop === 'invalidate') { void utility.client.apiPost('/api/thermal-zones/1/temperature/', {}); requests[1].resolve(response({})); await settle(); }
    if (stop === 'context') { utility.client.setActiveOrganizationContext(2); }
    await done; await new Promise(resolve => setTimeout(resolve, 60));
    const oldRange = requests.filter(request => request.url.includes('start=2020-01-01'));
    if (stop === 'invalidate') assert.equal(oldRange.length, 2, 'invalidation starts one fresh read, not the waiting retry');
    else assert.equal(oldRange.length, 1, stop + ': the waiting retry did not fire');
    if (stop === 'cancel' || stop === 'dispose' || stop === 'context') assert.equal(states.length, stop === 'context' ? count + 1 : count);
    controller.dispose();
  }
});

test('organization A-B-A aborts body reads, bypasses shared GETs and never continues old pagination', async () => {
  const requests = [];
  const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
  const oldStates = [], newStates = [];
  const old = utility.createTemperatureHistory(17, state => oldStates.push(state));
  const pending = old.select(range);
  const body = deferred();
  requests[0].resolve({ ...response([]), json: () => body.promise }); await tick();
  utility.client.setActiveOrganizationContext(2);
  utility.client.setActiveOrganizationContext(1);
  assert.equal(requests[0].options.signal.aborted, true);
  const current = utility.createTemperatureHistory(17, state => newStates.push(state));
  const fresh = current.select(range);
  assert.equal(requests.length, 2, 'same organization and URL must not share an old GET');
  requests[1].resolve(response([point('2020-02-29')])); await fresh;
  body.resolve({ results: [point('2020-01-01')], count: 501, next: '?offset=500' }); await pending;
  assert.equal(requests.length, 2);
  assert.deepEqual(plain(oldStates.at(-1)), { points: [], loading: false, failed: false });
  assert.deepEqual(plain(newStates.at(-1).points), [point('2020-02-29')]);
  old.dispose(); current.dispose();
});

function hookHarness() {
  const requests = [], slots = [], cleanup = [];
  const effects = new Map(), committedEffects = new Map();
  const flushEffects = () => {
    const pending = [...effects]; effects.clear();
    for (const [i, fn] of pending) { cleanup[i]?.(); committedEffects.set(i, fn); cleanup[i] = fn(); }
  };
  let cursor = 0;
  const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
  const hooks = {
    useMemo: factory => factory(),
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], value => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }]; },
    useRef(initial) { const i = cursor++; slots[i] ??= { current: initial }; return slots[i]; },
    useEffect(fn, deps) { const i = cursor++; if (!slots[i] || deps.some((dep, j) => dep !== slots[i][j])) {
      slots[i] = deps; effects.set(i, fn);
    } },
  };
  const { useTemperatureHistory } = load('../src/hooks/useTemperatureHistory.ts', { react: hooks,
    '../api/client': utility.client, '../utils/temperatureHistory': utility });
  return { requests, client: utility.client, hooks, useTemperatureHistory,
    renderWith(component, props) { cursor = 0; const tree = component(props); flushEffects(); return tree; },
    render(boxId, selected = range, flush = true, refreshSource) { cursor = 0; const state = useTemperatureHistory(boxId, selected.startDate, selected.endDate, refreshSource); if (flush) flushEffects(); return state; },
    replayEffects() { cleanup.forEach(fn => fn?.()); for (const [i, fn] of committedEffects) cleanup[i] = fn(); },
    dispose() { cleanup.forEach(fn => fn?.()); },
  };
}

test('shared hook hides old points before effects on date/box/context changes and retries selected range', async () => {
  const hook = hookHarness();
  hook.render(17); hook.requests[0].resolve(response([point('2020-02-29')])); await tick();
  assert.equal(hook.render(17).points.length, 1);
  const next = { startDate: '2024-01-01', endDate: '2024-04-01' };
  assert.equal(hook.render(17, next, false).points.length, 0);
  hook.render(17, next); hook.requests[1].reject(new Error('offline')); await tick();
  const failed = hook.render(17, next); assert.equal(failed.failed, true); failed.retry();
  assert.match(hook.requests[2].url, /start=2024-01-01&end=2024-04-01/);
  hook.client.setActiveOrganizationContext(2); hook.client.setActiveOrganizationContext(1);
  assert.equal(hook.render(17, next).points.length, 0);
  assert.equal(hook.requests[2].options.signal.aborted, true);
  assert.equal(hook.requests.length, 4);
  hook.requests[3].resolve(response([point('2024-02-01')])); await tick();
  assert.equal(hook.render(17, next).points.length, 1);
  assert.equal(hook.render(18, next).points.length, 0);
  hook.dispose();
});

test('new location projections reload the same range without joining an older read', async () => {
  const hook = hookHarness();
  const locations = [];
  hook.render(17, range, true, locations);
  hook.requests[0].resolve(response([point('2020-02-29')])); await tick();
  assert.equal(hook.render(17, range, true, locations).points.length, 1);
  const corrected = [{ id: 1 }];
  assert.equal(hook.render(17, range, false, corrected).points.length, 0);
  hook.render(17, range, true, corrected);
  assert.equal(hook.requests.length, 2);
  hook.dispose();
});

function trackingChartHarness() {
  const hook = hookHarness();
  const jsx = (type, props) => ({ type, props });
  const controls = function Controls() {};
  const trend = function Trend() {};
  const status = function Status() {};
  const chart = load('../src/components/BoxTrackingChart.tsx', {
    react: hook.hooks, 'react/jsx-runtime': { jsx, jsxs: jsx },
    '../hooks/useTemperatureHistory': { useTemperatureHistory: hook.useTemperatureHistory },
    '../utils/chartWindow': load('../src/utils/chartWindow.ts', {}),
    '../utils/biologicalTimeline': load('../src/utils/biologicalTimeline.ts', {}),
    '../i18n': { createTranslator: () => key => key },
    './BiologicalTrendChart': { default: trend, isChartEventInRange: () => true },
    './ChartWindowControls': { default: controls }, './TemperatureHistoryStatus': { default: status },
    './HistoricalTemperatureStatus': { default: () => null },
    '../hooks/useHistoricalTemperatures': { useHistoricalTemperatures: () => ({ points: [], loading: false, failed: false, retry() {} }) },
    '../utils/historicalTemperature': { weeklyTrendTemperatures: () => [] },
  }).default;
  function find(tree, type) {
    if (Array.isArray(tree)) return tree.flatMap(node => find(node, type));
    if (!tree?.props) return [];
    return [...(tree.type === type ? [tree.props] : []), ...find(tree.props.children, type), ...find(tree.props.temperatureStatus, type)];
  }
  return { hook, chart, controls, trend, status, find };
}

test('actual Box chart controls reuse covered ranges and preserve overlap and biology on failure/retry', async () => {
  const { hook, chart, controls, trend, status, find } = trackingChartHarness();
  const props = { boxId: 17, language: 'en', measurements: [
    { id: 2, measured_on: '2019-01-01', polyp_count: 0, ephyrae_count: 0, salinity_psu: null },
    { id: 1, measured_on: '2020-02-29', polyp_count: 0, ephyrae_count: 0, salinity_psu: null }],
    locations: [], events: [], labels: { polyps: 'Polyps', chartTitle: 'Measurements', chartEmpty: 'Empty' } };
  let tree = hook.renderWith(chart, props);
  assert.equal(find(tree, trend)[0].temperatureStatus.props.children[0].type, status);
  assert.equal(find(tree, status)[0].loading, true);
  assert.equal(find(tree, status)[0].language, 'en');
  const initial = find(tree, controls)[0];
  const initialQuery = new URL(hook.requests[0].url, 'https://test').searchParams;
  assert.equal(initialQuery.get('start'), initial.startDate);
  assert.equal(initialQuery.get('end'), initial.endDate);
  hook.requests[0].resolve(response([point('2020-02-29')])); await tick();
  tree = hook.renderWith(chart, props);
  assert.deepEqual(plain(find(tree, trend)[0].temperatures), [point('2020-02-29')]);
  find(tree, controls)[0].onChange('2020-02-29', '2020-03-01');
  tree = hook.renderWith(chart, props);
  assert.deepEqual(plain(find(tree, trend)[0].temperatures), [point('2020-02-29')]);
  assert.equal(hook.requests.length, 1, 'A covered subrange does not reload');
  find(tree, controls)[0].onChange('2019-01-01', initial.endDate);
  tree = hook.renderWith(chart, props);
  assert.deepEqual(plain(find(tree, trend)[0].temperatures), [point('2020-02-29')]);
  const query = new URL(hook.requests[1].url, 'https://test').searchParams;
  assert.equal(query.get('start'), '2019-01-01');
  assert.equal(Date.parse(query.get('end')) + 86400000, Date.parse(initial.startDate));
  hook.requests[1].reject(new Error('offline')); await tick();
  tree = hook.renderWith(chart, props);
  assert.equal(find(tree, status)[0].failed, true);
  assert.equal(find(tree, trend)[0].measurements[0].polypCount, 0);
  find(tree, status)[0].retry();
  hook.requests[2].resolve(response([])); await tick();
  tree = hook.renderWith(chart, props);
  assert.equal(find(tree, status)[0].failed, false);
  assert.deepEqual(plain(find(tree, trend)[0].temperatures), [point('2020-02-29')]);
  hook.dispose();
});

test('a mounted Box chart refreshes after a manual write with unchanged locations and window', async () => {
  const { hook, chart, controls, trend, status, find } = trackingChartHarness();
  const props = { boxId: 17, language: 'en', measurements: [
    { id: 1, measured_on: '2020-02-29', polyp_count: 0, ephyrae_count: 0, salinity_psu: null },
  ], locations: [], events: [], labels: { polyps: 'Polyps', chartTitle: 'Measurements', chartEmpty: 'Empty' } };
  let tree = hook.renderWith(chart, props);
  const window = find(tree, controls)[0];
  const scope = find(tree, trend)[0].selectionScope;
  hook.requests[0].resolve(response([])); await tick();
  tree = hook.renderWith(chart, props);
  assert.equal(find(tree, status)[0].loading, false);
  const write = hook.client.apiPost('/api/thermal-zones/1/temperature/', { temperature_c: 0 });
  hook.requests[1].resolve(response([])); await write;
  tree = hook.renderWith(chart, props);
  assert.equal(hook.requests.length, 3, 'same mounted chart re-reads successful empty coverage');
  assert.equal(find(tree, status)[0].loading, true);
  hook.requests[2].resolve(response([point('2020-02-29', 1, 0)])); await tick();
  tree = hook.renderWith(chart, props);
  assert.deepEqual(plain(find(tree, trend)[0].temperatures), [point('2020-02-29', 1, 0)]);
  assert.equal(find(tree, trend)[0].measurements[0].polypCount, 0);
  assert.equal(find(tree, trend)[0].selectionScope, scope, 'refresh does not reset legend/point selection');
  assert.equal(find(tree, controls)[0].startDate, window.startDate);
  assert.equal(find(tree, controls)[0].endDate, window.endDate);
  hook.dispose();
});

test('supplied-array charts make no coverage claim and Box IDs reset both window and point selection', () => {
  const { hook, chart, controls, trend, find } = trackingChartHarness();
  const supplied = [point('2020-02-29')];
  const props = { language: 'en', temperatures: supplied, measurements: [
    { id: 1, measured_on: '2019-01-01', polyp_count: 0, ephyrae_count: 0, salinity_psu: null },
    { id: 2, measured_on: '2020-02-29', polyp_count: 0, ephyrae_count: 0, salinity_psu: null },
  ], locations: [], events: [], labels: { polyps: 'Polyps', chartTitle: 'Measurements', chartEmpty: 'Empty' } };
  let tree = hook.renderWith(chart, props);
  assert.deepEqual(plain(find(tree, trend)[0].temperatures), supplied);
  assert.equal(find(tree, trend)[0].temperatureStatus, undefined);
  assert.equal(hook.requests.length, 0);
  tree = hook.renderWith(chart, { ...props, temperatures: [] });
  assert.equal(find(tree, trend)[0].temperatureStatus, undefined, 'No Box ID does not mean complete empty coverage');
  tree = hook.renderWith(chart, { ...props, boxId: 17 });
  const initial = find(tree, controls)[0];
  const scope = find(tree, trend)[0].selectionScope;
  assert.equal(find(tree, trend)[0].temperatures.length, 0, 'Native loading never backfills from supplied arrays');
  assert.equal(find(tree, trend)[0].temperatureStatus.props.children[0].props.loading, true);
  find(tree, controls)[0].onChange('2019-01-01', '2019-02-01');
  tree = hook.renderWith(chart, { ...props, boxId: 17 });
  assert.equal(find(tree, controls)[0].startDate, '2019-01-01');
  tree = hook.renderWith(chart, { ...props, boxId: 18 });
  assert.equal(find(tree, controls)[0].startDate, initial.startDate);
  assert.equal(find(tree, controls)[0].endDate, initial.endDate);
  assert.notEqual(find(tree, trend)[0].selectionScope, scope);
  assert.match(hook.requests.at(-1).url, /^\/api\/boxes\/18\/temperatures\//);
  hook.dispose();
});

test('the actual lazy preview passes BoxDetail.id through to native temperature loading', async () => {
  const { hook, chart } = trackingChartHarness();
  const jsx = (type, props) => ({ type, props });
  const preview = load('../src/components/BoxTrackingPreview.tsx', {
    react: { lazy: factory => factory, useMemo: factory => factory() },
    'react/jsx-runtime': { jsx, jsxs: jsx }, 'react-dom': {}, 'lucide-react': {},
    '../api/client': {}, '../hooks/useAnchoredPopover': {}, '../utils/errors': {},
    '../utils/biologicalTimeline': { getBiologicalTimelineLabels: () => ({}) }, './SkeletonRows': {},
    './BoxTrackingChart': { default: chart, buildLifecycleEvents: () => [] },
  }, '\nexport { TrackingChart };');
  const { default: PreviewChart } = await preview.TrackingChart();
  const detail = { id: 42, locations: [], biological_measurements: [], movements: [], lineage: { parents: [], children: [] } };
  const element = PreviewChart({ detail, language: 'en', t: key => key });
  assert.equal(element.type, chart);
  assert.equal(element.props.boxId, detail.id);
  assert.equal(element.props.temperatures, undefined);
  hook.renderWith(element.type, element.props);
  assert.match(hook.requests[0].url, /^\/api\/boxes\/42\/temperatures\//);
  hook.dispose();
});

test('coverage retains historical zones and zero/null, fetches only missing edges, and caches empty success', async () => {
  const requests = [], states = [];
  const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
  const controller = utility.createTemperatureHistory(17, state => states.push(state));
  const middle = { startDate: '2020-01-10', endDate: '2020-01-20' };
  const broad = { startDate: '2020-01-01', endDate: '2020-01-31' };
  const historical = [point('2020-01-15', 11), point('2020-01-15', 22, 18)];
  const first = controller.select(middle);
  requests[0].resolve(response(historical)); await first;
  const expanded = controller.select(broad);
  assert.deepEqual(plain(states.at(-1)), { points: historical, loading: true, failed: false });
  let query = new URL(requests[1].url, 'https://test').searchParams;
  assert.equal(query.get('start'), '2020-01-01'); assert.equal(query.get('end'), '2020-01-09');
  requests[1].resolve(response([])); await tick();
  query = new URL(requests[2].url, 'https://test').searchParams;
  assert.equal(query.get('start'), '2020-01-21'); assert.equal(query.get('end'), '2020-01-31');
  requests[2].reject(new Error('offline')); await expanded;
  assert.deepEqual(plain(states.at(-1)), { points: historical, loading: false, failed: true });
  await controller.select({ startDate: '2020-01-01', endDate: '2020-01-09' });
  assert.equal(requests.length, 3);
  assert.deepEqual(plain(states.at(-1)), { points: [], loading: false, failed: false });
  const recovered = controller.select(broad);
  assert.deepEqual(plain(states.at(-1)), { points: historical, loading: true, failed: false });
  assert.equal(new URL(requests[3].url, 'https://test').searchParams.get('start'), '2020-01-21');
  requests[3].resolve(response([point('2020-01-31', 11, 0)])); await recovered;
  await controller.select({ startDate: '2020-01-15', endDate: '2020-01-15' });
  assert.equal(requests.length, 4);
  assert.deepEqual(plain(states.at(-1)), { points: historical, loading: false, failed: false });
  assert.equal(states.at(-1).points[0].min_temperature_c, null);
  assert.equal(states.at(-1).points[0].average_temperature_c, 0);
  controller.dispose();
});

test('disjoint successful coverage leaves only the interior gap and trailing days to fetch', async () => {
  const loads = [], states = [];
  const { createTemperatureHistory } = setup(() => {});
  const controller = createTemperatureHistory(17, state => states.push(state), new AbortController().signal,
    async (id, selected) => { loads.push(plain(selected)); return []; });
  await controller.select({ startDate: '2020-02-01', endDate: '2020-02-05' });
  await controller.select({ startDate: '2020-02-10', endDate: '2020-02-15' });
  await controller.select({ startDate: '2020-02-01', endDate: '2020-02-20' });
  assert.deepEqual(loads.slice(2), [
    { startDate: '2020-02-06', endDate: '2020-02-09' },
    { startDate: '2020-02-16', endDate: '2020-02-20' },
  ]);
  assert.deepEqual(plain(states.at(-1)), { points: [], loading: false, failed: false });
  controller.dispose();
});

test('completed yearly chunks survive a later pagination failure; incomplete chunks never become coverage', async () => {
  const requests = [], states = [];
  const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
  const controller = utility.createTemperatureHistory(17, state => states.push(state));
  const selected = { ...range, endDate: '2022-01-02' };
  const pending = controller.select(selected);
  const known = [point('2020-02-29')];
  requests[0].resolve(response(known)); await tick();
  assert.deepEqual(plain(states.at(-1)), { points: known, loading: true, failed: false });
  requests[1].resolve(response([point('2021-01-01', 2)], 501, '?offset=500')); await tick();
  requests[2].reject(new Error('page failure')); await pending;
  assert.deepEqual(plain(states.at(-1)), { points: known, loading: false, failed: true });
  const retry = controller.select(selected);
  const query = new URL(requests[3].url, 'https://test').searchParams;
  assert.equal(query.get('start'), '2021-01-01'); assert.equal(query.get('offset'), '0');
  requests[3].resolve(response([])); await tick();
  requests[4].resolve(response([])); await retry;
  assert.deepEqual(plain(states.at(-1)), { points: known, loading: false, failed: false });
  await controller.select(selected);
  assert.equal(requests.length, 5);
  controller.dispose();
});

test('cancelled selections cannot poison successful empty coverage or recovery', async () => {
  const requests = [], states = [];
  const { createTemperatureHistory } = setup(() => {});
  const controller = createTemperatureHistory(17, state => states.push(state), new AbortController().signal,
    (id, selected, signal) => { const request = { ...deferred(), selected, signal }; requests.push(request); return request.promise; });
  const old = controller.select(range);
  controller.cancel();
  requests[0].resolve([point('2020-02-29')]); await old;
  const current = controller.select(range);
  assert.equal(requests.length, 2);
  requests[1].resolve([]); await current;
  await controller.select(range);
  assert.equal(requests.length, 2);
  assert.deepEqual(plain(states.at(-1)), { points: [], loading: false, failed: false });
  controller.dispose();
});

test('hook retains only overlapping completed points before effects and across failure/retry', async () => {
  const hook = hookHarness();
  const original = { startDate: '2020-01-10', endDate: '2020-01-20' };
  const expanded = { startDate: '2020-01-15', endDate: '2020-01-31' };
  const known = [point('2020-01-10'), point('2020-01-15', 11), point('2020-01-15', 22, 18)];
  hook.render(17, original); hook.requests[0].resolve(response(known)); await tick();
  assert.deepEqual(plain(hook.render(17, expanded, false).points), known.slice(1));
  assert.equal(hook.render(17, expanded, false).loading, true);
  hook.render(17, expanded);
  const query = new URL(hook.requests[1].url, 'https://test').searchParams;
  assert.equal(query.get('start'), '2020-01-21'); assert.equal(query.get('end'), '2020-01-31');
  hook.requests[1].reject(new Error('offline')); await tick();
  const failed = hook.render(17, expanded);
  assert.deepEqual(plain({ points: failed.points, loading: failed.loading, failed: failed.failed }),
    { points: known.slice(1), loading: false, failed: true });
  failed.retry();
  assert.equal(hook.render(17, expanded).loading, true);
  hook.requests[2].resolve(response([])); await tick();
  assert.equal(hook.render(17, expanded).failed, false);
  const narrowed = { startDate: '2020-01-21', endDate: '2020-01-31' };
  const empty = hook.render(17, narrowed, false);
  assert.equal(empty.loading, false); assert.equal(empty.failed, false); assert.equal(empty.points.length, 0);
  hook.render(17, narrowed);
  assert.equal(hook.requests.length, 3);
  hook.dispose();
});

test('range A-B-A before effects cannot resurrect a failure or invoke an obsolete retry', async () => {
  const hook = hookHarness();
  const other = { startDate: '2024-01-01', endDate: '2024-01-31' };
  hook.render(17); hook.requests[0].reject(new Error('old failure')); await tick();
  const failed = hook.render(17);
  assert.equal(failed.failed, true);
  hook.render(17, other, false);
  const returning = hook.render(17, range, false);
  assert.equal(returning.failed, false); assert.equal(returning.loading, true);
  failed.retry(); assert.equal(hook.requests.length, 1);
  hook.render(17);
  hook.requests[1].resolve(response([])); await tick();
  const empty = hook.render(17);
  assert.equal(empty.loading, false); assert.equal(empty.failed, false);
  hook.dispose();
});

test('Box and refresh-source A-B-A transitions invalidate completed cache even before effects', async () => {
  for (const mode of ['box', 'source']) {
    const hook = hookHarness();
    const original = [], changed = [{ id: 1 }];
    hook.render(17, range, true, original);
    hook.requests[0].resolve(response([point('2020-02-29')])); await tick();
    const old = hook.render(17, range, true, original);
    assert.equal(old.points.length, 1);
    hook.render(mode === 'box' ? 18 : 17, range, false, mode === 'box' ? original : changed);
    const returning = hook.render(17, range, false, original);
    assert.equal(returning.points.length, 0, mode); assert.equal(returning.loading, true, mode);
    old.retry(); assert.equal(hook.requests.length, 1, mode);
    hook.render(17, range, true, original);
    assert.equal(hook.requests.length, 2, mode);
    hook.requests[1].resolve(response([])); await tick();
    assert.equal(hook.render(17, range, true, original).points.length, 0);
    hook.dispose();
  }
});

test('pending Box and source A-B-A responses cannot publish stale success or error', async () => {
  for (const mode of ['box', 'source']) for (const outcome of ['success', 'error']) {
    const hook = hookHarness();
    const original = [], changed = [{ id: 1 }];
    hook.render(17, range, true, original);
    hook.render(mode === 'box' ? 18 : 17, range, true, mode === 'box' ? original : changed);
    hook.render(17, range, true, original);
    assert.equal(hook.requests[0].options.signal.aborted, true);
    assert.equal(hook.requests[1].options.signal.aborted, true);
    hook.requests[2].resolve(response([point('2020-02-29', 22, 18)])); await tick();
    if (outcome === 'success') hook.requests[0].resolve(response([point('2020-01-01')]));
    else hook.requests[0].reject(new Error('stale failure'));
    hook.requests[1].reject(new Error('stale intermediate failure')); await tick();
    const state = hook.render(17, range, true, original);
    assert.deepEqual(plain(state.points), [point('2020-02-29', 22, 18)]);
    assert.equal(state.loading, false); assert.equal(state.failed, false);
    hook.dispose();
  }
});

test('organization generation invalidates completed coverage across A-B-A and separate hooks never share it', async () => {
  const hook = hookHarness();
  hook.render(17); hook.requests[0].resolve(response([point('2020-02-29')])); await tick();
  const old = hook.render(17);
  assert.equal(old.points.length, 1);
  hook.client.setActiveOrganizationContext(2); hook.client.setActiveOrganizationContext(1);
  const returning = hook.render(17, range, false);
  assert.equal(returning.points.length, 0); assert.equal(returning.loading, true);
  old.retry(); assert.equal(hook.requests.length, 1);
  hook.render(17);
  hook.requests[1].resolve(response([])); await tick();
  assert.equal(hook.render(17).loading, false);
  hook.dispose();
  const separate = hookHarness();
  const initial = separate.render(17);
  assert.equal(initial.loading, true); assert.equal(separate.requests.length, 1);
  separate.dispose();
});

test('effect replay cancels the old read and recovers; disabled and disposed hooks cannot retry', async () => {
  const hook = hookHarness();
  hook.render(17);
  hook.replayEffects();
  assert.equal(hook.requests.length, 2);
  assert.equal(hook.requests[0].options.signal.aborted, true);
  hook.requests[1].resolve(response([point('2020-02-29')])); await tick();
  hook.requests[0].reject(new Error('old replay failure')); await tick();
  assert.equal(hook.render(17).points.length, 1);
  const disabled = hook.render(undefined);
  assert.equal(disabled.points.length, 0); assert.equal(disabled.loading, false); assert.equal(disabled.failed, false);
  disabled.retry(); assert.equal(hook.requests.length, 2);
  hook.render(17);
  const pending = hook.render(17);
  hook.dispose();
  assert.equal(hook.requests[2].options.signal.aborted, true);
  pending.retry(); assert.equal(hook.requests.length, 3);
  hook.requests[2].resolve(response([point('2020-01-01')])); await tick();
});

test('invalid selections fail explicitly without loading or caching an empty result', async () => {
  const loads = [], states = [];
  const { createTemperatureHistory } = setup(() => {});
  const controller = createTemperatureHistory(17, state => states.push(state), new AbortController().signal,
    async (id, selected) => { loads.push(selected); return []; });
  await controller.select({ startDate: '2020-02-30', endDate: '2020-03-01' });
  assert.deepEqual(plain(states.at(-1)), { points: [], loading: false, failed: true });
  assert.equal(loads.length, 0);
  await controller.select(range);
  assert.equal(loads.length, 1);
  assert.deepEqual(plain(states.at(-1)), { points: [], loading: false, failed: false });
  controller.dispose();
});

test('inline status keeps a stable polite span and shows no loading or failure text', () => {
  const jsx = (type, props) => ({ type, props });
  for (const language of ['fr', 'en']) {
    const component = load('../src/components/TemperatureHistoryStatus.tsx', {
      'react/jsx-runtime': { jsx, jsxs: jsx },
    }).default;
    for (const points of [[], [point('2020-02-29')]]) {
      for (const state of [
        { loading: true, failed: false }, { loading: true, failed: true },
        { loading: false, failed: true }, { loading: false, failed: false },
      ]) {
        const rendered = component({ points, language, retry() {}, ...state });
        assert.equal(rendered.type, 'span');
        assert.equal(rendered.props.className, 'bio-trend-series-status');
        assert.equal(rendered.props.role, 'status');
        assert.equal(rendered.props.children, undefined);
      }
    }
  }
});

test('Box, preview and Overview use the native range loader and put translated status inside the chart', () => {
  const source = name => readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8');
  assert.match(source('components/BoxPage.tsx'), /<BoxInsights\s+boxId=\{box.id\}/);
  assert.match(source('components/BoxInsights.tsx'), /<BoxTrackingChart\s+boxId=\{boxId\}/);
  assert.match(source('components/BoxTrackingChart.tsx'), /useTemperatureHistory\(boxId, startDate, endDate, locations\)/);
  assert.match(source('components/OverviewView.tsx'), /useTemperatureChartActivation\(box.id\)/);
  assert.match(source('components/OverviewView.tsx'), /temperatureBoxId = temperatureActivation.active \? box.id : undefined/);
  assert.match(source('components/OverviewView.tsx'), /useTemperatureHistory\(temperatureBoxId, startDate, endDate, box, temperatureLoaders.daily\)/);
  assert.match(source('components/OverviewView.tsx'), /useHistoricalTemperatures\(temperatureBoxId, startDate, endDate, box, temperatureLoaders.historical\)/);
  assert.match(source('components/OverviewView.tsx'), /createTemperatureLoadQueue\(2\)/);
  assert.match(source('components/OverviewView.tsx'), /temperatures=\{chartTemperatures\}/);
  assert.match(source('components/OverviewView.tsx'), /temperatureStatus=\{<>\s*<TemperatureHistoryStatus \{\.\.\.temperatureHistory\} language=\{language\} \/>/);
  assert.match(source('components/OverviewView.tsx'), /<HistoricalTemperatureStatus \{\.\.\.historicalTemperatures\}/);
  assert.match(source('components/BoxTrackingChart.tsx'), /temperatureStatus=\{boxId != null \? <>\s*<TemperatureHistoryStatus/);
  assert.match(source('components/BoxTrackingPreview.tsx'), /<BoxTrackingChart compact boxId=\{detail.id\}/);
  for (const language of ['fr', 'en']) {
    const catalog = load(`../src/i18n/${language}.ts`, {})[language];
    for (const key of ['temperatureHistoryLoading', 'temperatureHistoryFailed', 'temperatureHistoryRetry']) assert.equal(catalog[key], undefined);
  }
});
