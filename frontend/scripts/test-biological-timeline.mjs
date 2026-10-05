import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import * as scales from 'd3-scale';
import * as shapes from 'd3-shape';

function load(path, dependencies = {}) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, Date, require(name) {
    assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
    return dependencies[name];
  } });
  return exports;
}
const timeline = load('../src/utils/biologicalTimeline.ts');
const utility = (name) => load(`../src/utils/${name}.ts`);
const fr = load('../src/i18n/fr.ts').fr;
const en = load('../src/i18n/en.ts').en;
const reading = (id, date, polyps, ephyrae = 0) => ({ id, measured_on: date, polyp_count: polyps, ephyrae_count: ephyrae, salinity_psu: null, notes: '', user: 'Scientist' });
const measurement = reading(1, '2026-09-15', 100);
const parentEntry = {
  kind: 'subculture', id: 1, identity: 'subculture:1', effective_date: '2026-09-15', state_sequence: 2,
  polyp_count_before: 100, polyp_count_after: 0, allocated_polyps: 100, author: { username: 'Scientist' },
  allocations: [
    { child_box_id: 3, child_global_code: 'SF.003', allocated_polyps: 60, position: 2 },
    { child_box_id: 2, child_global_code: 'SF.002', allocated_polyps: 40, position: 1 },
  ],
};
const measurementEntry = { kind: 'measurement', id: 1, identity: 'measurement:1', effective_date: measurement.measured_on, state_sequence: 1, measurement };
const childEntry = { kind: 'subculture_initialization', id: 1, identity: 'subculture_initialization:1', event_id: 1, effective_date: '2026-09-15', state_sequence: 1, polyp_count_after: 0, allocated_polyps: 0, parent: { id: 99, global_code: 'SF.001' } };
const translator = (catalog, language) => (key) => key === 'subcultureAllocatedPolyps' ? (language === 'fr' ? 'Polypes alloués' : 'Allocated polyps') : catalog[key] ?? key;
const labelsFor = (catalog, language) => timeline.getBiologicalTimelineLabels(translator(catalog, language));
const plain = (value) => JSON.parse(JSON.stringify(value));

function chartHarness() {
  const states = [];
  let stateIndex = 0;
  const hooks = {
    useMemo: (fn) => fn(), useRef: () => ({ current: null }), useId: () => 'chart-test',
    useEffect() {}, useLayoutEffect() {},
    useState(initial) {
      const index = stateIndex++;
      if (!(index in states)) states[index] = initial;
      return [states[index], (next) => { states[index] = typeof next === 'function' ? next(states[index]) : next; }];
    },
  };
  const chart = load('../src/components/BiologicalTrendChart.tsx', {
    react: hooks, 'react/jsx-runtime': jsxRuntime, 'd3-scale': scales, 'd3-shape': shapes,
    '../utils/userIdentity': utility('userIdentity'),
    '../utils/dateFormat': utility('dateFormat'), '../utils/chartBiology': utility('chartBiology'), '../utils/chartLocations': utility('chartLocations'),
  });
  return { chart, render(props) { stateIndex = 0; return chart.default(props); } };
}
function allNodes(node) {
  if (!node || typeof node !== 'object') return [];
  return [node, ...[node.props?.children].flat(Infinity).flatMap(allNodes)];
}
const chartLabels = (catalog) => ({ chartTitle: catalog.chartTitle, empty: catalog.chartEmpty, polyps: catalog.polyps, ephyrae: catalog.ephyraeFull, location: catalog.confirmDetailCurrentLocation, missingReading: catalog.chartMissingReading, salinity: catalog.salinityFull, subculture: catalog.subcultureEvent });

test('ordinary consumers retain their measurements without a new timeline', () => {
  const data = timeline.prepareBiologicalChartData([measurement], undefined, labelsFor(fr, 'fr'));
  assert.equal(data.measurements.length, 1);
  assert.equal(data.measurements[0].ephyraeCount, 0);
  assert.equal(data.polypStates.length, 0);
  assert.equal(data.events.length, 0);
});

test('a multi-child parent event yields one absolute state and one lifecycle marker', () => {
  const data = timeline.prepareBiologicalChartData([measurement], [parentEntry, measurementEntry, parentEntry], labelsFor(fr, 'fr'));
  assert.equal(data.measurements.length, 1);
  assert.equal(data.polypStates.length, 1);
  assert.equal(data.events.length, 1);
  assert.equal(data.polypStates[0].polypCount, 0);
  assert.equal(data.polypStates[0].ephyraeCount, undefined);
  assert.equal(data.polypStates[0].salinity, undefined);
  assert.deepEqual(plain(data.polypStates[0].detailLines), [
    { label: 'Boîte parent', value: '100 → 0 Polypes' },
    { label: 'Polypes alloués', value: '100' },
    { label: 'Boîtes enfants', value: 'SF.002: 40, SF.003: 60' },
  ]);
});

test('child initialization retains allocated zero and its parent code without inventing measurements', () => {
  const data = timeline.prepareBiologicalChartData([], [childEntry], labelsFor(en, 'en'));
  assert.equal(data.measurements.length, 0);
  assert.equal(data.polypStates[0].kind, 'subculture_initialization');
  assert.equal(data.polypStates[0].polypCount, 0);
  assert.equal(data.polypStates[0].detailLines[0].value, '0');
  assert.equal(data.polypStates[0].detailLines[1].value, 'SF.001');
});

test('legacy unknown balances are markers, never fake zero points', () => {
  const legacy = { kind: 'subculture', id: 2, effective_date: '2026-09-15', polyp_count_before: null, polyp_count_after: null, allocated_polyps: null, children: [{ id: 4, global_code: 'SF.004' }] };
  const data = timeline.prepareBiologicalChartData([], [legacy], labelsFor(fr, 'fr'));
  assert.equal(data.polypStates.length, 0);
  assert.equal(data.events.length, 1);
  assert.deepEqual(plain(data.events[0].detailLines), [{ label: 'Boîtes enfants', value: 'SF.004' }]);
});

for (const [language, catalog] of [['fr', fr], ['en', en]]) {
  test(`${language}: partial allocations remain an event, not a quantitative graph point`, () => {
    const partial = { ...parentEntry, polyp_count_after: null, allocated_polyps: null,
      allocations: [
        { child_box_id: 2, child_global_code: 'SF.002', allocated_polyps: 0, position: 0 },
        { child_box_id: 3, child_global_code: 'SF.003', allocated_polyps: null, position: 1 },
      ],
    };
    const data = timeline.prepareBiologicalChartData([measurement], [measurementEntry, partial], labelsFor(catalog, language));
    assert.equal(data.measurements[0].polypCount, 100);
    assert.equal(data.polypStates.length, 0);
    assert.equal(data.events.length, 1);
    assert.deepEqual(plain(data.events[0].detailLines), [{
      label: catalog.confirmDetailChildren,
      value: `SF.002: 0, SF.003: ${catalog.subcultureUnknown}`,
    }]);
    const { chart } = chartHarness();
    const geometry = chart.buildGeometry(data.measurements, [], [], '2026-09-01', '2026-09-30', false, undefined, data.polypStates);
    assert.deepEqual(plain(geometry.plottedMeasurements.map(point => point.polypCount)), [100]);
  });
}

test('same-day identities and server state sequence remain distinct and correctly ordered', () => {
  const data = timeline.prepareBiologicalChartData([measurement], [parentEntry, measurementEntry], labelsFor(fr, 'fr'));
  const { chart } = chartHarness();
  const result = chart.buildGeometry(data.measurements, [], [], '2026-09-01', '2026-09-30', false, undefined, data.polypStates);
  assert.deepEqual(plain(result.plottedMeasurements.map((point) => point.polypCount)), [100, 0]);
  assert.notEqual(chart.getTrendPointId(result.plottedMeasurements[0]), chart.getTrendPointId(result.plottedMeasurements[1]));
  assert.equal(result.measurementSegments.flat().length, 1);
  assert.equal(result.polypSegments.flat().length, 2);
  assert.equal(result.yCount(0), result.countHeight - result.padding.bottom);
  assert.equal(result.countLine((point) => point.ephyraeCount)(data.polypStates), null);
});

for (const [language, catalog] of [['fr', fr], ['en', en]]) {
  test(`${language.toUpperCase()} chart exposes the subculture point and allocations by focus, pinning and Escape`, () => {
    const data = timeline.prepareBiologicalChartData([measurement], [parentEntry, measurementEntry], labelsFor(catalog, language));
    const harness = chartHarness();
    const props = { ...data, events: [], startDate: '2026-09-01', endDate: '2026-09-30', labels: chartLabels(catalog) };
    const tree = harness.render(props);
    const point = allNodes(tree).find((node) => node.props?.className === 'bio-trend-measurement is-subculture');
    assert.ok(point);
    assert.match(point.props['aria-label'], new RegExp(catalog.subcultureEvent));
    const markers = allNodes(point).filter((node) => node.props?.className?.includes('bio-trend-dot'));
    assert.equal(markers.length, 1);
    assert.equal(markers[0].type, 'rect');
    point.props.onFocus();
    let markup = renderToStaticMarkup(harness.render(props));
    assert.match(markup, /100 → 0/);
    assert.match(markup, /SF\.002: 40, SF\.003: 60/);
    assert.doesNotMatch(markup, /undefined|NaN|measurement.*edit|Scientist.*salinity/);
    let prevented = false;
    point.props.onKeyDown({ key: 'Enter', preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
    markup = renderToStaticMarkup(harness.render(props));
    assert.match(markup, /bio-trend-tooltip is-pinned/);
    point.props.onBlur();
    point.props.onKeyDown({ key: 'Escape', preventDefault() {} });
    assert.doesNotMatch(renderToStaticMarkup(harness.render(props)), /bio-trend-tooltip/);
  });
}

test('timeline points respect gaps and chart windows without changing overflow behavior', () => {
  const { chart } = chartHarness();
  const state = { id: 1, kind: 'subculture', title: 'Subculture', date: '2026-09-15', polypCount: 1200 };
  const result = chart.buildGeometry([], [], [], '2026-09-01', '2026-09-30', true, undefined, [state]);
  assert.equal(result.plottedMeasurements.length, 1);
  assert.equal(result.yCount(1200), result.yCount(1000));
  assert.equal(chart.splitTrendPointsOnGaps([state, { ...state, date: '2026-09-26' }]).length, 2);
  assert.equal(chart.buildGeometry([], [], [], '2026-08-01', '2026-08-31', true, undefined, [state]).plottedMeasurements.length, 0);
});

test('same-day measurements and states retain separate, nonzero pointer hit areas', () => {
  const { chart } = chartHarness();
  for (const responsive of [false, true]) {
    const areas = chart.buildMeasurementHitAreas([100, 100, 100], 44, 284, responsive);
    assert.ok(areas.every((area) => area.width > 0));
    for (let index = 1; index < areas.length; index++) assert.ok(areas[index - 1].left + areas[index - 1].width <= areas[index].left + 1e-9);
  }
});

test('hiding polyps removes unmeasured event targets instead of blocking ephyrae readings', () => {
  const data = timeline.prepareBiologicalChartData([measurement], [parentEntry, measurementEntry], labelsFor(fr, 'fr'));
  const harness = chartHarness();
  const props = { ...data, events: [], startDate: '2026-09-01', endDate: '2026-09-30', labels: chartLabels(fr) };
  const button = allNodes(harness.render(props)).find((node) => node.type === 'button' && node.props.className === 'is-polyps');
  button.props.onClick();
  const tree = harness.render(props);
  assert.equal(allNodes(tree).filter((node) => node.props?.className?.startsWith('bio-trend-measurement')).length, 1);
  assert.doesNotMatch(renderToStaticMarkup(tree), /bio-trend-measurement is-subculture/);
});

test('tracking window prefers the latest quantitative state over an older ordinary measurement', () => {
  const tracking = load('../src/components/BoxTrackingChart.tsx', {
    react: { useMemo: (fn) => fn(), useState: (initial) => [initial, () => {}] }, 'react/jsx-runtime': jsxRuntime,
    '../utils/chartWindow': utility('chartWindow'), '../utils/biologicalTimeline': timeline,
    './BiologicalTrendChart': { default: () => null }, './ChartWindowControls': { default: () => null },
    '../i18n': { createTranslator: (language) => translator(language === 'fr' ? fr : en, language) },
  });
  const oldMeasurement = reading(8, '2024-01-01', 100);
  const tree = tracking.default({ measurements: [oldMeasurement], biologicalTimeline: [parentEntry], locations: [], events: [], language: 'fr', labels: {
    polyps: fr.polyps, chartTitle: fr.chartTitle, chartEmpty: fr.chartEmpty,
  } });
  const trend = allNodes(tree).find((node) => node.props?.polypStates);
  assert.ok(trend.props.startDate <= parentEntry.effective_date && trend.props.endDate >= parentEntry.effective_date);
  assert.equal(trend.props.polypStates[0].polypCount, 0);
  assert.equal(trend.props.events.length, 1);
  assert.equal(trend.props.polypStates[0].title, 'Repiquage');
  assert.equal(trend.props.labels.subculture, 'Repiquage');
});

test('lifecycle grouping and preview integration use one event per parent operation', () => {
  const tracking = load('../src/components/BoxTrackingChart.tsx', {
    react: {}, 'react/jsx-runtime': jsxRuntime,
    '../utils/chartWindow': utility('chartWindow'), '../utils/biologicalTimeline': timeline,
    './BiologicalTrendChart': {}, './ChartWindowControls': {},
    '../i18n': { createTranslator: (language) => translator(language === 'fr' ? fr : en, language) },
  });
  const event = { id: 9, event_date: '2026-09-15' };
  const events = tracking.buildLifecycleEvents({ parents: [], children: [
    { event, box: { id: 2, global_code: 'SF.002' } },
    { event, box: { id: 3, global_code: 'SF.003' } },
  ] }, [], { subcultureEvent: 'Subculture', movementEvent: 'Transfer' });
  assert.equal(events.length, 1);
  assert.equal(events[0].detail, 'SF.002, SF.003');
  const source = readFileSync(new URL('../src/components/BoxTrackingPreview.tsx', import.meta.url), 'utf8');
  assert.match(source, /biologicalTimeline=\{detail\.biological_timeline\}/);
  assert.match(source, /detail\.biological_timeline\?\.length/);
});
