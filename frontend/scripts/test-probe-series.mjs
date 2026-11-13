import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import * as d3Scale from 'd3-scale';
import * as d3Shape from 'd3-shape';
import ts from 'typescript';

function load(path, imports = {}, globals = {}) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, Intl, Date, Math, Number, Set, ...globals, require(name) {
    assert.ok(Object.hasOwn(imports, name), `Unexpected import: ${name}`);
    return imports[name];
  } });
  return exports;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const chart = load('../src/utils/probeChart.ts');
const gestures = load('../src/utils/probeGestures.ts');
const series = load('../src/utils/probeSeries.ts', { './probeChart': chart });
const environment = load('../src/utils/environmentFormat.ts');
const fr = load('../src/i18n/fr.ts').fr;
const en = load('../src/i18n/en.ts').en;

const raw = (time, value) => [time, null, value.toFixed(2), 1, value, value, time];
const interval = (start, end, mean, low, high, count = 15, last = end - MINUTE) =>
  [start, end, (mean * count).toFixed(2), count, low, high, last];
const probe = (id, sources, overrides = {}) => ({
  id, code: `P${id}`, location: '', is_active: true, external_channel: id,
  sampling_interval_seconds: 60, points: sources.map(() => [0, 0, null, null]), point_sources: sources.map((source) => [source]), ...overrides,
});
const readings = (probes, resolution = 1) => ({
  zone_id: 7, start: '', end: '', resolution_minutes: resolution, target_temperature_c: '22.00',
  first_reading_at: null, last_reading_at: null, monitoring: {}, probes,
});
const steady = (id, count = 5, step = MINUTE, value = 22) =>
  probe(id, Array.from({ length: count }, (_, index) => raw(index * step, value + id / 10)));
const window = { start: 0, end: 10 * MINUTE };

test('the average hue is the perceptual mix of the probe hues, legible and in sync with the stylesheet', () => {
  const css = readFileSync(new URL('../src/styles/components/probe-charts.css', import.meta.url), 'utf8');
  series.SERIES_COLORS.forEach((color, slot) => {
    assert.match(css, new RegExp(`\\.probe-series-${slot + 1} \\{ --series-color: ${color};`), `slot ${slot + 1}`);
  });
  assert.equal(series.averageSeriesColor([]), null);
  const mixed = series.averageSeriesColor([{ slot: 0 }, { slot: 1 }]);
  assert.match(mixed, /^#[0-9a-f]{6}$/);
  assert.ok(!series.SERIES_COLORS.includes(mixed), 'The average never takes one probe hue');
  assert.equal(series.averageSeriesColor([{ slot: 1 }, { slot: 0 }]), mixed, 'Order does not matter');
  const luminance = (hex) => [1, 3, 5].map((index) => Number.parseInt(hex.slice(index, index + 2), 16) / 255)
    .map((value) => (value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4))
    .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0);
  for (const slots of [[0, 1], [0, 2], [0, 1, 2], [0, 1, 2, 3, 4, 5, 6, 7]]) {
    const color = series.averageSeriesColor(slots.map((slot) => ({ slot })));
    assert.ok(1.05 / (luminance(color) + 0.05) >= 4.5, `${color} keeps text-level contrast on white`);
  }
});

function render(path, props, imports, globals = {}, prepare = null) {
  const slots = [];
  let cursor = 0;
  let pendingEffects = [];
  const hooks = {
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial === 0 ? 800 : initial };
      return [slots[index].value, (next) => { slots[index].value = typeof next === 'function' ? next(slots[index].value) : next; }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useMemo(factory) { cursor++; return factory(); },
    useId() { cursor++; return 'test-id'; },
    useEffect(effect) { cursor++; pendingEffects.push(effect); },
    useLayoutEffect(effect) { cursor++; pendingEffects.push(effect); },
    useCallback(callback) { cursor++; return callback; },
  };
  const component = load(path, { react: hooks, 'react/jsx-runtime': jsxRuntime, ...imports }, globals);
  const view = {
    tree: null,
    render(name = 'default', next = props) {
      cursor = 0; pendingEffects = []; view.name = name;
      view.tree = component[name](prepare ? prepare(name, next, component) : next);
      return view;
    },
    flush() { const effects = pendingEffects; pendingEffects = []; for (const effect of effects) effect(); },
    component,
    find(predicate, node = view.tree) {
      if (Array.isArray(node)) return node.flatMap((child) => view.find(predicate, child));
      if (!React.isValidElement(node)) return [];
      return [...(predicate(node) ? [node] : []), ...view.find(predicate, node.props.children ?? null)];
    },
    text(node = view.tree) {
      if (Array.isArray(node)) return node.map((child) => view.text(child)).join('');
      if (React.isValidElement(node)) return view.text(node.props.children ?? null);
      return node == null || typeof node === 'boolean' ? '' : String(node);
    },
  };
  return view;
}

const chartLabels = { target: 'Target', mean: 'Mean', minimum: 'Min', maximum: 'Max', unit: '°C', keyboardDescription: 'Keys',
  average: 'Probe average', averageContributors: '{count} contributing probes' };
function renderChart(data, overrides = {}) {
  const built = series.buildProbeSeries(data);
  const props = {
    ariaLabel: 'Chart', average: series.buildProbeAverage(data), domain: [20, 26], height: 320, labels: chartLabels, locale: 'en-GB', series: built, target: 22, window,
    onZoom() {}, onResetZoom() {}, ...overrides,
  };
  const view = render('../src/components/ProbeSeriesChart.tsx', props, {
    'd3-scale': d3Scale, 'd3-shape': d3Shape, '../utils/environmentFormat': environment,
    '../utils/probeChart': chart, '../utils/probeSeries': series, '../utils/probeGestures': gestures,
  }, { ResizeObserver: class { observe() {} disconnect() {} }, URLSearchParams });
  view.render();
  const svg = () => view.find((node) => node.type === 'svg')[0];
  view.hover = (time) => {
    const x = 44 + ((time - window.start) / (window.end - window.start)) * (800 - 44 - 16);
    svg().props.onPointerMove({ pointerType: 'mouse', clientX: x, clientY: 40, currentTarget: { getBoundingClientRect: () => ({ left: 0 }) } });
    view.render();
    return view.find((node) => node.props.className === 'probe-series-tooltip')[0];
  };
  view.groups = () => view.find((node) => (node.props.className ?? '').startsWith('probe-series probe-series-'));
  view.svg = svg;
  return view;
}

for (const count of [1, 2, 10]) {
  test(`${count} probe(s) share one chart, axes and one distinct curve each`, () => {
    const data = readings(Array.from({ length: count }, (_, index) => steady(index + 1)));
    const view = renderChart(data);
    assert.equal(view.find((node) => node.type === 'svg').length, 1);
    assert.equal(view.groups().length, count);
    const classes = view.groups().map((node) => node.props.className);
    assert.equal(new Set(classes).size, count, 'Every probe up to ten has its own visual identity');
    assert.equal(view.find((node) => node.props.className === 'probe-series-unit')[0].props.children, '°C');
    assert.ok(view.groups().every((node) => view.find((child) => child.props.className === 'probe-series-line', node).length === 1));
  });
}

test('every series style has a solid curve: eight hues, then a marker for repeated hues', () => {
  const css = readFileSync(new URL('../src/styles/components/probe-charts.css', import.meta.url), 'utf8');
  const looks = [];
  const colors = [];
  for (let slot = 1; slot <= series.SERIES_STYLE_COUNT; slot += 1) {
    const rule = css.match(new RegExp(`\.probe-series-${slot} \{([^}]+)\}`))[1];
    assert.match(rule, /--series-color/);
    assert.doesNotMatch(rule, /dash/);
    looks.push(rule.trim());
    colors.push(rule.match(/--series-color: (#[0-9a-f]{6})/)[1]);
  }
  assert.equal(new Set(looks).size, series.SERIES_STYLE_COUNT);
  assert.equal(new Set(colors.slice(0, 8)).size, 8, 'The first eight probes each have their own hue');
  for (const slot of [9, 10]) {
    assert.match(css, new RegExp(`\.probe-series-${slot} \{[^}]*--series-marker-fill`), `slot ${slot} adds a marker`);
    assert.match(css, new RegExp(`\.probe-series-name i\.probe-series-${slot}::before`), `legend ${slot} repeats the marker`);
  }
  assert.doesNotMatch(css, /color-danger/, 'No probe curve may borrow the danger color');
});

test('asynchronous cadences keep their own instants and are never aligned', () => {
  const data = readings([
    probe(1, [raw(0, 22), raw(MINUTE, 22.1), raw(2 * MINUTE, 22.2)]),
    probe(2, [raw(17_000, 23), raw(617_000, 23.5)], { sampling_interval_seconds: 600 }),
  ]);
  const [first, second] = series.buildProbeSeries(data);
  assert.deepEqual([...first.readings.map((r) => r[0])], [0, MINUTE, 2 * MINUTE]);
  assert.deepEqual([...second.readings.map((r) => r[0])], [17_000, 617_000]);
  assert.equal(second.cadenceMs, 600_000);
  assert.equal(series.splitReadingsAtGaps(second.readings, second.cadenceMs).length, 1, 'Normal slow cadence is not a gap');
  const view = renderChart(data);
  const tooltip = view.hover(60_000);
  const rows = view.find((node) => node.type === 'span', tooltip);
  assert.equal(rows.length, 2);
  assert.match(view.text(rows[0]), /^P122\.1°C/);
  assert.match(view.text(rows[1]), /^P223\.0°C/);
  assert.doesNotMatch(view.text(tooltip), /\(\+/, 'The tooltip stays light: no gap to target per row');
  assert.equal(view.text(rows[1]), 'P223.0°C', 'No per-probe time next to the value');
});

test('a real gap splits the curve; the threshold follows each probe cadence', () => {
  const left = [raw(0, 22), raw(MINUTE, 22), raw(10 * MINUTE, 22), raw(11 * MINUTE, 22)];
  const built = series.buildProbeSeries(readings([probe(1, left)]))[0];
  const segments = series.splitReadingsAtGaps(built.readings, built.cadenceMs);
  assert.equal(segments.length, 2);
  assert.equal(segments[0].at(-1)[0], MINUTE);
  assert.equal(segments[1][0][0], 10 * MINUTE);
  assert.equal(series.splitReadingsAtGaps([[0, 1], [3 * MINUTE, 1]], MINUTE).length, 1, 'Exactly three cadences stays connected');
  assert.equal(series.splitReadingsAtGaps([[0, 1], [3 * MINUTE + 1, 1]], MINUTE).length, 2);
  const view = renderChart(readings([probe(1, left)]));
  const path = view.find((node) => node.props.className === 'probe-series-line')[0].props.d;
  assert.equal(path.match(/M/g).length, 2, 'Never connect across the gap');
});

test('a tooltip never invents a value inside a gap', () => {
  const data = readings([probe(1, [raw(0, 22), raw(10 * MINUTE - 1, 22)])]);
  assert.equal(series.seriesEntryAt(series.buildProbeSeries(data)[0], 5 * MINUTE), null);
  const view = renderChart(data);
  view.svg().props.onKeyDown({ key: 'Escape', preventDefault() {} });
  view.render();
  const built = series.buildProbeSeries(data);
  assert.equal(series.snapCursorTime(built, 5 * MINUTE - 1), 0, 'The pointer snaps to a real instant');
});

test('zero is a real value in series, domain, tooltip and summaries', () => {
  const data = readings([probe(1, [raw(0, 0), raw(MINUTE, 0)])]);
  data.target_temperature_c = '0.00';
  const built = series.buildProbeSeries(data)[0];
  assert.equal(built.minimum, 0);
  assert.equal(built.maximum, 0);
  assert.deepEqual({ ...built.latest }, { time: MINUTE, value: 0, condensed: false });
  const domain = series.seriesTemperatureDomain([built], 0);
  assert.ok(domain[0] < 0 && domain[1] > 0);
  const view = renderChart(data, { target: 0, domain });
  const tooltip = view.hover(0);
  assert.match(view.text(tooltip), /0\.0°C/);
  assert.equal(view.find((node) => node.props.className === 'probe-series-target').length, 1);
});

test('missing data remains absent, not zero', () => {
  const built = series.buildProbeSeries(readings([probe(1, [])]))[0];
  assert.equal(built.minimum, null);
  assert.equal(built.maximum, null);
  assert.equal(built.latest, null);
  assert.equal(series.seriesTemperatureDomain([built], null), null);
  const view = renderChart(readings([probe(1, [])]), { domain: null, target: null });
  assert.equal(view.find(() => true, view.groups()[0].props.children).length, 0, 'An empty probe draws nothing');
});

test('pixel reduction preserves real extrema, order and only real points', () => {
  const data = Array.from({ length: 1000 }, (_, index) => [index, index === 500 ? 40 : index === 700 ? -3 : 22 + Math.sin(index)]);
  const reduced = series.reduceReadingsForPixels(data, (time) => time / 50);
  assert.ok(reduced.length < 100);
  assert.ok(reduced.some(([, value]) => value === 40));
  assert.ok(reduced.some(([, value]) => value === -3));
  assert.deepEqual([...reduced[0]], [...data[0]]);
  assert.deepEqual([...reduced.at(-1)], [...data.at(-1)]);
  for (const point of reduced) assert.ok(data.includes(point), 'No synthetic point');
  assert.deepEqual(reduced.map(([time]) => time), reduced.map(([time]) => time).sort((a, b) => a - b));
});

test('coincident readings keep both probes in the tooltip', () => {
  const view = renderChart(readings([steady(1), probe(2, Array.from({ length: 5 }, (_, i) => raw(i * MINUTE, 22.1)))]));
  assert.equal(view.find((node) => node.type === 'span', view.hover(MINUTE)).length, 2);
});

test('condensed series use intervals, keep extrema and are not drawn as instant readings', () => {
  const data = readings([probe(1, [
    interval(0, 15 * MINUTE, 22, 21, 24),
    interval(15 * MINUTE, 30 * MINUTE, 23, 22, 29),
    interval(60 * MINUTE, 75 * MINUTE, 22, 20, 23),
  ])], 15);
  const built = series.buildProbeSeries(data)[0];
  assert.equal(built.readings.length, 0);
  assert.equal(built.intervals.length, 3);
  assert.equal(built.minimum, 20);
  assert.equal(built.maximum, 29);
  assert.equal(built.latest.condensed, true);
  assert.equal(built.latest.time, 75 * MINUTE - MINUTE);
  const window90 = { start: 0, end: 90 * MINUTE };
  const view = renderChart(data, { window: window90, domain: [18, 30] });
  const [mean] = view.find((node) => node.props.className === 'probe-series-line');
  assert.equal(mean.props.d.match(/M/g).length, 2, 'Separate intervals stay disconnected');
  assert.equal(view.find((node) => node.props.className === 'probe-series-band').length, 1);
  assert.equal(view.find((node) => node.type === 'circle').length, 0, 'A bucket mean is not an observation');
  const entry = series.seriesEntryAt(built, 20 * MINUTE);
  assert.equal(entry.kind, 'condensed');
  assert.equal(entry.interval.maximum, 29);
  assert.equal(series.seriesEntryAt(built, 45 * MINUTE), null, 'A gap between intervals has no value');
});

test('condensed tooltips state the interval, its mean and its range compactly, without obsolete provenance text', () => {
  const data = readings([probe(1, [interval(0, 15 * MINUTE, 22, 21, 24)])], 15);
  const hover = renderChart(data, { window: { start: 0, end: 10 * MINUTE }, domain: [18, 30] });
  const tooltip = hover.hover(5 * MINUTE);
  const text = hover.text(tooltip);
  const short = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
  assert.equal(hover.text(hover.find((node) => node.type === 'small', tooltip)[0]), short.formatRange(0, 15 * MINUTE));
  assert.match(text, /P122\.0°C21\.0 – 24\.0°C/);
  assert.doesNotMatch(text, /Mean|Min |Max /, 'Statistic names stay out of the visible tooltip');
  assert.doesNotMatch(text, /Intervalles stockés|Stored intervals/);
});

test('pixel binning of intervals keeps exact extrema and count weights', () => {
  const bins = series.binIntervalsForPixels([
    { start: 0, end: 10, mean: 10, minimum: 5, maximum: 15, count: 1, lastMeasured: 9 },
    { start: 10, end: 20, mean: 20, minimum: 18, maximum: 99, count: 3, lastMeasured: 19 },
    { start: 40, end: 50, mean: 1, minimum: 1, maximum: 1, count: 1, lastMeasured: 49 },
  ], () => 0);
  assert.equal(bins.length, 2, 'A gap inside one column remains a separate interval');
  assert.equal(bins[0].mean, 17.5);
  assert.equal(bins[0].minimum, 5);
  assert.equal(bins[0].maximum, 99);
  assert.equal(bins[0].count, 4);
});

test('the target line uses the provided value and disappears without a target', () => {
  const view = renderChart(readings([steady(1)]));
  const target = view.find((node) => node.props.className === 'probe-series-target')[0];
  const line = view.find((node) => node.type === 'line', target)[0];
  assert.equal(line.props.y1, line.props.y2, 'The target is a horizontal solid line');
  assert.equal(line.props.strokeDasharray, undefined);
  assert.equal(view.text(target), '', 'No label is drawn on the plot next to the curves');
  const key = view.find((node) => node.props.className === 'probe-series-target-key')[0];
  assert.match(view.text(key), /Target 22\.0°C/);
  assert.equal(key.props.y, 8, 'The target is named above the plot area');
  assert.equal(key.props.textAnchor, 'end');
  const none = renderChart(readings([steady(1)]), { target: null });
  assert.equal(none.find((node) => node.props.className === 'probe-series-target').length, 0);
  assert.equal(none.find((node) => node.props.className === 'probe-series-target-key').length, 0);
});

test('curves, condensed means and the target are solid strokes in the stylesheet', () => {
  const css = readFileSync(new URL('../src/styles/components/probe-charts.css', import.meta.url), 'utf8');
  assert.doesNotMatch(css, /dasharray|--series-dash|dashed|dotted|double/);
  const view = renderChart(readings([probe(1, [interval(0, 15 * MINUTE, 22, 21, 24), interval(15 * MINUTE, 30 * MINUTE, 22.5, 22, 23)])], 15));
  for (const node of view.find((child) => child.type === 'path' || child.type === 'line')) {
    assert.equal(node.props.strokeDasharray, undefined);
  }
});

test('tooltip shows the actual timestamp and value; the target stays named above the plot', () => {
  const view = renderChart(readings([probe(1, [raw(2 * MINUTE, 22.6)])]));
  const tooltip = view.hover(2.2 * MINUTE);
  const expected = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }).format(2 * MINUTE);
  assert.equal(view.find((node) => node.type === 'small', tooltip)[0].props.children, expected);
  assert.match(view.text(tooltip), /P122\.6°C/);
  assert.doesNotMatch(view.text(tooltip), /Target/);
  assert.match(view.text(view.find((node) => node.props.className === 'probe-series-target-key')[0]), /Target 22\.0°C/);
});

test('hiding a probe removes its curve and tooltip row but keeps one Y axis', () => {
  const data = readings([steady(1), steady(2)]);
  const view = renderChart(data, { hiddenIds: new Set([2]) });
  assert.equal(view.groups().length, 1);
  assert.equal(view.find((node) => node.type === 'span', view.hover(MINUTE)).length, 1);
});

test('keyboard moves across actual instants, announces them and zooms', () => {
  const zooms = [];
  const view = renderChart(readings([probe(1, [raw(MINUTE, 22), raw(3 * MINUTE, 0)])]), { onZoom: (...args) => zooms.push(args) });
  const key = (name) => { view.svg().props.onKeyDown({ key: name, preventDefault() {} }); view.render(); };
  key('ArrowRight');
  assert.match(view.find((node) => node.props.className === 'sr-only')[0].props.children, /22\.0°C/);
  key('ArrowRight');
  assert.match(view.find((node) => node.props.className === 'sr-only')[0].props.children, /0\.0°C/);
  key('+');
  assert.equal(zooms.length, 1);
  assert.equal(view.svg().props.tabIndex, 0);
  assert.equal(view.svg().props.role, 'img');
  assert.equal(view.svg().props['aria-description'], 'Keys');
});

test('a read-only trend has no zoom gesture', () => {
  const view = renderChart(readings([steady(1)]), { onZoom: undefined, onResetZoom: undefined });
  assert.equal(view.svg().props.className, 'probe-series-svg');
  view.svg().props.onKeyDown({ key: '+', preventDefault() { assert.fail('No zoom on the trend'); } });
});

test('a page-level chart lets vertical swipes scroll and has no wheel capture', () => {
  const css = readFileSync(new URL('../src/styles/components/probe-charts.css', import.meta.url), 'utf8');
  assert.match(css.match(/\.probe-series-svg \{([^}]+)\}/)[1], /touch-action: pan-y/);
  assert.doesNotMatch(readFileSync(new URL('../src/components/ProbeSeriesChart.tsx', import.meta.url), 'utf8'), /'wheel'/);
});

test('recent change is per probe, between its own real readings, with no threshold', () => {
  const changes = series.probeRecentChanges(series.buildProbeSeries(readings([
    probe(1, [raw(0, 22), raw(MINUTE, 22.6)]),
    probe(2, [raw(17_000, 0), raw(617_000, -0.5)], { sampling_interval_seconds: 600 }),
    probe(3, [raw(0, 22)]),
    probe(4, []),
  ])));
  assert.deepEqual(changes.map((change) => ({ ...change })), [
    { id: 1, code: 'P1', delta: 0.6, from: 0, to: MINUTE },
    { id: 2, code: 'P2', delta: -0.5, from: 17_000, to: 617_000 },
  ]);
  const condensed = series.probeRecentChanges(series.buildProbeSeries(readings([probe(1, [interval(0, 15 * MINUTE, 22, 21, 24)])], 15)));
  assert.equal(condensed.length, 0, 'No trend claim from condensed means');
});

test('edge buckets are reported as partial; contained intervals are not', () => {
  const built = series.buildProbeSeries(readings([probe(1, [interval(0, 15 * MINUTE, 22, 21, 24)])], 15));
  assert.equal(series.hasPartialCoverage(built, { start: 5 * MINUTE, end: 30 * MINUTE }), true);
  assert.equal(series.hasPartialCoverage(built, { start: 0, end: 30 * MINUTE }), false);
});

for (const resolution of [1, 15, 60]) {
  test(`server average at ${resolution} minutes preserves periods, zero and contributor counts`, () => {
    const duration = resolution * MINUTE;
    const data = { ...readings([steady(1), steady(2)], resolution),
      probe_average: [[0, 0, 2], [duration, -1.25, 3], [3 * duration, 7, 2]] };
    const average = series.buildProbeAverage(data);
    assert.deepEqual(JSON.parse(JSON.stringify(average)), [
      { start: 0, end: duration, mean: 0, contributors: 2 },
      { start: duration, end: 2 * duration, mean: -1.25, contributors: 3 },
      { start: 3 * duration, end: 4 * duration, mean: 7, contributors: 2 },
    ]);
    const view = renderChart(data, { domain: [-2, 26] });
    const path = view.find((node) => node.props.className === 'probe-series-average probe-series-average-line')[0];
    assert.equal((path.props.d.match(/M/g) ?? []).length, 2, 'A missing period breaks the curve');
    assert.equal((path.props.d.match(/H/g) ?? []).length, 3, 'Every server mean has its own period, not pixel-averaged');
    const tooltip = view.hover(0);
    assert.match(view.text(tooltip), /Probe average0\.0°C/);
    assert.doesNotMatch(view.text(tooltip), /contributing/, 'No contributor sentence in the tooltip');
    const averageRow = view.find((node) => node.type === 'span' && node.props.className === 'probe-series-average', tooltip)[0];
    assert.equal(averageRow.props.title, undefined, 'No period bounds on the average row');
    const line = view.find((node) => node.props.className === 'probe-series-average probe-series-average-line')[0];
    assert.equal(line.props.style['--series-color'], series.averageSeriesColor(series.buildProbeSeries(data)));
    assert.equal(view.groups().length, 2, 'Individual curves remain present');
    assert.equal(view.find((node) => node.props.className === 'probe-series-target').length, 1);
    const domain = series.seriesTemperatureDomain([], null, average);
    assert.ok(domain[0] < -1.25 && domain[1] > 7);
  });
}

test('average does not appear from individual readings or across an absent minute', () => {
  assert.equal(series.buildProbeAverage(readings([steady(1), steady(2)])).length, 0);
  assert.equal(series.buildProbeAverage(null).length, 0);
  const data = { ...readings([]), probe_average: [[0, 0, 2], [2 * MINUTE, 5, 3]] };
  const view = renderChart(data);
  assert.equal(view.hover(MINUTE), undefined, 'No nearest-value fallback for a missing average period');
  const withProbe = renderChart({ ...data, probes: [probe(1, [raw(0, 22)])] });
  assert.ok(!withProbe.text(withProbe.hover(MINUTE)).includes('Probe average'),
    'Snapping to an older individual reading must not fill a missing average period');
  view.svg().props.onKeyDown({ key: 'Escape', preventDefault() {} });
  view.render();
  view.svg().props.onKeyDown({ key: 'ArrowRight', preventDefault() {} });
  view.render();
  assert.match(view.text(view.find((node) => node.props.role === 'status')[0]), /Probe average.*0\.0°C, 2 contributing probes/);
  view.svg().props.onKeyDown({ key: 'ArrowRight', preventDefault() {} });
  view.render();
  assert.match(view.text(view.find((node) => node.props.role === 'status')[0]), /5\.0°C, 3 contributing probes/);
});

// Actual section components, with the data hook controlled.
function zoneCharts({ state = {}, language = 'fr', initial = { settled: true, data: null } } = {}) {
  const copy = language === 'fr' ? fr : en;
  const calls = [];
  const current = { data: null, window: { start: 0, end: 24 * HOUR }, isLoading: true, error: null, retry() { current.retried = (current.retried ?? 0) + 1; }, revalidate() {}, ...state };
  const context = { signal: new AbortController().signal };
  const clock = { end: 24 * HOUR };
  const dates = load('../src/utils/dateFormat.ts', {}, { document: { documentElement: { lang: language } } });
  const stub = () => null;
  const icon = () => null;
  const view_initial = { value: initial };
  // The shared popover hook owns placement, outside click and Escape; here it only records its close callback.
  const popover = { anchorRef: { current: null }, panelRef: { current: null }, onClose: null };
  const zone = { id: 7, target_temperature_c: '22.00', probes: [
    { id: 1, code: 'P1', location: 'Gauche', is_active: true, is_collected: true },
    { id: 2, code: 'P2', location: '', is_active: false, is_collected: true },
    { id: 3, code: 'Manual', location: '', is_active: true, is_collected: false },
  ] };
  const view = render('../src/components/ZoneProbeCharts.tsx', { zone, t(key) { assert.equal(typeof copy[key], 'string', key); return copy[key]; } }, {
    '../api/client': { getOrganizationResourceSignal: () => context.signal },
    'react-dom': { createPortal: (node) => node },
    '../hooks/useAnchoredPopover': { useAnchoredPopover: (isOpen, onClose) => {
      popover.onClose = onClose;
      return { anchorRef: popover.anchorRef, panelRef: popover.panelRef, position: { visibility: isOpen ? 'visible' : 'hidden' }, id: 'range-popover' };
    } },
    '../hooks/useLiveWindowEnd': { default: () => clock.end, useDocumentVisible: () => true },
    '../hooks/useProbeReadings': { default: (zoneId, requested) => {
      calls.push({ zoneId, window: requested });
      return requested === null ? { data: null, window: { start: 0, end: 0 }, isLoading: false, error: null, retry() {} } : current;
    } },
    '../utils/probeReadings': load('../src/utils/probeReadings.ts'), '../utils/probePeriod': load('../src/utils/probePeriod.ts'),
    '../utils/dateFormat': dates, '../utils/environmentFormat': environment,
    '../utils/errors': { getErrorMessage: (error) => error.message },
    '../utils/probeChart': chart, '../utils/probeSeries': series,
    './PolypbaseIcon': { default: icon }, './ProbeSeriesChart': { default: stub, seriesClassName: (slot) => `probe-series-${slot + 1}`, averageSeriesStyle: () => undefined },
  }, { document: { documentElement: { lang: language } } }, (name, next, component) => {
    if (name === 'ZoneProbeChart') return { ...next, chart: component.useZoneChartReadings(next.zone, true) };
    if (name === 'ZoneThermalTrend') return { ...next, initial: view_initial.value };
    return next;
  });
  // The trend starts its fallback from an effect: commit it like React would.
  const renderOnce = view.render;
  view.render = (name, ...rest) => {
    renderOnce(name, ...rest);
    if (name === 'ZoneThermalTrend') { view.flush(); renderOnce(name, ...rest); }
    return view;
  };
  view_initial.value = initial;
  view.initial = view_initial;
  view.clock = clock;
  view.context = context;
  view.zone = zone;
  view.chart = stub;
  view.copy = copy;
  view.calls = calls;
  view.popover = popover;
  view.current = current;
  return view;
}
const payload = readings([steady(1, 3), probe(2, [raw(0, 0)])]);
const section = (view) => view.find((node) => (node.props.className ?? '').includes('zone-probe-chart-section'))[0];

const periodGroup = (view) => view.find((node) => node.props.className === 'zone-probe-chart-range')[0];
const periodButtons = (view) => view.find((node) => node.type === 'button', periodGroup(view));
const rangeButton = (view) => view.find((node) => (node.props.className ?? '').includes('zone-probe-range-button'))[0];
const selectedPeriod = (view) => {
  const pressed = periodButtons(view).filter((node) => node.props['aria-pressed'] === true);
  assert.equal(pressed.length, 1, 'Exactly one period is selected');
  return pressed[0] === rangeButton(view) ? 'custom' : pressed[0].key;
};
const choose = (view, value) => {
  const button = value === 'custom' ? rangeButton(view) : periodButtons(view).find((node) => node.key === value);
  button.props.onClick();
  view.render('ZoneProbeChart');
};

test('the combined chart is on the page by default for the last 2 hours, with no trigger or modal', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
  assert.equal(section(view).type, 'section');
  assert.equal(view.find((node) => node.type === view.chart).length, 1);
  assert.equal(view.find((node) => node.props.role === 'dialog').length, 0);
  assert.deepEqual({ ...view.calls.at(-1).window }, { start: 22 * HOUR, end: 24 * HOUR });
  assert.equal(view.calls.at(-1).zoneId, 7);
  assert.equal(periodButtons(view).filter((node) => node.props['aria-pressed'] === true).length, 0, 'The initial two-hour window is distinct from the period presets');
  assert.equal(view.text(rangeButton(view)), view.copy.probePeriodCustom, 'A rolling window needs no long date string');
  const visible = periodButtons(view).find((node) => node.key === '24');
  assert.equal(view.text(visible), view.copy.probePeriod_24h);
  assert.equal(visible.props['aria-label'], view.copy.probeRangeReset);
});

for (const [language, prefix, options, names] of [
  ['fr', 'Modifier la période : ', ['24 h', '7 jours', '30 jours', 'Dates précises'], ['Dernières 24 h', '7 derniers jours', '30 derniers jours', 'Dates précises']],
  ['en', 'Change the period: ', ['24 h', '7 days', '30 days', 'Exact dates'], ['Last 24 h', 'Last 7 days', 'Last 30 days', 'Exact dates']],
]) {
  test(`${language}: one segmented period control switches in one click and names exact periods`, () => {
    const view = zoneCharts({ language, state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
    const group = periodGroup(view);
    assert.equal(group.props.role, 'group');
    assert.equal(group.props['aria-label'], view.copy.probePeriod);
    assert.deepEqual(periodButtons(view).map((node) => view.text(node)), options);
    assert.deepEqual(periodButtons(view).map((node) => node.props['aria-label']), names);
    // Exact bounds and time zone stay available to assistive technologies and on hover.
    const description = view.find((node) => node.props.id === group.props['aria-describedby'])[0];
    const format = new Intl.DateTimeFormat(language === 'fr' ? 'fr-FR' : 'en-GB', { dateStyle: 'medium', timeStyle: 'short' });
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const exact = `${format.format(0)} → ${format.format(24 * HOUR)} (${zone})`;
    assert.equal(view.text(description), exact);
    assert.equal(rangeButton(view).props.title, exact);

    choose(view, '168');
    assert.equal(view.calls.at(-1).window.end - view.calls.at(-1).window.start, 7 * 24 * HOUR);
    assert.equal(view.calls.at(-1).window.end, 24 * HOUR, 'A relative period ends at the live end');
    assert.equal(selectedPeriod(view), '168');
    assert.equal(view.find((node) => node.type === 'form').length, 0, 'A relative period applies in one step');

    choose(view, 'custom');
    assert.equal(rangeButton(view).props['aria-expanded'], true);
    assert.equal(view.find((node) => node.props.className === 'zone-probe-range-form').length, 1);
    const inputs = view.find((node) => node.type === 'input');
    assert.equal(inputs.length, 2);
    assert.ok(inputs.every((node) => node.props.type === 'datetime-local' && node.props.value));
    assert.ok(!view.text().includes(view.copy.probeTimeZone.replace('{zone}', zone)), 'No time zone sentence in the editor');
    view.find((node) => node.type === 'form')[0].props.onSubmit({ preventDefault() {} });
    view.render('ZoneProbeChart');
    assert.equal(selectedPeriod(view), 'custom');
    const button = rangeButton(view);
    const label = button.props['aria-label'];
    assert.ok(label.startsWith(prefix));
    assert.doesNotMatch(label, /\{range\}/);
    assert.ok(label.endsWith(`(${zone})`), 'The accessible name carries the time zone');
    assert.ok(view.text(button).length < label.length - prefix.length, 'The visible label is shorter than the exact bounds');

    choose(view, '24');
    assert.equal(selectedPeriod(view), '24', 'One click returns to the rolling window');
    assert.equal(view.text(rangeButton(view)), view.copy.probePeriodCustom);
  });
}

test('Escape closes the exact dates editor and keeps the current period', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
  choose(view, '720');
  choose(view, 'custom');
  const popover = view.find((node) => node.props.role === 'dialog')[0];
  assert.equal(popover.props.id, rangeButton(view).props['aria-controls']);
  assert.match(popover.props.className, /anchored-popover/, 'Exact dates open in the shared small popover');
  view.popover.onClose(true);
  view.render('ZoneProbeChart');
  assert.equal(view.find((node) => node.type === 'form').length, 0);
  assert.equal(selectedPeriod(view), '720');
});

test('obsolete arrows, modal and provenance copy are gone from the page', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
  const text = view.text();
  assert.equal(view.find((node) => node.props.className === 'chart-periods').length, 0);
  for (const key of ['probePeriod_90d', 'probeWindowPrevious', 'probeWindowNext', 'probeAggregateCoverage', 'probeReadingsAction']) {
    assert.ok(!text.includes(view.copy[key]), key);
  }
  for (const source of ['ZoneProbeCharts.tsx', 'ProbeSeriesChart.tsx', 'ZonesView.tsx']) {
    const code = readFileSync(new URL(`../src/components/${source}`, import.meta.url), 'utf8');
    assert.doesNotMatch(code, /probePeriod_90d|probeWindowPrevious|probeWindowNext|probeAggregateCoverage|ProbeReadingsModal/, source);
  }
});

test('loading keeps the frame and busy state, without claiming there is no data', () => {
  const view = zoneCharts().render('ZoneProbeChart');
  assert.equal(section(view).props['aria-busy'], true);
  assert.equal(view.find((node) => node.type === view.chart).length, 1);
  assert.ok(!view.text().includes(view.copy.probeNoReadings));
  assert.equal(view.find((node) => node.type === 'tr').length, 3, 'Collected probes are listed while loading, manual ones are not');
});

test('empty is distinct from loading and error; zero is not empty', () => {
  const empty = zoneCharts({ state: { data: readings([probe(1, [])]), isLoading: false } }).render('ZoneProbeChart');
  assert.ok(empty.text().includes(empty.copy.probeNoReadings));
  const zero = zoneCharts({ state: { data: readings([probe(1, [raw(0, 0)])]), isLoading: false } }).render('ZoneProbeChart');
  assert.ok(!zero.text().includes(zero.copy.probeNoReadings));
  assert.match(zero.text(), /0\.0°C/);
});

test('error reports and retries without blanking retained data', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false, error: new Error('Offline') } }).render('ZoneProbeChart');
  const status = view.find((node) => node.props.className === 'probe-series-status')[0];
  assert.equal(status.props.role, 'status');
  assert.match(view.text(status), /Offline/);
  view.find((node) => node.type === 'button', status)[0].props.onClick();
  assert.equal(view.current.retried, 1);
  assert.ok(!view.text().includes(view.copy.probeNoReadings));
  assert.equal(view.find((node) => node.type === view.chart).length, 1);
});

test('an organization change that clears the hook shows no previous measurement', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
  assert.match(view.text(), /22\.1°C/);
  Object.assign(view.current, { data: null, isLoading: false });
  view.render('ZoneProbeChart');
  assert.doesNotMatch(view.text(), /22\.1°C/);
});

test('the legend identifies probes, distinguishes inactive ones and last values with real times', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
  const rows = view.find((node) => node.type === 'tr').slice(1);
  assert.equal(rows.length, 2);
  assert.match(view.text(rows[0]), /P1/);
  assert.equal(view.find((node) => node.type === 'time', rows[0])[0].props.dateTime, new Date(2 * MINUTE).toISOString());
  const toggle = view.find((node) => node.type === 'button', rows[0])[0];
  assert.equal(toggle.props['aria-pressed'], true);
  toggle.props.onClick();
  view.render('ZoneProbeChart');
  assert.equal(view.find((node) => node.type === view.chart)[0].props.hiddenIds.has(1), true);
  assert.equal(view.find((node) => node.type === 'button', view.find((node) => node.type === 'tr')[1])[0].props['aria-pressed'], false);
});

for (const language of ['fr', 'en']) {
  test(`${language}: server average legend shows only its value and stays when individual curves are hidden`, () => {
    const data = { ...payload, probe_average: [[0, 0, 2]] };
    const view = zoneCharts({ language, state: { data, isLoading: false } }).render('ZoneProbeChart');
    const row = view.find((node) => node.type === 'tr')[1];
    assert.ok(view.text(row).includes(view.copy.probeAverage));
    assert.ok(!view.text(row).includes(view.copy.probeAverageContributors.replace('{count}', '2')), 'No contributor sentence');
    assert.match(view.text(row), /0\.0°C/);
    assert.equal(view.find((node) => node.type === 'time', row).length, 0, 'No period bounds next to the average');
    const legend = view.find((node) => node.props.className === 'probe-series-legend')[0];
    for (const button of view.find((node) => node.type === 'button' && node.props['aria-pressed'] === true, legend)) button.props.onClick();
    view.render('ZoneProbeChart');
    const props = view.find((node) => node.type === view.chart)[0].props;
    assert.equal(props.average[0].mean, 0);
    assert.equal(props.average[0].contributors, 2);
    assert.ok(props.hiddenIds.has(1) && props.hiddenIds.has(2));
    Object.assign(view.current, { data: null, isLoading: true });
    view.context.signal = new AbortController().signal;
    view.render('ZoneProbeChart');
    assert.equal(view.find((node) => node.type === view.chart)[0].props.average.length, 0);
    assert.ok(!view.text().includes(view.copy.probeAverage), 'No old average after context clears');
  });
}

test('a condensed window labels aggregates in the legend, without explanatory notes', () => {
  // A window narrower than its buckets: partial buckets are drawn without a note.
  const view = zoneCharts({ state: { data: readings([probe(1, [interval(0, 15 * MINUTE, 22, 21, 24)])], 15), isLoading: false,
    window: { start: 5 * MINUTE, end: 10 * MINUTE } } }).render('ZoneProbeChart');
  assert.ok(view.text().includes(view.copy.probeLatestInterval.replace('{minutes}', '15')));
  assert.ok(!view.text().includes(view.copy.probeCondensedNotice.replace('{minutes}', '15')));
  assert.ok(!view.text().includes(view.copy.probePartialCoverage));
  assert.equal(view.find((node) => node.props.className === 'zone-probe-chart-note').length, 0);
});

test('a partial preview of a selected period shows no coverage sentence', () => {
  const view = zoneCharts({ state: { data: { ...payload, coverage_complete: false }, isLoading: false } }).render('ZoneProbeChart');
  choose(view, '168');
  assert.ok(!view.text().includes(view.copy.probeCoveragePreview));
});

test('date range editing validates, applies and returns to the rolling window', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
  choose(view, 'custom');
  const inputs = () => view.find((node) => node.type === 'input');
  assert.equal(inputs().length, 2);
  assert.ok(inputs().every((node) => node.props.type === 'datetime-local'));
  inputs()[1].props.onChange({ target: { value: '2000-01-01T00:00' } });
  inputs()[0].props.onChange({ target: { value: '2000-01-02T00:00' } });
  view.render('ZoneProbeChart');
  const form = () => view.find((node) => node.type === 'form')[0];
  form().props.onSubmit({ preventDefault() {} });
  view.render('ZoneProbeChart');
  assert.equal(view.find((node) => node.props.role === 'alert').length, 1);
  inputs()[0].props.onChange({ target: { value: '2000-01-01T00:00' } });
  inputs()[1].props.onChange({ target: { value: '2000-01-02T00:00' } });
  view.render('ZoneProbeChart');
  form().props.onSubmit({ preventDefault() {} });
  view.render('ZoneProbeChart');
  assert.equal(view.calls.at(-1).window.end - view.calls.at(-1).window.start, 24 * HOUR);
  assert.equal(view.calls.at(-1).window.start, new Date('2000-01-01T00:00').getTime());
  // A whole local day is named once, without repeated times.
  assert.equal(view.text(rangeButton(view)),
    new Intl.DateTimeFormat('fr-FR', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date('2000-01-01T00:00')));
  choose(view, '24');
  assert.deepEqual({ ...view.calls.at(-1).window }, { start: 0, end: 24 * HOUR });
  assert.equal(view.text(rangeButton(view)), view.copy.probePeriodCustom);
});

test('cancelling the exact dates editor keeps the current period', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
  choose(view, '720');
  choose(view, 'custom');
  assert.equal(selectedPeriod(view), 'custom', 'The control reflects the open editor');
  view.find((node) => node.type === 'button' && node.props.children === view.copy.cancel)[0].props.onClick();
  view.render('ZoneProbeChart');
  assert.equal(selectedPeriod(view), '720');
  assert.equal(view.calls.at(-1).window.end - view.calls.at(-1).window.start, 30 * 24 * HOUR);
});

test('range parsing keeps the bounds the server accepts', () => {
  const now = new Date('2026-11-09T12:00').getTime();
  const { parseRangeInputs } = load('../src/components/ZoneProbeCharts.tsx', {
    react: {}, 'react/jsx-runtime': jsxRuntime, 'react-dom': {}, '../api/client': {}, '../hooks/useAnchoredPopover': {},
    '../hooks/useLiveWindowEnd': {}, '../hooks/useProbeReadings': {},
    '../utils/probeReadings': {}, '../utils/probePeriod': {},
    '../utils/dateFormat': {}, '../utils/environmentFormat': {}, '../utils/errors': {}, '../utils/probeChart': chart, '../utils/probeSeries': series,
    './PolypbaseIcon': {}, './ProbeSeriesChart': {},
  });
  assert.equal(parseRangeInputs('', '2026-11-09T10:00', now), null);
  assert.equal(parseRangeInputs('2026-11-09T11:00', '2026-11-09T10:00', now), null);
  assert.equal(parseRangeInputs('2026-11-09T10:00', '2026-11-09T10:10', now), null, 'Below the 30-minute minimum');
  assert.equal(parseRangeInputs('2026-11-09T10:00', '2026-11-09T13:00', now), null, 'Future end');
  assert.equal(parseRangeInputs('2025-01-01T00:00', '2026-11-09T10:00', now), null, 'Beyond 366 days');
  assert.deepEqual({ ...parseRangeInputs('2026-11-08T10:00', '2026-11-09T10:00', now) },
    { start: new Date('2026-11-08T10:00').getTime(), end: new Date('2026-11-09T10:00').getTime() });
});

test('the trend renders a compact chart without the removed per-probe changes' , () => {
  const view = zoneCharts({ state: { data: readings([probe(1, [raw(0, 22), raw(MINUTE, 22.6)])]), isLoading: false, window: { start: 0, end: 6 * HOUR } } }).render('ZoneThermalTrend');
  const props = view.find((node) => node.type === view.chart)[0].props;
  assert.equal(props.onZoom, undefined);
  assert.ok(props.height < 200);
  assert.equal(props.target, 22);
  assert.equal(view.find((node) => node.props.className === 'zone-thermal-changes').length, 0);
  assert.equal(view.find((node) => /danger|alert/.test(node.props.className ?? '')).length, 0);
  assert.equal(view.calls.at(-1).window.end - view.calls.at(-1).window.start, 6 * HOUR);
});

test('the trend reports errors and an empty window', () => {
  const failed = zoneCharts({ state: { data: null, isLoading: false, error: new Error('Offline') } }).render('ZoneThermalTrend');
  assert.match(failed.text(), /Offline/);
  const empty = zoneCharts({ state: { data: readings([probe(1, [])]), isLoading: false } }).render('ZoneThermalTrend');
  assert.ok(empty.text().includes(empty.copy.probeNoReadings));
  assert.equal(empty.find((node) => node.props.className === 'zone-thermal-changes').length, 0);
});

for (const [name, catalog] of [['fr', fr], ['en', en]]) {
  test(`${name}: new chart copy exists, keeps placeholders and avoids the middle dot`, () => {
    for (const [key, placeholder] of [['temperatureTrendTitle', '{hours}'], ['probeRangeEdit', '{range}'], ['probeCondensedNotice', '{minutes}'], ['probeSeriesToggle', '{probe}']]) {
      assert.ok(catalog[key].includes(placeholder), key);
    }
    for (const key of ['probeIntervalMean', 'probeCombinedChartKeyboard', 'probeRangeStart', 'probeRangeEnd', 'probeRangeApply', 'probeRangeReset', 'probeRangeInvalid', 'probeLegendProbe', 'probeLatestInterval']) {
      assert.equal(typeof catalog[key], 'string', key);
      assert.doesNotMatch(catalog[key], /·/);
    }
  });
}

test('the rolling window advances on schedule, pauses when disabled and catches up on return', () => {
  let now = 1_000_000;
  let end;
  let effect;
  const intervals = [];
  const documentStub = { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} };
  const stubDate = class extends Date { static now() { return now; } };
  const module = load('../src/hooks/useLiveWindowEnd.ts', { react: {
    useState: (initial) => { end ??= initial(); return [end, (next) => { end = typeof next === 'function' ? next(end) : next; }]; },
    useEffect: (run) => { effect = run; },
  } }, {
    Date: stubDate, document: documentStub,
    setInterval: (fn, delay) => { intervals.push({ fn, delay }); return intervals.length; }, clearInterval() {},
    addEventListener() {}, removeEventListener() {},
  });
  module.default(60_000, true);
  const cleanup = effect();
  assert.equal(intervals[0].delay, 60_000);
  now += 60_000;
  intervals[0].fn();
  assert.equal(end, 1_060_000);
  documentStub.visibilityState = 'hidden';
  now += 60_000;
  intervals[0].fn();
  assert.equal(end, 1_060_000, 'No refresh while the page is hidden');
  documentStub.visibilityState = 'visible';
  cleanup();
  now += 3_600_000;
  module.default(60_000, true);
  effect();
  assert.equal(end, now, 'Returning to the rolling window must not reuse an old end');
});

const liveDay = (overrides = {}) => ({
  ...readings([steady(1, 3)]), start: new Date(0).toISOString(), end: new Date(24 * HOUR).toISOString(), ...overrides,
});

test('the trend reuses the first chart response instead of requesting six hours', () => {
  const recent = probe(1, [raw(18 * HOUR, 22), raw(23 * HOUR, 22.6)]);
  const view = zoneCharts({ initial: { settled: true, data: liveDay({ probes: [recent] }) } }).render('ZoneThermalTrend');
  assert.ok(view.calls.every((call) => call.window === null), 'No own request at opening');
  assert.equal(view.find((node) => node.props.className === 'zone-thermal-changes').length, 0);
  const chart = view.find((node) => node.type === view.chart)[0].props;
  assert.deepEqual({ ...chart.window }, { start: 18 * HOUR, end: 24 * HOUR });
});

test('the reused six-hour window excludes older readings and keeps zero', () => {
  const data = liveDay({ probes: [probe(1, [raw(HOUR, 99), raw(19 * HOUR, 0), raw(20 * HOUR, 0)])] });
  const view = zoneCharts({ initial: { settled: true, data } }).render('ZoneThermalTrend');
  const chart = view.find((node) => node.type === view.chart)[0].props;
  assert.deepEqual([...chart.series[0].readings.map((reading) => reading[1])], [0, 0]);
  assert.equal(chart.series[0].minimum, 0);
});

test('the trend waits for the first chart response without claiming there is no data', () => {
  const view = zoneCharts({ initial: { settled: false, data: null } }).render('ZoneThermalTrend');
  assert.ok(view.calls.every((call) => call.window === null));
  assert.ok(!view.text().includes(view.copy.probeNoReadings));
  assert.equal(view.find((node) => node.props.className === 'zone-thermal-trend')[0].props['aria-busy'], true);
});

test('the trend refreshes alone after its first interval, keeping the reused data meanwhile', () => {
  const recent = probe(1, [raw(18 * HOUR, 22), raw(23 * HOUR, 22.6)]);
  const view = zoneCharts({ state: { data: null, isLoading: true }, initial: { settled: true, data: liveDay({ probes: [recent] }) } }).render('ZoneThermalTrend');
  view.clock.end += 60_000;
  view.render('ZoneThermalTrend');
  assert.deepEqual({ ...view.calls.at(-1).window }, { start: view.clock.end - 6 * HOUR, end: view.clock.end });
  assert.equal(view.find((node) => node.type === view.chart)[0].props.series[0].readings.length, 2);
});

for (const [name, initial] of [
  ['condensed', { settled: true, data: liveDay({ resolution_minutes: 15, probes: [probe(1, [interval(0, 15 * MINUTE, 22, 21, 24)])] }) }],
  ['shorter than six hours', { settled: true, data: liveDay({ start: new Date(22 * HOUR).toISOString() }) }],
  ['failed', { settled: true, data: null }],
]) {
  test(`a ${name} first response makes the trend read for itself immediately`, () => {
    const view = zoneCharts({ state: { data: null, isLoading: true }, initial }).render('ZoneThermalTrend');
    assert.equal(view.calls.at(-1).window.end - view.calls.at(-1).window.start, 6 * HOUR);
  });
}

test('trend errors stay independent from the chart and offer their own retry', () => {
  const view = zoneCharts({ state: { data: null, isLoading: false, error: new Error('Trend offline') } }).render('ZoneThermalTrend');
  assert.match(view.text(view.find((node) => node.props.className === 'probe-series-status')[0]), /Trend offline/);
});

test('opening a location starts exactly one series read, for the chart window', () => {
  const view = zoneCharts().render('ZoneProbeChart');
  assert.deepEqual(view.calls.map((call) => ({ ...call.window })), [{ start: 22 * HOUR, end: 24 * HOUR }]);
});

test('the first live response settles once and is kept when the user changes the range', () => {
  const data = liveDay({ zone_id: 7 });
  const view = zoneCharts({ state: { data: null, isLoading: true } });
  const results = [];
  const original = view.component.useZoneChartReadings;
  view.component.useZoneChartReadings = (...args) => { const value = original(...args); results.push(value); return value; };
  view.render('ZoneProbeChart');
  assert.deepEqual({ settled: results.at(-1).initial.settled, data: results.at(-1).initial.data }, { settled: false, data: null });
  view.current.data = data;
  view.current.isLoading = false;
  view.render('ZoneProbeChart');
  view.flush();
  view.render('ZoneProbeChart');
  assert.equal(results.at(-1).initial.settled, true);
  assert.equal(results.at(-1).initial.data, data);
  results.at(-1).setCustom({ start: 0, end: HOUR });
  view.current.data = liveDay({ zone_id: 7, resolution_minutes: 15 });
  view.render('ZoneProbeChart');
  view.flush();
  view.render('ZoneProbeChart');
  assert.equal(results.at(-1).initial.data, data, 'A custom range never replaces the live seed');
});

test('a failed first read settles without data, and a new organization context discards the seed', () => {
  const data = liveDay({ zone_id: 7 });
  const view = zoneCharts({ state: { data: null, isLoading: false, error: new Error('Offline') } });
  const results = [];
  const original = view.component.useZoneChartReadings;
  view.component.useZoneChartReadings = (...args) => { const value = original(...args); results.push(value); return value; };
  view.render('ZoneProbeChart');
  view.flush();
  view.render('ZoneProbeChart');
  assert.deepEqual({ settled: results.at(-1).initial.settled, data: results.at(-1).initial.data }, { settled: true, data: null });
  view.context.signal = new AbortController().signal;
  Object.assign(view.current, { data, error: null });
  view.render('ZoneProbeChart');
  view.flush();
  view.render('ZoneProbeChart');
  assert.equal(results.at(-1).initial.data, data);
  view.context.signal = new AbortController().signal;
  Object.assign(view.current, { data: null, isLoading: true });
  view.render('ZoneProbeChart');
  assert.deepEqual({ settled: results.at(-1).initial.settled, data: results.at(-1).initial.data }, { settled: false, data: null });
});

test('a location without a collected probe requests no series', () => {
  const view = zoneCharts();
  const result = view.component.useZoneChartReadings(view.zone, false);
  assert.deepEqual(view.calls.map((call) => call.window), [null]);
  assert.equal(result.initial.settled, true);
  assert.equal(result.initial.data, null);
});

test('date selection has no data-derived lower bound and still reaches old periods', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
  choose(view, 'custom');
  const inputs = () => view.find((node) => node.type === 'input');
  assert.ok(inputs().every((node) => node.props.min === undefined));
  assert.ok(inputs().every((node) => typeof node.props.max === 'string'));
  inputs()[0].props.onChange({ target: { value: '2024-03-01T00:00' } });
  inputs()[1].props.onChange({ target: { value: '2024-03-08T00:00' } });
  view.render('ZoneProbeChart');
  view.find((node) => node.type === 'form')[0].props.onSubmit({ preventDefault() {} });
  view.render('ZoneProbeChart');
  assert.equal(view.calls.at(-1).window.start, new Date('2024-03-01T00:00').getTime());
  assert.equal(view.find((node) => node.props.role === 'alert').length, 0);
  // The button reopens the editor on the exact bounds already shown.
  rangeButton(view).props.onClick();
  view.render('ZoneProbeChart');
  assert.deepEqual(view.find((node) => node.type === 'input').map((node) => node.props.value), ['2024-03-01T00:00', '2024-03-08T00:00']);
});

test('opening the range editor focuses the popover, not a date field, and closing returns to the period control', () => {
  const view = zoneCharts({ state: { data: payload, isLoading: false } }).render('ZoneProbeChart');
  const focused = [];
  view.popover.panelRef.current = { focus() { focused.push('popover'); } };
  view.popover.anchorRef.current = { focus() { focused.push('period'); } };
  choose(view, 'custom');
  view.flush();
  assert.deepEqual(focused, ['popover'], 'Focusing a date input would preselect its first segment');
  assert.equal(view.find((node) => node.props.role === 'dialog')[0].props.tabIndex, -1);
  assert.ok(view.find((node) => node.type === 'input').every((node) => node.ref == null));
  view.find((node) => node.type === 'button' && node.props.children === view.copy.cancel)[0].props.onClick();
  view.render('ZoneProbeChart');
  assert.deepEqual(focused, ['popover', 'period']);
});

test('chart controls stop above the fixed phone navigation without changing it', () => {
  const css = readFileSync(new URL('../src/styles/pages/zones.css', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
  const rule = css.match(/([^{}]*\.zone-probe-range-button,[^{}]*)\{\s*scroll-margin-bottom: var\(--phone-nav-clearance, 0px\);/);
  assert.ok(rule, 'The range controls reserve the navigation height');
  for (const selector of ['.zone-probe-period-option', '.zone-probe-range-form :is(input, button)', '.probe-series-legend button', '.probe-series-svg']) {
    assert.ok(rule[1].includes(selector), selector);
  }
  assert.match(readFileSync(new URL('../src/styles/responsive/phone.css', import.meta.url), 'utf8'), /--phone-nav-clearance: calc\(100px \+ env\(safe-area-inset-bottom\)\)/);
});

test('the gap rule is documented as a display assumption, not as freshness', () => {
  const source = readFileSync(new URL('../src/utils/probeSeries.ts', import.meta.url), 'utf8');
  assert.match(source, /Display assumption of this chart, not a scientific rule/);
  assert.doesNotMatch(source, /validated freshness contract/);
});
