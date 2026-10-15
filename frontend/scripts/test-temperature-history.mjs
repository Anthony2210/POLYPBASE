import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { resourceClient } from './resource-test-harness.mjs';

function load(path, dependencies) {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  vm.runInNewContext(code, { exports, AbortController, URLSearchParams, Date,
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
  const client = resourceClient({ fetch });
  const utility = load('../src/utils/temperatureHistory.ts', { '../api/client': client });
  return { client, ...utility };
}

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
  const disposed = controller.select(range); controller.dispose();
  const count = publications.length;
  assert.equal(requests[3].options.signal.aborted, true);
  requests[3].resolve(response([point('2020-01-01')])); await disposed;
  assert.equal(publications.length, count);
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
  const requests = [], slots = [], effects = [], cleanup = [];
  let cursor = 0;
  const utility = setup((url, options) => { const request = { ...deferred(), url, options }; requests.push(request); return request.promise; });
  const hooks = {
    useMemo: factory => factory(),
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial; return [slots[i], value => { slots[i] = value; }]; },
    useRef(initial) { const i = cursor++; slots[i] ??= { current: initial }; return slots[i]; },
    useEffect(fn, deps) { const i = cursor++; if (!slots[i] || deps.some((dep, j) => dep !== slots[i][j])) {
      slots[i] = deps; effects.push(() => { cleanup[i]?.(); cleanup[i] = fn(); });
    } },
  };
  const { useTemperatureHistory } = load('../src/hooks/useTemperatureHistory.ts', { react: hooks,
    '../api/client': utility.client, '../utils/temperatureHistory': utility });
  return { requests, client: utility.client, hooks, useTemperatureHistory,
    renderWith(component, props) { cursor = 0; const tree = component(props); while (effects.length) effects.shift()(); return tree; },
    render(boxId, selected = range, flush = true, refreshSource) { cursor = 0; const state = useTemperatureHistory(boxId, selected.startDate, selected.endDate, refreshSource); if (flush) while (effects.length) effects.shift()(); return state; },
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

test('actual Box chart controls load historical ranges, clear old points, and preserve biology on failure/retry', async () => {
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
  }).default;
  const props = { boxId: 17, language: 'en', measurements: [{ id: 1, measured_on: '2020-02-29', polyp_count: 0, ephyrae_count: 0, salinity_psu: null }],
    locations: [], events: [], labels: { polyps: 'Polyps', chartTitle: 'Measurements', chartEmpty: 'Empty' } };
  function find(tree, type) {
    if (Array.isArray(tree)) return tree.flatMap(node => find(node, type));
    if (!tree?.props) return [];
    return [...(tree.type === type ? [tree.props] : []), ...find(tree.props.children, type)];
  }
  let tree = hook.renderWith(chart, props);
  const initial = find(tree, controls)[0];
  const initialQuery = new URL(hook.requests[0].url, 'https://test').searchParams;
  assert.equal(initialQuery.get('start'), initial.startDate);
  assert.equal(initialQuery.get('end'), initial.endDate);
  hook.requests[0].resolve(response([point('2020-02-29')])); await tick();
  tree = hook.renderWith(chart, props);
  assert.deepEqual(plain(find(tree, trend)[0].temperatures), [point('2020-02-29')]);
  find(tree, controls)[0].onChange('2020-02-29', '2020-03-01');
  tree = hook.renderWith(chart, props);
  assert.equal(find(tree, trend)[0].temperatures.length, 0);
  const query = new URL(hook.requests[1].url, 'https://test').searchParams;
  assert.equal(query.get('start'), '2020-02-29'); assert.equal(query.get('end'), '2020-03-01');
  hook.requests[1].reject(new Error('offline')); await tick();
  tree = hook.renderWith(chart, props);
  assert.equal(find(tree, status)[0].failed, true);
  assert.equal(find(tree, trend)[0].measurements[0].polypCount, 0);
  find(tree, status)[0].retry();
  hook.requests[2].resolve(response([])); await tick();
  tree = hook.renderWith(chart, props);
  assert.equal(find(tree, status)[0].failed, false);
  assert.equal(find(tree, trend)[0].temperatures.length, 0);
  hook.dispose();
});

test('shared status is accessible, quiet during loading, and translated with an actionable retry', () => {
  const jsx = (type, props) => ({ type, props });
  for (const language of ['fr', 'en']) {
    const catalog = load(`../src/i18n/${language}.ts`, {})[language];
    const component = load('../src/components/TemperatureHistoryStatus.tsx', {
      '../i18n': { createTranslator: () => key => catalog[key] },
      'react/jsx-runtime': { jsx, jsxs: jsx },
    }).default;
    const loading = component({ loading: true, failed: false, language, retry() {} }).props.children[0];
    assert.equal(loading.props.className, 'sr-only'); assert.equal(loading.props.role, 'status');
    assert.equal(loading.props.children, catalog.temperatureHistoryLoading);
    let retried = false;
    const error = component({ loading: false, failed: true, language, retry() { retried = true; } }).props.children[1];
    assert.equal(error.props.role, 'alert');
    const button = error.props.children[1]; assert.equal(button.props.children, catalog.temperatureHistoryRetry);
    button.props.onClick(); assert.equal(retried, true);
  }
});

test('Box and Overview wire the same range hook and loading/error/retry translations exist in both languages', () => {
  const source = name => readFileSync(new URL(`../src/${name}`, import.meta.url), 'utf8');
  assert.match(source('components/BoxPage.tsx'), /<BoxInsights\s+boxId=\{box.id\}/);
  assert.match(source('components/BoxInsights.tsx'), /<BoxTrackingChart\s+boxId=\{boxId\}/);
  assert.match(source('components/BoxTrackingChart.tsx'), /useTemperatureHistory\(boxId, startDate, endDate, locations\)/);
  assert.match(source('components/OverviewView.tsx'), /useTemperatureHistory\(box.id, startDate, endDate, box\)/);
  assert.match(source('components/OverviewView.tsx'), /temperatures=\{temperatureHistory.points\}/);
  for (const language of ['fr', 'en']) {
    const catalog = load(`../src/i18n/${language}.ts`, {})[language];
    for (const key of ['temperatureHistoryLoading', 'temperatureHistoryFailed', 'temperatureHistoryRetry']) assert.ok(catalog[key]);
  }
});
