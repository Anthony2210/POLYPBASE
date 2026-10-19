import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as scales from 'd3-scale';
import * as shapes from 'd3-shape';
import { resourceClient } from './resource-test-harness.mjs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
function compile(source, dependencies = {}, globals = {}) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, Date, Number, Map, URLSearchParams, Promise, Error, ...globals,
    require(name) { assert.ok(name in dependencies, `Unexpected dependency: ${name}`); return dependencies[name]; } });
  return exports;
}
const plain = (value) => JSON.parse(JSON.stringify(value));
// ISO week N of 2026 starts on Monday 2025-12-29 + 7 * (N - 1) days.
const point = (id, week, temperature = '15.250', year = 2026) => {
  const start = new Date(Date.UTC(2025, 11, 29) + (week - 1) * 7 * 86_400_000);
  const end = new Date(start.getTime() + 6 * 86_400_000);
  return { id, key: `historical_weekly:${id}`, source_kind: 'historical_weekly', temporal_precision: 'iso_week',
    statistic: 'source_reading', temperature_c: temperature, unit: 'degC', iso_year: year, iso_week: week,
    interval_start: start.toISOString().slice(0, 10), interval_end: end.toISOString().slice(0, 10),
    measured_on: null, measured_at: null, biological_measurement_id: id + 100, box_id: 7,
    thermal_zone_id: null, probe_id: null, evidence_count: 1 };
};
const response = (results, count = results.length, next = null) => ({ ok: true,
  headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ({ results, count, next, previous: null }) });

function utilities(fetch) {
  const client = resourceClient({ fetch });
  const history = compile(read('../src/utils/temperatureHistory.ts'), { '../api/client': client });
  return compile(read('../src/utils/historicalTemperature.ts'), {
    '../api/client': client, './temperatureHistory': history,
  }, { AbortController });
}

test('zero stays a plotted measurement and the exact source spelling is displayed', () => {
  const { toWeeklyTrendTemperature } = utilities(async () => response([]));
  const zero = toWeeklyTrendTemperature(point(1, 2, '0'));
  assert.equal(zero.average_temperature_c, 0);
  assert.equal(zero.week.valueText, '0');
  assert.equal(toWeeklyTrendTemperature(point(2, 2, '-0.50')).average_temperature_c, -0.5);
  assert.equal(toWeeklyTrendTemperature(point(3, 2, '15.250')).week.valueText, '15.250');
});

test('missing, empty or non-numeric values are dropped, never turned into zero', () => {
  const { toWeeklyTrendTemperature, weeklyTrendTemperatures } = utilities(async () => response([]));
  for (const value of [null, undefined, '', ' ', 'NaN', 'Infinity', '15,2', 0, 15.2]) {
    assert.equal(toWeeklyTrendTemperature({ ...point(1, 2), temperature_c: value }), null, String(value));
  }
  assert.equal(weeklyTrendTemperatures([{ ...point(1, 2), temperature_c: '' }, point(2, 3, '0')]).length, 1);
});

test('the plotting position is the middle of the week and the week bounds are kept as uncertainty', () => {
  const { toWeeklyTrendTemperature } = utilities(async () => response([]));
  const trend = toWeeklyTrendTemperature(point(1, 2));
  assert.deepEqual(plain(trend.week), { id: 1, year: 2026, number: 2, start: '2026-01-05', end: '2026-01-11', valueText: '15.250' });
  assert.equal(trend.date, '2026-01-08');
  assert.equal(trend.zone_id, undefined);
  assert.equal(trend.zone_name, undefined);
});

test('loader follows pages, dedupes weeks seen in two chunks and keeps the organization context', async () => {
  const requests = [];
  const pages = [
    response([point(1, 2), point(2, 3)], 501, 'https://ignored.test/'),
    response([point(3, 4, '0')], 501),
    response([point(3, 4, '0'), point(4, 5)]),
  ];
  const { loadHistoricalTemperatures } = utilities(async (url, options) => { requests.push({ url, options }); return pages.shift(); });
  const result = await loadHistoricalTemperatures(7, { startDate: '2025-01-01', endDate: '2026-12-31' }, new AbortController().signal);
  assert.deepEqual(plain(result.map(({ id }) => id)), [1, 2, 3, 4]);
  assert.equal(result.find(({ id }) => id === 3).temperature_c, '0');
  assert.deepEqual(requests.map(({ url }) => Object.fromEntries(new URL(url, 'https://test').searchParams)), [
    { start: '2025-01-01', end: '2026-01-01', limit: '500', offset: '0' },
    { start: '2025-01-01', end: '2026-01-01', limit: '500', offset: '500' },
    { start: '2026-01-02', end: '2026-12-31', limit: '500', offset: '0' },
  ]);
  for (const { url, options } of requests) {
    assert.match(url, /^\/api\/boxes\/7\/historical-temperatures\/\?/);
    assert.equal(options.cache, 'no-store');
    assert.equal(new Headers(options.headers).get('X-Organization-ID'), '1');
  }
});

test('a failed or cancelled page rejects, so no partial series is published', async () => {
  const pages = [response([point(1, 2)], 600, 'https://ignored.test/')];
  const { loadHistoricalTemperatures } = utilities(async () => pages.shift() ?? ({ ok: false, status: 503,
    headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ({}) }));
  await assert.rejects(loadHistoricalTemperatures(7, { startDate: '2026-01-01', endDate: '2026-12-31' }, new AbortController().signal));
  const controller = new AbortController();
  controller.abort();
  await assert.rejects(loadHistoricalTemperatures(7, { startDate: '2026-01-01', endDate: '2026-12-31' }, controller.signal));
});

const chartSource = read('../src/components/BiologicalTrendChart.tsx');
const chart = compile(chartSource, {
  'react/jsx-runtime': {}, react: {}, 'd3-scale': scales, 'd3-shape': shapes,
  '../utils/userIdentity': compile(read('../src/utils/userIdentity.ts')),
  '../utils/dateFormat': compile(read('../src/utils/dateFormat.ts')),
  '../utils/chartBiology': compile(read('../src/utils/chartBiology.ts')),
  '../utils/chartLocations': compile(read('../src/utils/chartLocations.ts')),
});

test('weekly points are plotted with a real zero next to current zone readings', () => {
  const { toWeeklyTrendTemperature } = utilities(async () => response([]));
  const weekly = [point(1, 2, '0'), point(2, 3, '12.5')].map(toWeeklyTrendTemperature);
  const current = [{ date: '2026-01-20', average_temperature_c: 14, zone_id: 1, zone_name: 'Zone' }];
  const geometry = chart.buildGeometry([], [], [], '2026-01-01', '2026-01-31', false, undefined, [], [...weekly, ...current]);
  assert.equal(geometry.plottedTemperatures.length, 3);
  assert.equal(geometry.plottedTemperatures[0].average_temperature_c, 0);
  for (const plotted of geometry.plottedTemperatures) {
    const y = geometry.yTemperature(plotted.average_temperature_c);
    assert.ok(y >= geometry.plotTop && y <= geometry.countHeight - geometry.padding.bottom);
  }
  const stray = chart.buildGeometry([], [], [], '2026-01-01', '2026-01-31', false, undefined, [],
    [{ date: '2026-01-08', average_temperature_c: null }, { date: '2026-01-09', average_temperature_c: NaN }]);
  assert.equal(stray.plottedTemperatures.length, 0);
});

test('chart renders week-only readings as a titled week with a possible period, not a date', () => {
  assert.match(chartSource, /point\.week \? `\$\{labels\.historicalWeek \?\? ''\} \$\{point\.week\.number\}, \$\{point\.week\.year\}`/);
  assert.match(chartSource, /bio-trend-week-span/);
  assert.match(chartSource, /point\.week\?\.valueText \?\? String\(point\.average_temperature_c\)/);
  assert.match(chartSource, /plottedTemperatures\.filter\(\(point\) => !point\.week\)/);
});

test('every historical string exists in French and English without a middle dot', () => {
  const french = read('../src/i18n/fr.ts');
  const english = read('../src/i18n/en.ts');
  const keys = [...french.matchAll(/^ {2}(historicalTemperature\w+):/gm)].map((match) => match[1]);
  assert.ok(keys.length >= 7);
  for (const key of keys) {
    for (const text of [french, english]) {
      const line = text.match(new RegExp(`^ {2}${key}:.*$`, 'm'));
      assert.ok(line, key);
      assert.ok(!line[0].includes('·'));
    }
  }
});

test('weekly and daily series have separate legend toggles, ids and main-scale handling', () => {
  assert.match(chartSource, /temperaturePointId[\s\S]*temperature-week-\$\{point\.week\.id\}/);
  assert.match(chartSource, /point\.week \? historicalVisible : temperatureVisible/);
  assert.match(chartSource, /is-temperature is-weekly/);
  assert.match(chartSource, /labels\.historicalTemperature\} \(°C\)/);
  assert.match(chartSource, /temperatureOutsideScale/);
  // The legend is not gated on compact, so compact charts carry the distinction too.
  assert.doesNotMatch(chartSource.slice(chartSource.indexOf('bio-trend-legend bio-trend-legend--footer')), /\{!compact/);
  const css = read('../src/styles/components/biological-trend-chart.css');
  assert.match(css, /\.bio-trend-legend button\.is-weekly::before/);
  const tracking = read('../src/components/BoxTrackingChart.tsx');
  assert.match(tracking, /temperatureStatus=\{boxId != null/);
  assert.match(tracking, /historicalTemperature: boxId != null/);
});

test('zero, negative and above-scale weekly values keep their exact text and outside-scale handling', () => {
  const { toWeeklyTrendTemperature } = utilities(async () => response([]));
  const geometry = chart.buildGeometry([], [], [], '2026-01-01', '2026-01-31', false, undefined, [],
    ['0', '-2.5', '45.125'].map((value, index) => toWeeklyTrendTemperature(point(index + 1, index + 2, value))));
  assert.deepEqual(plain(geometry.plottedTemperatures.map((item) => item.week.valueText)), ['0', '-2.5', '45.125']);
  assert.equal(geometry.plottedTemperatures[0].average_temperature_c, 0);
  assert.match(chartSource, /point\.average_temperature_c === 0 \? layout\.zeroRadius/);
});

test('status shows loading and failure with retry inside the chart feedback area', () => {
  const source = read('../src/components/HistoricalTemperatureStatus.tsx');
  for (const key of ['Loading', 'Failed', 'Retry']) assert.match(source, new RegExp(`historicalTemperature${key}`));
  assert.match(source, /role="alert"/);
  assert.match(source, /onClick=\{retry\}/);
  assert.match(source, /bio-trend-series-status/);
  assert.doesNotMatch(source, /historicalTemperatureEmpty/, 'no empty message for boxes without weekly values');
  assert.match(source, /if \(!loading && !failed\) return null/, 'an idle status renders nothing');
});

function hookHarness(fetch) {
  const client = resourceClient({ fetch });
  const effects = [];
  const states = [];
  let cursor = 0;
  const react = {
    useState(initial) { const i = cursor++; states[i] ??= initial; return [states[i], (v) => { states[i] = typeof v === 'function' ? v(states[i]) : v; }]; },
    useMemo(factory, deps) { const i = cursor++; const old = states[i]; if (old && old.deps.every((d, n) => Object.is(d, deps[n]))) return old.value; const value = factory(); states[i] = { deps, value }; return value; },
    useEffect(effect, deps) { const i = cursor++; const old = states[i]; if (old && old.deps.every((d, n) => Object.is(d, deps[n]))) return; if (old?.cleanup) old.cleanup(); states[i] = { deps, cleanup: undefined, pending: effect }; effects.push(i); },
  };
  const utility = utilities(fetch);
  const hook = compile(read('../src/hooks/useHistoricalTemperatures.ts'), {
    react, '../api/client': client, '../utils/historicalTemperature': utility,
  }, { AbortController });
  const render = (...args) => { cursor = 0; const out = hook.useHistoricalTemperatures(...args); while (effects.length) { const i = effects.shift(); states[i].cleanup = states[i].pending(); } return out; };
  return { render, client };
}
const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };

test('a changed measurement set drops loaded points at once and reloads, so corrected readings are not stale', async () => {
  let calls = 0;
  const { render } = hookHarness(async () => response(calls++ === 0 ? [point(1, 2, '0')] : []));
  const range = ['2026-01-01', '2026-03-31'];
  assert.equal(render(7, ...range, 'm1:15').loading, true);
  await flush();
  const loaded = render(7, ...range, 'm1:15');
  assert.equal(loaded.points.length, 1);
  assert.equal(loaded.points[0].temperature_c, '0');
  const corrected = render(7, ...range, 'm1:16');
  assert.equal(corrected.points.length, 0);
  assert.equal(corrected.loading, true);
  await flush();
  const reloaded = render(7, ...range, 'm1:16');
  assert.equal(reloaded.points.length, 0);
  assert.equal(reloaded.loading, false);
  assert.equal(reloaded.failed, false);
  assert.equal(calls, 2);
  // Another Box never shows the previous Box's points.
  assert.equal(render(8, ...range, 'm1:16').points.length, 0);
});

test('identity-only publication drops evidence immediately and delayed pre-correction loads cannot restore it', async () => {
  const requests = [];
  const { render } = hookHarness((url, options) => new Promise(resolve => requests.push({ url, options, resolve })));
  const range = ['2026-01-01', '2026-03-31'];
  const oldBox = { id: 7, global_code: 'PCA-CHO-1.005' };
  const correctedBox = { ...oldBox, global_code: 'PCA-JKA-1.005' };
  render(7, ...range, oldBox);
  assert.equal(requests.length, 1);
  const corrected = render(7, ...range, correctedBox);
  assert.equal(corrected.points.length, 0);
  assert.equal(corrected.loading, true);
  assert.equal(requests[0].options.signal.aborted, true);
  assert.equal(requests.length, 2, 'same numeric box with changed identity re-fetches');
  requests[1].resolve(response([]));
  await flush();
  requests[0].resolve(response([point(1, 2, '0')]));
  await flush();
  assert.equal(render(7, ...range, correctedBox).points.length, 0);
  assert.equal(render(7, ...range, correctedBox).loading, false);
  assert.equal(requests.length, 2, 'unchanged publication does not re-fetch');
});

test('organization switch discards historical points and late completion', async () => {
  const requests = [];
  const { render, client } = hookHarness((url, options) => new Promise(resolve => requests.push({ url, options, resolve })));
  const args = [7, '2026-01-01', '2026-03-31', 'same-box'];
  render(...args);
  client.setActiveOrganizationContext(2);
  assert.equal(render(...args).points.length, 0);
  assert.equal(requests[0].options.signal.aborted, true);
  requests[1].resolve(response([]));
  requests[0].resolve(response([point(1, 2)]));
  await flush();
  assert.equal(render(...args).points.length, 0);
  assert.equal(render(...args).loading, false);
});

test('box publication is wired through both chart consumers, not just count/date fingerprints', () => {
  assert.match(read('../src/components/BoxPage.tsx'), /historicalTemperatureRefreshKey=\{box\}/);
  assert.match(read('../src/components/BoxInsights.tsx'), /historicalTemperatureRefreshKey=\{historicalTemperatureRefreshKey\}/);
  assert.match(read('../src/components/BoxTrackingPreview.tsx'), /historicalTemperatureRefreshKey=\{detail\}/);
  const tracking = read('../src/components/BoxTrackingChart.tsx');
  assert.match(tracking, /\[historicalTemperatureRefreshKey, measurements, locations\]/);
  assert.match(tracking, /useHistoricalTemperatures\(boxId, extentStart, extentEnd, historicalRefreshScope\)/);
});

test('a failed load is reported and retry loads again', async () => {
  let calls = 0;
  const { render } = hookHarness(async () => (calls++ === 0
    ? { ok: false, status: 503, headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ({}) }
    : response([point(1, 2)])));
  const range = ['2026-01-01', '2026-03-31'];
  render(7, ...range, 'k');
  await flush();
  const failed = render(7, ...range, 'k');
  assert.equal(failed.failed, true);
  assert.equal(failed.loading, false);
  failed.retry();
  const retrying = render(7, ...range, 'k');
  assert.equal(retrying.loading, true);
  assert.equal(retrying.failed, false);
  await flush();
  assert.equal(render(7, ...range, 'k').points.length, 1);
});
