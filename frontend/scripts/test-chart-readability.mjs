import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as scales from 'd3-scale';
import * as shapes from 'd3-shape';

const chartSource = readFileSync(new URL('../src/components/BiologicalTrendChart.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../src/styles/components/biological-trend-chart.css', import.meta.url), 'utf8');
function compile(source, dependencies = {}) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, Date, require(name) {
    assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
    return dependencies[name];
  } });
  return exports;
}
const utility = (name) => compile(readFileSync(new URL(`../src/utils/${name}.ts`, import.meta.url), 'utf8'));
const chart = compile(chartSource, {
  'react/jsx-runtime': {}, react: {}, 'd3-scale': scales, 'd3-shape': shapes,
  '../utils/userIdentity': utility('userIdentity'),
  '../utils/dateFormat': utility('dateFormat'),
  '../utils/chartBiology': utility('chartBiology'),
  '../utils/chartLocations': utility('chartLocations'),
});
const reading = (id, date, polypCount, ephyraeCount) => ({ id, date, polypCount, ephyraeCount });
const readings = [reading(1, '2026-01-01', 0, 0), reading(2, '2026-01-06', 250, 500), reading(3, '2026-06-30', 1200, 1000)];
const locations = [{ id: 1, name: 'A very long laboratory location name', startsAt: '2026-01-01', endsAt: null }];
const geometry = (width, compact = false, values = readings) => chart.buildGeometry(
  values, locations, [], '2026-01-01', '2026-06-30', compact, chart.resolveChartLayout(compact, width, true),
);

// Execute the production component and its effects, retaining hook state between renders.
// DOM refs stay unmounted: this harness tests JSX/handlers, not browser layout or hit testing.
function mountTrendChart(overrides = {}) {
  const slots = [];
  let cursor = 0;
  let dirty = false;
  let pendingEffects = [];
  let props = {
    startDate: '2026-01-01', endDate: '2026-01-31', measurements: readings,
    temperatures: [
      { date: '2026-01-01', average_temperature_c: 0, zone_id: 7, zone_name: 'Cold room' },
      { date: '2026-01-08', average_temperature_c: 22, zone_id: 7, zone_name: 'Cold room' },
      { date: '2026-01-22', average_temperature_c: 24, zone_id: 8, zone_name: 'Warm room' },
    ],
    selectionScope: 'box-1',
    labels: { chartTitle: 'Chart', closeDetail: 'Close details', empty: 'Empty', ephyrae: 'Ephyrae',
      location: 'Location', locationChange: 'Location change', missingReading: 'Missing',
      movement: 'Move', polyps: 'Polyps', temperature: 'Temperature',
      temperatureOutsideScale: 'Outside the displayed scale (0–40 °C)' },
    ...overrides,
  };
  const unchanged = (previous, next) => previous != null && next != null
    && previous.length === next.length && next.every((value, index) => Object.is(value, previous[index]));
  const memo = (callback, dependencies) => {
    const index = cursor++;
    if (!slots[index] || !unchanged(slots[index].dependencies, dependencies)) {
      slots[index] = { value: callback(), dependencies };
    }
    return slots[index].value;
  };
  const effect = (callback, dependencies) => {
    const index = cursor++;
    const previous = slots[index];
    if (previous && unchanged(previous.dependencies, dependencies)) return;
    slots[index] = { dependencies };
    pendingEffects.push(() => {
      previous?.cleanup?.();
      slots[index].cleanup = callback();
    });
  };
  const jsx = (type, props, key) => ({ type, props, key });
  const component = compile(chartSource, {
    'react/jsx-runtime': { jsx, jsxs: jsx },
    react: {
      useState(initial) {
        const index = cursor++;
        if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
        return [slots[index], (update) => {
          const value = typeof update === 'function' ? update(slots[index]) : update;
          if (!Object.is(value, slots[index])) { slots[index] = value; dirty = true; }
        }];
      },
      useMemo: memo, useRef: (current) => memo(() => ({ current }), []),
      useId: () => memo(() => 'temperature-test', []),
      useEffect: effect, useLayoutEffect: effect,
    },
    'd3-scale': scales, 'd3-shape': shapes,
    '../utils/userIdentity': utility('userIdentity'), '../utils/dateFormat': utility('dateFormat'),
    '../utils/chartBiology': utility('chartBiology'), '../utils/chartLocations': utility('chartLocations'),
  }).default;
  return {
    render(patch = {}) {
      props = { ...props, ...patch };
      for (let pass = 0; pass < 20; pass++) {
        cursor = 0; dirty = false; pendingEffects = [];
        const tree = component(props);
        for (const run of pendingEffects) run();
        if (!dirty) return chartNodes(tree);
      }
      assert.fail('Chart effects did not settle');
    },
  };
}

function chartNodes(tree) {
  const nodes = [];
  function visit(node) {
    if (Array.isArray(node)) return node.forEach(visit);
    if (!node || typeof node !== 'object') return;
    nodes.push(node);
    visit(node.props?.children);
  }
  visit(tree);
  return nodes;
}
const hasClass = (node, name) => node.props?.className?.split(' ').includes(name);
const temperatureControls = (nodes) => nodes.filter((node) => hasClass(node, 'bio-trend-temperature-point'));
const temperatureLegend = (nodes) => nodes.find((node) => node.type === 'button' && hasClass(node, 'is-temperature') && !hasClass(node, 'bio-trend-event-choice'));
const chartTooltip = (nodes) => nodes.find((node) => hasClass(node, 'bio-trend-tooltip'));
const selectionGuide = (nodes) => nodes.find((node) => hasClass(node, 'bio-trend-selection-guide'));
const nodeText = (node) => Array.isArray(node) ? node.map(nodeText).join('')
  : node && typeof node === 'object' ? nodeText(node.props?.children) : node == null || node === false ? '' : String(node);
// Snapshots come only from the returned production JSX, not a second rendering implementation.
const axisFrame = (nodes) => JSON.stringify(nodes.filter((node) => node.type === 'svg'
  || hasClass(node, 'bio-trend-axis') || hasClass(node, 'bio-trend-grid') || hasClass(node, 'bio-trend-value-label'))
  .map(({ type, props: { viewBox, className, x, y, x1, x2, y1, y2, children } }) => (
    { type, viewBox, className, x, y, x1, x2, y1, y2, text: type === 'text' ? nodeText(children) : undefined }
  )));
const biologyGeometry = (nodes) => JSON.stringify(nodes.filter((node) => (
  (hasClass(node, 'is-polyps') || hasClass(node, 'is-ephyrae')) && (node.type === 'path' || node.type === 'circle')
)).map(({ type, props: { className, d, cx, cy, r } }) => ({ type, className, d, cx, cy, r })));
function keyEvent(key, overrides = {}) {
  return { key, prevented: false, preventDefault() { this.prevented = true; }, ...overrides };
}
function clickEvent() {
  return { stopped: false, stopPropagation() { this.stopped = true; } };
}

test('weekly evidence uses uncertainty spans and independent toggles without changing main chart geometry', () => {
  const weekly = { date: '2026-01-08', average_temperature_c: 0,
    week: { id: 55, year: 2026, number: 2, start: '2026-01-05', end: '2026-01-11', valueText: '0.000' } };
  const daily = { date: '2026-01-08', average_temperature_c: 12, zone_id: 7, zone_name: 'Zone' };
  const view = mountTrendChart({ temperatures: [weekly, daily], labels: {
    chartTitle: 'Chart', empty: 'Empty', polyps: 'Polyps', ephyrae: 'Ephyrae', location: 'Location',
    temperature: 'Zone temperature', historicalTemperature: 'Historical temperature by week',
    historicalWeek: 'Week', historicalPeriod: 'Possible period', historicalNote: 'Exact day unknown',
  } });
  let nodes = view.render();
  const axes = axisFrame(nodes), biology = biologyGeometry(nodes);
  const weeklyLegend = () => nodes.find(node => node.type === 'button' && hasClass(node, 'is-weekly'));
  assert.equal(temperatureControls(nodes).length, 2);
  assert.equal(nodes.filter(node => hasClass(node, 'bio-trend-week-span')).length, 1);
  assert.equal(nodes.filter(node => node.type === 'rect' && hasClass(node, 'is-weekly')).length, 1);
  const control = temperatureControls(nodes).find(node => node.props['aria-label'].startsWith('Week 2, 2026'));
  assert.ok(control.props['aria-label'].includes('Historical temperature by week: 0.000'));
  assert.doesNotMatch(control.props['aria-label'], /08\/01\/2026/);
  control.props.onClick(clickEvent());
  nodes = view.render();
  const tooltipText = nodeText(chartTooltip(nodes));
  assert.ok(tooltipText.includes(`Possible period${utility('dateFormat').formatDisplayDate(weekly.week.start)} - ${utility('dateFormat').formatDisplayDate(weekly.week.end)}`));
  assert.ok(!tooltipText.includes(utility('dateFormat').formatDisplayDate(weekly.date)), 'synthetic marker position is never an observed day');
  assert.match(nodeText(chartTooltip(nodes)), /Exact day unknown/);
  temperatureLegend(nodes).props.onClick();
  nodes = view.render();
  assert.equal(temperatureControls(nodes).length, 1, 'daily toggle leaves weekly evidence visible');
  assert.ok(chartTooltip(nodes), 'daily toggle does not unpin weekly evidence');
  weeklyLegend().props.onClick();
  nodes = view.render();
  assert.equal(temperatureControls(nodes).length, 0);
  assert.equal(chartTooltip(nodes), undefined);
  assert.equal(axisFrame(nodes), axes);
  assert.equal(biologyGeometry(nodes), biology);
});

test('temperature uses an independent right axis and connects sparse weekly values without synthesizing samples', () => {
  const temperatures = [
    { date: '2026-01-15', average_temperature_c: 24 },
    { date: '2026-01-01', average_temperature_c: 0 },
    { date: '2026-01-08', average_temperature_c: 22 },
    { date: '2026-01-09', average_temperature_c: null },
    { date: '2026-01-10', average_temperature_c: NaN },
    { date: '2025-12-31', average_temperature_c: 100 },
  ];
  for (const compact of [true, false]) {
    for (const responsive of [true, false]) {
      const result = chart.buildGeometry(readings, locations, [], '2026-01-01', '2026-01-15', compact,
        chart.resolveChartLayout(compact, 296, responsive), [], temperatures);
      assert.equal(result.plottedTemperatures.length, 3);
      assert.equal(result.plottedTemperatures[0].average_temperature_c, 0);
      assert.equal(result.plottedTemperatures[1].date, '2026-01-08');
      const path = result.temperatureLine(result.plottedTemperatures);
      assert.equal((path.match(/M/g) ?? []).length, 1);
      assert.equal((path.match(/L/g) ?? []).length, 2);
      assert.ok(result.padding.right >= 62);
      assert.ok(result.temperatureTicks.length > 1);
      for (const point of result.plottedTemperatures) {
        const y = result.yTemperature(point.average_temperature_c);
        assert.ok(y >= result.plotTop && y <= result.countHeight - result.padding.bottom);
      }
      assert.equal(result.maxCount, 1000);
      assert.equal(result.zoneBandHeight, 0);
    }
  }
  const single = chart.buildGeometry([], [], [], '2026-01-01', '2026-01-15', false, undefined, [], temperatures.slice(0, 1));
  assert.ok(Number.isFinite(single.yTemperature(24)));
  assert.equal(single.plottedTemperatures.length, 1);
  assert.match(chartSource, /className="bio-trend-temperature-axis"/);
  assert.match(chartSource, /x1=\{width - padding.right\} x2=\{width - padding.right\}/);
  assert.match(chartSource, /role="button" tabIndex=\{0\}/);
  assert.match(chartSource, /\{value\} °C/);
  assert.match(css, /--series-color: var\(--color-temperature\)/);
});

test('value axes retain independent fixed domains and factual temperatures in every layout', () => {
  const samples = [[0], [22.3], [22.1, 22.4], [-4.7, -1.2], [-0.2, 0, 0.3], [0, 24]];
  for (const compact of [true, false]) {
    for (const responsive of [true, false]) {
      for (const values of samples) {
        const temperatures = values.map((value, index) => ({ date: `2026-01-${String(1 + index * 7).padStart(2, '0')}`, average_temperature_c: value }));
        const snapshot = JSON.stringify(temperatures);
        const result = chart.buildGeometry(readings, [], [], '2026-01-01', '2026-01-31', compact,
          chart.resolveChartLayout(compact, 360, responsive), [], temperatures);
        assert.deepEqual([...result.countTicks], scales.scaleLinear().domain([0, 1000]).ticks(4));
        assert.deepEqual([...result.temperatureTicks], [0, 5, 10, 15, 20, 25, 30, 35, 40]);
        assert.deepEqual([...result.yTemperature.domain()], [0, 40]);
        assert.notDeepEqual([...result.temperatureTicks], [...result.countTicks]);
        assert.equal(result.yCount(0), result.countHeight - result.padding.bottom);
        assert.equal(result.yCount(1200), result.yCount(1000), 'Biological overflow remains clamped at 1000');
        assert.equal(result.temperatureTicks.length, 9);
        assert.equal(JSON.stringify(result.plottedTemperatures), snapshot, 'Do not round or synthesize real samples');
        assert.equal(JSON.stringify(temperatures), snapshot, 'Do not mutate source temperatures');
        for (const value of values) {
          const y = result.yTemperature(value);
          assert.ok(Number.isFinite(y));
          if (value >= 0 && value <= 40) assert.ok(y >= result.plotTop && y <= result.countHeight - result.padding.bottom);
          else assert.ok(y < result.plotTop || y > result.countHeight - result.padding.bottom, 'Scale never silently clamps an out-of-range value');
        }
      }
    }
  }
  const empty = chart.buildGeometry(readings, [], [], '2026-01-01', '2026-01-31', false, undefined, [], [
    { date: '2026-01-01', average_temperature_c: null },
    { date: '2026-01-08', average_temperature_c: Infinity },
  ]);
  assert.equal(empty.plottedTemperatures.length, 0);
  assert.equal(empty.padding.right, 62, 'Absent temperatures keep the same axis gutter');
});

test('value axis typography and spacing are shared while the existing olive token stays distinct', () => {
  const rule = css.match(/^\.bio-trend-value-label\s*\{([^}]+)\}/m)[1];
  assert.match(rule, /font-family: inherit/);
  assert.match(rule, /font-size: var\(--chart-axis-size\)/);
  assert.match(rule, /font-weight: 650/);
  assert.match(rule, /font-variant-numeric: tabular-nums/);
  assert.match(css, /\.bio-trend-temperature-axis \.bio-trend-value-label \{ fill: var\(--color-temperature\); \}/);
  assert.doesNotMatch(css, /\.bio-trend-temperature-axis line\s*\{/);
  assert.match(chartSource, /className="bio-trend-axis" x1=\{width - padding.right\}/);
  assert.match(chartSource, /x=\{width - padding.right \+ 8\} y=\{yTemperature\(value\) \+ 4\}/);
  assert.match(chartSource, /x=\{padding.left - 8\}\s+y=\{yCount\(value\) \+ 4\}/);
  const tokens = readFileSync(new URL('../src/styles/tokens.css', import.meta.url), 'utf8');
  assert.match(tokens, /--color-temperature: #657d4f;/);
});

test('rendered temperature axis and legend survive empty/loading/failed props, windows and Box scopes', () => {
  for (const compact of [false, true]) {
    const harness = mountTrendChart({ compact });
    let nodes = harness.render();
    const frame = axisFrame(nodes);
    const biology = biologyGeometry(nodes);
    const samples = [
      { date: '2026-01-01', average_temperature_c: 0 },
      { date: '2026-01-08', average_temperature_c: 22 },
    ];
    for (const [state, temperatures] of [['populated', samples], ['empty', []], ['loading', []], ['failed', []]]) {
      const status = { type: 'p', props: { role: state === 'failed' ? 'alert' : 'status', children: state } };
      nodes = harness.render({ temperatures, temperatureStatus: status });
      const axis = nodes.find((node) => hasClass(node, 'bio-trend-temperature-axis'));
      assert.ok(axis, state);
      assert.equal(axis.props['aria-label'], 'Temperature (°C)');
      assert.deepEqual(chartNodes(axis).filter((node) => node.type === 'text').map(nodeText),
        ['0 °C', '5 °C', '10 °C', '15 °C', '20 °C', '25 °C', '30 °C', '35 °C', '40 °C']);
      assert.equal(temperatureLegend(nodes).props['aria-pressed'], true, state);
      assert.equal(nodeText(temperatureLegend(nodes)), 'Temperature (°C)');
      assert.ok(nodes.includes(status), 'The actual status prop remains in the rendered feedback');
      assert.equal(axisFrame(nodes), frame, `${state}: fixed axes and 62px gutter`);
      assert.equal(biologyGeometry(nodes), biology, `${state}: biological coordinates do not move`);
    }
    for (const patch of [
      { startDate: '2026-01-05', endDate: '2026-01-25' },
      { selectionScope: 'box-2', measurements: [] },
      { startDate: '2026-02-01', endDate: '2026-02-28', temperatureStatus: undefined },
    ]) {
      nodes = harness.render(patch);
      assert.equal(axisFrame(nodes), frame, 'Window and Box changes cannot remove or resize the axes');
      assert.equal(temperatureLegend(nodes).props['aria-pressed'], true);
    }
    const svg = nodes.find((node) => node.type === 'svg');
    const axisLine = chartNodes(nodes.find((node) => hasClass(node, 'bio-trend-temperature-axis'))).find((node) => node.type === 'line');
    assert.equal(Number(svg.props.viewBox.split(' ')[2]) - axisLine.props.x1, 62);
    assert.ok(nodes.some((node) => hasClass(node, 'bio-trend-empty')));
  }
});

test('temperature hover/focus are transient; click, Enter and Space pin exact details and selection guides', () => {
  const harness = mountTrendChart();
  let nodes = harness.render();
  let point = temperatureControls(nodes)[1];
  const frame = axisFrame(nodes);
  assert.equal(point.props.role, 'button');
  assert.equal(point.props.tabIndex, 0);
  assert.equal(point.props['aria-pressed'], false);
  assert.equal(selectionGuide(nodes), undefined);
  point.props.onMouseEnter();
  nodes = harness.render();
  assert.equal(nodeText(chartTooltip(nodes)).includes('22'), true);
  assert.equal(temperatureControls(nodes)[1].props['aria-describedby'], chartTooltip(nodes).props.id);
  assert.equal(temperatureControls(nodes)[1].props['aria-pressed'], false);
  assert.equal(selectionGuide(nodes), undefined, 'Hover is not selection');
  point.props.onMouseLeave();
  assert.equal(chartTooltip(harness.render()), undefined);
  point.props.onFocus();
  nodes = harness.render();
  assert.ok(nodeText(chartTooltip(nodes)).includes('Cold room'));
  assert.equal(temperatureControls(nodes)[1].props['aria-pressed'], false);
  point.props.onBlur();
  assert.equal(chartTooltip(harness.render()), undefined);

  for (const activation of ['click', 'Enter', ' ']) {
    nodes = harness.render();
    point = temperatureControls(nodes)[1];
    const event = activation === 'click' ? clickEvent() : keyEvent(activation);
    point.props[activation === 'click' ? 'onClick' : 'onKeyDown'](event);
    assert.equal(activation === 'click' ? event.stopped : event.prevented, true);
    nodes = harness.render();
    point = temperatureControls(nodes)[1];
    assert.equal(point.props['aria-pressed'], true, activation);
    assert.equal(temperatureControls(nodes).filter((node) => node.props['aria-pressed']).length, 1);
    assert.ok(hasClass(point, 'is-selected'));
    assert.ok(hasClass(chartTooltip(nodes), 'is-pinned'));
    assert.ok(nodeText(chartTooltip(nodes)).includes('Temperature (°C)22'));
    const dot = chartNodes(point).find((node) => hasClass(node, 'bio-trend-dot'));
    assert.equal(selectionGuide(nodes).props.x1, dot.props.cx);
    assert.equal(selectionGuide(nodes).props.x2, dot.props.cx);
    assert.equal(axisFrame(nodes), frame);
    const escape = keyEvent('Escape');
    point.props.onKeyDown(escape);
    assert.equal(escape.prevented, true);
    nodes = harness.render();
    assert.equal(temperatureControls(nodes)[1].props['aria-pressed'], false);
    assert.equal(selectionGuide(nodes), undefined);
    assert.equal(chartTooltip(nodes), undefined);
  }
});

test('temperature arrow/Home/End navigation focuses temperature queries, including boundary clamps', () => {
  const harness = mountTrendChart();
  let nodes = harness.render();
  const queries = [];
  let focused;
  const svg = { querySelector(selector) {
    queries.push(selector);
    const target = temperatureControls(harness.render()).find((node) => selector === `[data-temperature-index="${node.props['data-temperature-index']}"]`);
    assert.ok(target, `Navigation must query a real temperature control: ${selector}`);
    return { focus() { focused?.props.onBlur(); focused = target; target.props.onFocus(); } };
  } };
  for (const [from, key, to] of [
    [1, 'ArrowLeft', 0], [0, 'ArrowLeft', 0], [0, 'ArrowRight', 1],
    [1, 'ArrowRight', 2], [2, 'ArrowRight', 2], [1, 'Home', 0], [0, 'End', 2],
  ]) {
    const event = keyEvent(key, { currentTarget: { ownerSVGElement: svg } });
    temperatureControls(nodes)[from].props.onKeyDown(event);
    assert.equal(event.prevented, true, key);
    assert.equal(queries.at(-1), `[data-temperature-index="${to}"]`);
    nodes = harness.render();
    assert.equal(temperatureControls(nodes)[to].props['aria-describedby'], chartTooltip(nodes).props.id);
    assert.equal(nodeText(chartTooltip(nodes)).includes(to === 2 ? 'Warm room' : 'Cold room'), true);
    assert.equal(selectionGuide(nodes), undefined, 'Focus navigation does not silently pin');
    assert.ok(temperatureControls(nodes).every((node) => !node.props['aria-pressed']));
  }
  const ignored = keyEvent('Tab', { currentTarget: { ownerSVGElement: svg } });
  temperatureControls(nodes)[0].props.onKeyDown(ignored);
  assert.equal(ignored.prevented, false);
  assert.equal(queries.length, 7);
});

test('temperature toggle preserves biological selection and axes while clearing hidden temperature details', () => {
  const harness = mountTrendChart();
  let nodes = harness.render();
  const frame = axisFrame(nodes);
  const biology = biologyGeometry(nodes);
  const measurement = nodes.find((node) => node.props?.['data-measurement-index'] === 1);
  measurement.props.onClick(clickEvent());
  nodes = harness.render();
  const biologicalGuide = JSON.stringify(selectionGuide(nodes).props);
  const biologicalDetail = nodeText(chartTooltip(nodes));
  temperatureControls(nodes)[0].props.onMouseEnter();
  temperatureControls(nodes)[0].props.onFocus();
  temperatureLegend(nodes).props.onClick();
  nodes = harness.render();
  assert.equal(temperatureLegend(nodes).props['aria-pressed'], false);
  assert.equal(temperatureControls(nodes).length, 0);
  assert.equal(nodes.filter((node) => hasClass(node, 'bio-trend-line') && hasClass(node, 'is-temperature')).length, 0);
  assert.equal(nodes.find((node) => node.props?.['data-measurement-index'] === 1).props['aria-pressed'], true);
  assert.equal(nodeText(chartTooltip(nodes)), biologicalDetail);
  assert.equal(JSON.stringify(selectionGuide(nodes).props), biologicalGuide);
  assert.equal(axisFrame(nodes), frame);
  assert.equal(biologyGeometry(nodes), biology);

  temperatureLegend(nodes).props.onClick();
  nodes = harness.render();
  const point = temperatureControls(nodes)[2];
  point.props.onFocus(); point.props.onMouseEnter(); point.props.onClick(clickEvent());
  nodes = harness.render();
  assert.ok(hasClass(chartTooltip(nodes), 'is-pinned'));
  temperatureLegend(nodes).props.onClick();
  nodes = harness.render();
  assert.equal(chartTooltip(nodes), undefined);
  assert.equal(selectionGuide(nodes), undefined);
  assert.equal(axisFrame(nodes), frame);
  assert.equal(biologyGeometry(nodes), biology);
  temperatureLegend(nodes).props.onClick();
  nodes = harness.render();
  assert.equal(temperatureControls(nodes).length, 3);
  assert.ok(temperatureControls(nodes).every((node) => !node.props['aria-pressed']));
  assert.equal(chartTooltip(nodes), undefined, 'Re-enabling must not resurrect hidden focus/hover/pin');
});

test('Box selectionScope resets temperature pins and legend visibility but not chart geometry', () => {
  const harness = mountTrendChart();
  let nodes = harness.render();
  const frame = axisFrame(nodes);
  temperatureControls(nodes)[1].props.onClick(clickEvent());
  nodes = harness.render();
  assert.ok(selectionGuide(nodes));
  nodes = harness.render({ selectionScope: 'box-2' });
  assert.equal(selectionGuide(nodes), undefined);
  assert.equal(chartTooltip(nodes), undefined);
  assert.ok(temperatureControls(nodes).every((node) => !node.props['aria-pressed']));
  temperatureLegend(nodes).props.onClick();
  nodes.find((node) => node.type === 'button' && hasClass(node, 'is-polyps')).props.onClick();
  nodes = harness.render();
  assert.equal(temperatureLegend(nodes).props['aria-pressed'], false);
  nodes = harness.render({ selectionScope: 'box-3' });
  assert.equal(temperatureLegend(nodes).props['aria-pressed'], true);
  assert.equal(nodes.find((node) => node.type === 'button' && hasClass(node, 'is-polyps')).props['aria-pressed'], true);
  assert.equal(axisFrame(nodes), frame);
});

test('temperature zone/date identity retains a pin across overlapping windows and refreshed sources', () => {
  const harness = mountTrendChart();
  let nodes = harness.render();
  const selected = temperatureControls(nodes)[1];
  assert.equal(selected.key, 'temperature-7-2026-01-08');
  selected.props.onClick(clickEvent());
  nodes = harness.render({ startDate: '2026-01-05', endDate: '2026-01-25' });
  const retained = temperatureControls(nodes)[0];
  assert.equal(retained.key, selected.key, 'Dropping an earlier sparse observation cannot change the ID');
  assert.equal(retained.props['data-temperature-index'], 0);
  assert.equal(retained.props['aria-pressed'], true, 'Window effects preserve a still-visible pin');
  assert.ok(hasClass(chartTooltip(nodes), 'is-pinned'));
  assert.equal(selectionGuide(nodes).props.x1, chartNodes(retained).find((node) => hasClass(node, 'bio-trend-dot')).props.cx);
  nodes = harness.render({ temperatures: [
    { date: '2026-01-22', average_temperature_c: 24, zone_id: 8, zone_name: 'Warm room' },
    { date: '2026-01-08', average_temperature_c: 21.75, zone_id: 7, zone_name: 'Renamed room' },
  ] });
  assert.equal(temperatureControls(nodes)[0].key, selected.key);
  assert.equal(temperatureControls(nodes)[0].props['aria-pressed'], true);
  assert.ok(nodeText(chartTooltip(nodes)).includes('21.75'));
  assert.ok(nodeText(chartTooltip(nodes)).includes('Renamed room'), 'Details resolve from current data, not stale pin objects');
  nodes = harness.render({ startDate: '2026-01-15' });
  assert.equal(chartTooltip(nodes), undefined);
  assert.equal(selectionGuide(nodes), undefined);
  assert.ok(temperatureControls(nodes).every((node) => !node.props['aria-pressed']));
});

test('removed or null temperature sources cannot display stale hovered, focused or pinned details', () => {
  const harness = mountTrendChart();
  for (const handler of ['onMouseEnter', 'onFocus', 'onClick']) {
    for (const replacement of [[], [{ date: '2026-01-08', average_temperature_c: null, zone_id: 7 }],
      [{ date: '2026-01-08', average_temperature_c: 22, zone_id: 99, zone_name: 'Another room' }]]) {
      let nodes = harness.render({ selectionScope: `${handler}-${JSON.stringify(replacement)}`, temperatures: [
        { date: '2026-01-08', average_temperature_c: 22, zone_id: 7, zone_name: 'Cold room' },
      ] });
      temperatureControls(nodes)[0].props[handler](clickEvent());
      assert.ok(chartTooltip(harness.render()), handler);
      nodes = harness.render({ temperatures: replacement });
      assert.equal(chartTooltip(nodes), undefined, `${handler}: stale details disappear without changing the window`);
      assert.equal(selectionGuide(nodes), undefined);
      assert.ok(temperatureControls(nodes).every((node) => !node.props['aria-pressed']));
    }
  }
});

test('rendered sparse observations retain zero and exact decimals without inventing null samples', () => {
  const temperatures = [
    { date: '2026-01-22', average_temperature_c: 22.125, zone_id: 7 },
    { date: '2026-01-01', average_temperature_c: 0, zone_id: 7 },
    { date: '2026-01-08', average_temperature_c: null, zone_id: 7 },
    { date: '2026-01-09', average_temperature_c: NaN, zone_id: 7 },
    { date: '2026-01-10', average_temperature_c: Infinity, zone_id: 7 },
    { date: '2025-12-31', average_temperature_c: 24, zone_id: 7 },
  ];
  const original = JSON.stringify(temperatures);
  const harness = mountTrendChart({ temperatures, measurements: [] });
  let nodes = harness.render();
  const points = temperatureControls(nodes);
  assert.deepEqual(points.map((node) => node.key), ['temperature-7-2026-01-01', 'temperature-7-2026-01-22']);
  const dots = points.map((point) => chartNodes(point).find((node) => hasClass(node, 'bio-trend-dot')));
  const axis = chartNodes(nodes.find((node) => hasClass(node, 'bio-trend-temperature-axis'))).find((node) => node.type === 'line');
  assert.equal(dots[0].props.cy, axis.props.y2, 'A real zero is rendered on the zero tick');
  assert.ok(dots[0].props.r > dots[1].props.r, 'Zero retains its deliberate visible marker');
  assert.ok(points[0].props['aria-label'].includes('Temperature: 0 °C'));
  assert.ok(points[1].props['aria-label'].includes('22.125 °C'));
  const path = nodes.find((node) => hasClass(node, 'bio-trend-line') && hasClass(node, 'is-temperature')).props.d;
  assert.equal((path.match(/M/g) ?? []).length, 1);
  assert.equal((path.match(/L/g) ?? []).length, 1, 'Only the two real observations form the sparse line');
  assert.equal(nodes.some((node) => hasClass(node, 'bio-trend-empty')), false);
  points[0].props.onClick(clickEvent());
  nodes = harness.render();
  assert.ok(nodeText(chartTooltip(nodes)).includes('Temperature (°C)0'));
  assert.equal(JSON.stringify(temperatures), original);
});

test('off-scale -2/43 temperatures use directional markers, exact details and notes, never clamped dots or lines', () => {
  const harness = mountTrendChart({ temperatures: [
    { date: '2026-01-01', average_temperature_c: 20, zone_id: 7 },
    { date: '2026-01-08', average_temperature_c: -2, zone_id: 7 },
    { date: '2026-01-15', average_temperature_c: 20, zone_id: 7 },
    { date: '2026-01-22', average_temperature_c: 43, zone_id: 7 },
    { date: '2026-01-29', average_temperature_c: 20, zone_id: 7 },
  ] });
  let nodes = harness.render();
  const points = temperatureControls(nodes);
  const axis = chartNodes(nodes.find((node) => hasClass(node, 'bio-trend-temperature-axis'))).find((node) => node.type === 'line');
  const path = nodes.find((node) => hasClass(node, 'bio-trend-line') && hasClass(node, 'is-temperature')).props.d;
  assert.equal((path.match(/M/g) ?? []).length, 3, 'Off-scale samples split the real in-range segments');
  assert.equal((path.match(/L/g) ?? []).length, 0, 'No clamped connection or bridge through an off-scale sample');
  assert.equal(nodes.filter((node) => hasClass(node, 'bio-trend-dot') && hasClass(node, 'is-temperature')).length, 3);
  assert.equal(nodes.filter((node) => hasClass(node, 'bio-trend-overflow') && hasClass(node, 'is-temperature')).length, 2);
  const note = nodeText(nodes.find((node) => hasClass(node, 'bio-trend-temperature-overflow-note')));
  assert.equal(note, 'Outside the displayed scale (0–40 °C)');
  for (const [index, value, edge, direction] of [[1, -2, axis.props.y2, -1], [3, 43, axis.props.y1, 1]]) {
    const point = points[index];
    assert.equal(chartNodes(point).some((node) => hasClass(node, 'bio-trend-dot')), false);
    const marker = chartNodes(point).find((node) => hasClass(node, 'bio-trend-overflow'));
    const hit = chartNodes(point).find((node) => hasClass(node, 'bio-trend-temperature-hit'));
    assert.equal(hit.props.cy, edge);
    const coordinates = marker.props.d.match(/-?\d+(?:\.\d+)?/g).map(Number);
    assert.equal(coordinates[3], edge + direction, 'Marker tip explicitly indicates the out-of-scale direction');
    assert.ok(point.props['aria-label'].includes(`${value} °C`));
    assert.ok(point.props['aria-label'].includes(note));
    point.props.onClick(clickEvent());
    nodes = harness.render();
    assert.ok(nodeText(chartTooltip(nodes)).includes(`Temperature (°C)${value}`));
    assert.ok(nodeText(chartTooltip(nodes)).includes(note));
    assert.equal(temperatureControls(nodes)[index].props['aria-pressed'], true);
  }
});

test('coincident same-day same-value zones retain separate native temperature buttons and details', () => {
  const harness = mountTrendChart({ temperatures: [
    { date: '2026-01-08', average_temperature_c: 22, zone_id: 7, zone_name: 'Cold room' },
    { date: '2026-01-08', average_temperature_c: 22, zone_id: 8, zone_name: 'Warm room' },
  ] });
  const choices = (nodes) => nodes.filter((node) => node.type === 'button' && hasClass(node, 'bio-trend-event-choice') && hasClass(node, 'is-temperature'));
  let nodes = harness.render();
  const points = temperatureControls(nodes);
  assert.equal(points.length, 2);
  assert.equal(new Set(points.map((node) => node.key)).size, 2);
  const dots = points.map((point) => chartNodes(point).find((node) => hasClass(node, 'bio-trend-dot')));
  assert.equal(dots[0].props.cx, dots[1].props.cx);
  assert.equal(dots[0].props.cy, dots[1].props.cy, 'Native alternatives must not jitter the scientific coordinates');
  assert.equal(choices(nodes).length, 2);
  for (const [index, room] of ['Cold room', 'Warm room'].entries()) {
    let button = choices(nodes)[index];
    assert.equal(button.props.type, 'button', 'Enter/Space activation is provided by a native button');
    assert.equal(button.key, points[index].key);
    assert.ok(nodeText(button).includes('22 °C'));
    assert.ok(nodeText(button).includes(room));
    button.props.onMouseEnter();
    assert.ok(nodeText(chartTooltip(harness.render())).includes(room));
    button.props.onMouseLeave();
    assert.equal(chartTooltip(harness.render()), undefined);
    button.props.onFocus();
    assert.ok(nodeText(chartTooltip(harness.render())).includes(room));
    button.props.onBlur();
    assert.equal(chartTooltip(harness.render()), undefined);
    button.props.onClick();
    nodes = harness.render();
    assert.equal(choices(nodes)[index].props['aria-pressed'], true);
    assert.equal(choices(nodes)[1 - index].props['aria-pressed'], false);
    assert.ok(nodeText(chartTooltip(nodes)).includes(room));
    assert.equal(selectionGuide(nodes).props.x1, dots[index].props.cx);
    button = choices(nodes)[index];
    button.props.onKeyDown(keyEvent('Escape'));
    nodes = harness.render();
    assert.equal(chartTooltip(nodes), undefined);
    assert.equal(selectionGuide(nodes), undefined);
    assert.ok(choices(nodes).every((node) => !node.props['aria-pressed']));
  }
  temperatureLegend(nodes).props.onClick();
  assert.equal(choices(harness.render()).length, 0);
});

test('location changes use the shared movement translation in every chart entry point', () => {
  const trackingSource = readFileSync(new URL('../src/components/BoxTrackingChart.tsx', import.meta.url), 'utf8');
  const exportSource = readFileSync(new URL('../src/components/ExportsView.tsx', import.meta.url), 'utf8');
  const overviewSource = readFileSync(new URL('../src/components/OverviewView.tsx', import.meta.url), 'utf8');
  assert.match(trackingSource, /movement: createTranslator\(language\)\('movementEvent'\)/);
  assert.match(exportSource, /title: translations\[language\]\.movementEvent/);
  assert.match(exportSource, /movement: translations\[language\]\.movementEvent/);
  assert.match(overviewSource, /movement: t\('movementEvent'\)/);
  for (const source of [chartSource, trackingSource, exportSource]) {
    assert.doesNotMatch(source, /['"](?:Transferts?|Transfers?)['"]/);
  }
  assert.match(chartSource, /movement: string;/);

  for (const [language, expected] of [['fr', 'Déplacement'], ['en', 'Move']]) {
    const locale = compile(readFileSync(new URL(`../src/i18n/${language}.ts`, import.meta.url), 'utf8'))[language];
    assert.equal(locale.movementEvent, expected);
    assert.match(locale.adminTransferTitle, language === 'fr' ? /Transfert/ : /Transfer/);
    const jsx = (type, props) => ({ type, props });
    const renderedChart = compile(chartSource, {
      'react/jsx-runtime': { jsx, jsxs: jsx },
      react: {
        useState: (initial) => [initial, () => {}],
        useMemo: (callback) => callback(),
        useRef: (current) => ({ current }),
        useId: () => 'movement-test',
        useEffect() {},
        useLayoutEffect() {},
      },
      'd3-scale': scales, 'd3-shape': shapes,
      '../utils/userIdentity': utility('userIdentity'),
      '../utils/dateFormat': utility('dateFormat'),
      '../utils/chartBiology': utility('chartBiology'),
      '../utils/chartLocations': utility('chartLocations'),
    }).default({
      startDate: '2026-01-01', endDate: '2026-06-30', measurements: readings,
      temperatures: [{ date: '2026-01-01', average_temperature_c: 0 }, { date: '2026-01-08', average_temperature_c: 22 }],
      locations: [
        { id: 1, name: 'A', startsAt: '2026-01-01', endsAt: '2026-03-01' },
        { id: 2, name: 'B', startsAt: '2026-03-01', endsAt: null },
      ],
      events: [{ id: 3, date: '2026-02-01', title: expected, kind: 'movement' }],
      labels: { temperature: locale.temperature, chartTitle: 'Chart', empty: 'Empty', ephyrae: 'Ephyrae', location: 'Location', locationChange: locale.locationPeriodChange, missingReading: 'Missing', movement: locale.movementEvent, polyps: 'Polyps' },
    });
    const nodes = [];
    function visit(node) {
      if (Array.isArray(node)) return node.forEach(visit);
      if (!node || typeof node !== 'object') return;
      nodes.push(node);
      visit(node.props?.children);
    }
    visit(renderedChart);
    assert.equal(nodes.filter((node) => node.props?.className === 'bio-trend-location-band').length, 0);
    const temperaturePoints = nodes.filter((node) => node.props?.className === 'bio-trend-temperature-point');
    assert.equal(temperaturePoints.length, 2);
    assert.ok(temperaturePoints[0].props['aria-label'].includes(`${locale.temperature}: 0 °C`));
    assert.equal(temperaturePoints[0].props.tabIndex, 0);
    for (const handler of ['onFocus', 'onMouseEnter', 'onClick', 'onKeyDown']) assert.equal(typeof temperaturePoints[0].props[handler], 'function');
    assert.equal(nodes.filter((node) => node.props?.className === 'bio-trend-line is-temperature').length, 1);
    const temperatureLegend = nodes.find((node) => node.type === 'button' && node.props.className === 'is-temperature');
    assert.equal(temperatureLegend.props['aria-pressed'], true);
    const rightAxis = nodes.find((node) => node.props?.className === 'bio-trend-temperature-axis');
    assert.equal(rightAxis.props['aria-label'], `${locale.temperature} (°C)`);
    const valueLabels = nodes.filter((node) => node.type === 'text' && node.props?.className?.includes('bio-trend-value-label'));
    assert.ok(valueLabels.some((node) => node.props.className.includes('bio-trend-y-label')));
    assert.ok(valueLabels.some((node) => Array.isArray(node.props.children) && node.props.children.includes(' °C')));
    const expectedGeometry = chart.buildGeometry(readings, [], [], '2026-01-01', '2026-06-30', false);
    assert.deepEqual(nodes.filter((node) => node.props?.className === 'bio-trend-grid').map((node) => node.props.y1),
      [...expectedGeometry.countTicks].filter((value) => value > 0 && value < 1000).map(expectedGeometry.yCount));
    const markers = nodes.filter((node) => node.props?.className === 'bio-trend-event is-movement');
    assert.equal(markers.length, 1, 'only the persisted movement uses the movement style');
    for (const marker of markers) assert.ok(marker.props['aria-label'].startsWith(`${expected} `));
    const derived = nodes.filter((node) => node.props?.className === 'bio-trend-event is-location-change');
    assert.equal(derived.length, 0, 'derived period boundaries do not add chart markers');
    const markerLabels = nodes.filter((node) => node.props?.className === 'bio-trend-event-label');
    assert.deepEqual([...markerLabels.map((label) => label.props.children)], [expected.toLocaleUpperCase('fr-FR')]);
    assert.match(chartSource, /title: eventTitle,/);
    assert.match(chartSource, /accessibleTitle: `\$\{eventTitle\} - \$\{formatEventDate\(event\)\}`/);
  }
});

test('phone/tablet geometry fills the available padded card content, in both chart modes', () => {
  for (const viewport of [320, 375, 390, 768, 960, 1024, 1180]) {
    // Model page/card padding, not the full viewport as drawing space.
    const contentWidth = viewport - 64;
    for (const compact of [false, true]) {
      const result = geometry(contentWidth, compact);
      assert.equal(result.width, contentWidth);
      assert.equal(result.xPosition('2026-01-01'), 44);
      assert.equal(result.xPosition('2026-06-30'), contentWidth - 62);
      assert.equal(result.width - result.padding.left - result.padding.right, contentWidth - 106);
      assert.ok(result.plotHeight > 160);
      assert.ok(result.xPosition('2026-06-30') + 5.2 + 1.3 < result.width);
      assert.ok(result.yCount(0) + 5.2 + 1.3 < result.countHeight);
      assert.ok(result.yCount(1000) - 5.2 - 1.3 >= 0);
      for (const tick of result.timeTicks) {
        assert.ok(tick.x >= result.padding.left + 116);
        assert.ok(tick.x <= result.width - result.padding.right - 116);
      }
      for (let index = 1; index < result.timeTicks.length; index++) {
        assert.ok(result.timeTicks[index].x - result.timeTicks[index - 1].x >= 88);
      }
    }
  }
});

test('responsive geometry preserves dates, fixed count scale, zeros, overflow and gaps', () => {
  const phone = geometry(296);
  const desktop = chart.buildGeometry(readings, locations, [], '2026-01-01', '2026-06-30', false);
  assert.equal(phone.maxCount, 1000);
  assert.equal(phone.yCount(1200), phone.yCount(1000));
  assert.equal(phone.yCount(0), phone.countHeight - phone.padding.bottom);
  assert.deepEqual(JSON.parse(JSON.stringify(phone.plottedMeasurements)), readings);
  assert.equal(phone.measurementSegments.length, desktop.measurementSegments.length);
  const fraction = (result, date) => (result.xPosition(date) - result.padding.left) / (result.width - result.padding.left - result.padding.right);
  assert.ok(Math.abs(fraction(phone, '2026-01-06') - fraction(desktop, '2026-01-06')) < 1e-12);
  assert.equal(readings[0].polypCount, 0);
});

test('desktop sizing and markers stay unchanged; responsive points are modestly larger', () => {
  for (const compact of [true, false]) {
    const desktop = chart.resolveChartLayout(compact, 296, false);
    assert.equal(desktop.width, compact ? 640 : 860);
    assert.equal(desktop.countHeight, compact ? 238 : 260);
    assert.equal(desktop.pointRadius, 2.15);
    assert.equal(desktop.zeroRadius, 2.8);
    const mobile = chart.resolveChartLayout(compact, 296, true);
    assert.equal(mobile.pointRadius, 3);
    assert.equal(mobile.zeroRadius, 3.4);
  }
  assert.equal(chart.buildDiamondPath(20, 30, 3), 'M20 27 L23 30 L20 33 L17 30 Z');
  assert.match(chartSource, /visibleSeries\.ephyrae && measurement\.ephyraeCount != null && layout\.responsive/);
  assert.match(chartSource, /<circle className=\{`bio-trend-dot is-polyps/);
});

test('dense series keep every reading and partition hit areas instead of covering earlier points', () => {
  const dense = Array.from({ length: 31 }, (_, index) => reading(index, `2026-01-${String(index + 1).padStart(2, '0')}`, index, index));
  const result = geometry(296, false, dense);
  assert.equal(result.plottedMeasurements.length, 31);
  result.hitAreas.forEach((area, index) => {
    const x = result.xPosition(dense[index].date);
    assert.ok(area.width > 0 && area.width <= 44);
    assert.ok(area.left <= x && area.left + area.width >= x);
    if (index) assert.ok(result.hitAreas[index - 1].left + result.hitAreas[index - 1].width <= area.left + 1e-9);
  });
  const tied = chart.buildMeasurementHitAreas([44, 44, 50], 44, 284, true);
  assert.ok(tied.every((area) => area.width >= 0));
  assert.match(chartSource, /data-measurement-index=\{measurementIndex\}/);
  assert.match(chartSource, /handleMeasurementKey\(keyEvent, detail, measurementIndex\)/);
  assert.deepEqual(JSON.parse(JSON.stringify(chart.buildMeasurementHitAreas([44, 50], 44, 284, false))), [{ left: 31, width: 26 }, { left: 37, width: 26 }]);
});

test('responsive CSS contracts keep text readable, canvas fitted, and shape/color distinctions', () => {
  assert.doesNotMatch(css, /(?:width|min-width):\s*640px/);
  const rule = (selector) => {
    const start = css.lastIndexOf(`${selector} {`);
    assert.ok(start >= 0, `Missing rule: ${selector}`);
    return css.slice(start, css.indexOf('}', start) + 1);
  };
  const text = rule('.bio-trend.is-responsive');
  for (const token of ['axis', 'date', 'location']) assert.match(text, new RegExp(`--chart-${token}-size: \\.75rem`));
  assert.match(text, /--chart-legend-size: \.8125rem/);
  const svg = rule('.bio-trend.is-responsive .bio-trend-svg');
  for (const declaration of ['width: 100%', 'min-width: 0', 'height: auto', 'min-height: 0', 'aspect-ratio: auto']) assert.ok(svg.includes(declaration));
  assert.match(rule('.bio-trend.is-responsive .bio-trend-legend button'), /min-height: 44px/);
  assert.match(rule('.bio-trend.is-responsive .bio-trend-legend .is-ephyrae::before'), /rotate\(45deg\)/);
  assert.match(rule('.bio-trend.is-responsive .bio-trend-legend .is-polyps::before'), /border-radius: 50%/);
  assert.match(css, /\.bio-trend-dot\.is-polyps \{ --series-color: var\(--color-primary\)/);
  assert.match(css, /\.bio-trend-dot\.is-ephyrae \{ --series-color: var\(--color-ephyrae\)/);
  assert.match(chartSource, /entry\.contentRect\.width/);
  assert.match(chartSource, /canvas\.clientWidth - parseFloat\(style\.paddingLeft\) - parseFloat\(style\.paddingRight\)/);
  assert.doesNotMatch(chartSource, /className="bio-trend-location-band"/);
  assert.match(chartSource, /const chartId = useId\(\)/);
  assert.doesNotMatch(chartSource, /chartId\}-location-/);
});

test('persisted movements and derived period changes stay distinct, exact and deduplicated', () => {
  const periods = [
    { id: 1, name: 'A', startsAt: '2026-01-01T08:00:00Z', endsAt: '2026-03-01T10:00:00Z' },
    { id: 2, name: 'B', startsAt: '2026-03-01T10:00:00Z', endsAt: null },
  ];
  const movement = (id, date, instant) => ({ id, date, instant, title: 'Move', detail: 'A -> B', kind: 'movement' });
  const points = (events, locationList = periods) => chart.buildGeometry(readings, locationList, events, '2026-01-01', '2026-06-30', false).eventPoints;
  const kinds = (events, locationList) => [...points(events, locationList).map(({ event }) => event.kind)];

  // Period change without any BoxMovement: derived, never labelled as a recorded movement.
  assert.deepEqual(kinds([]), ['location_change']);
  assert.equal(points([])[0].event.title, '');
  assert.equal(points([])[0].coincidesWithBoundary, false);

  // Persisted movement away from every boundary keeps its own date, with no snapping.
  const isolated = points([movement(7, '2026-02-10', '2026-02-10T09:00:00Z')]);
  const geometry = chart.buildGeometry(readings, periods, [], '2026-01-01', '2026-06-30', false);
  assert.deepEqual([...isolated.map(({ event }) => event.kind)].sort(), ['location_change', 'movement']);
  assert.equal(isolated.find(({ event }) => event.kind === 'movement').x, geometry.xPosition('2026-02-10'));

  // Same instant: a single persisted marker, flagged as coinciding with the boundary.
  const same = points([movement(8, '2026-03-01', '2026-03-01T10:00:00Z')]);
  assert.equal(same.length, 1);
  assert.equal(same[0].event.kind, 'movement');
  assert.equal(same[0].coincidesWithBoundary, true);
  assert.equal(same[0].x, geometry.xPosition('2026-03-01'));
  // Same instant written with another UTC offset is still the same instant.
  assert.equal(points([movement(9, '2026-03-01', '2026-03-01T11:00:00+01:00')]).length, 1);

  // Adjacent but different timestamps: two markers, movement not moved onto the boundary.
  for (const [date, instant] of [['2026-02-28', '2026-02-28T10:00:00Z'], ['2026-03-02', '2026-03-02T10:00:00Z'], ['2026-03-01', '2026-03-01T10:00:01Z']]) {
    const adjacent = points([movement(10, date, instant)]);
    assert.deepEqual([...adjacent.map(({ event }) => event.kind)].sort(), ['location_change', 'movement'], instant);
    assert.equal(adjacent.find(({ event }) => event.kind === 'movement').x, geometry.xPosition(date));
    assert.equal(adjacent.find(({ event }) => event.kind === 'movement').coincidesWithBoundary, false);
  }

  // A date-only movement cannot prove it matches a timestamped boundary.
  assert.equal(points([movement(11, '2026-03-01')]).length, 2);
  // Date-only on both sides is the same instant at that precision.
  const dateOnly = [{ id: 1, name: 'A', startsAt: '2026-01-01', endsAt: '2026-03-01' }, { id: 2, name: 'B', startsAt: '2026-03-01', endsAt: null }];
  assert.deepEqual(kinds([movement(12, '2026-03-01')], dateOnly), ['movement']);
});

test('same-day events have separate native controls and exact event details in both languages', () => {
  const periods = [
    { id: 1, name: 'A', startsAt: '2026-01-01', endsAt: '2026-03-01T08:00:00Z' },
    { id: 2, name: 'B', startsAt: '2026-03-01T08:00:00Z', endsAt: null },
  ];
  for (const language of ['fr', 'en']) {
    const locale = compile(readFileSync(new URL(`../src/i18n/${language}.ts`, import.meta.url), 'utf8'))[language];
    let stateIndex = 0;
    const states = [];
    const jsx = (type, props) => ({ type, props });
    const component = compile(chartSource, {
      'react/jsx-runtime': { jsx, jsxs: jsx },
      react: {
        useState(initial) {
          const index = stateIndex++;
          if (!(index in states)) states[index] = initial;
          return [states[index], (value) => { states[index] = typeof value === 'function' ? value(states[index]) : value; }];
        },
        useMemo: (callback) => callback(), useRef: (current) => ({ current }),
        useId: () => 'overlap-test', useEffect() {}, useLayoutEffect() {},
      },
      'd3-scale': scales, 'd3-shape': shapes,
      '../utils/userIdentity': utility('userIdentity'), '../utils/dateFormat': utility('dateFormat'),
      '../utils/chartBiology': utility('chartBiology'), '../utils/chartLocations': utility('chartLocations'),
    }).default;
    const props = {
      startDate: '2026-01-01', endDate: '2026-06-30', measurements: readings, locations: periods,
      labels: { chartTitle: 'Chart', empty: 'Empty', ephyrae: 'Ephyrae', location: 'Location',
        locationChange: locale.locationPeriodChange, locationChangeNote: locale.locationPeriodChangeNote,
        missingReading: 'Missing', movement: locale.movementEvent, polyps: 'Polyps' },
      events: [{ id: 7, date: '2026-03-01', instant: '2026-03-01T10:00:00Z', title: locale.movementEvent, detail: 'A -> B', kind: 'movement' }],
    };
    function render() {
      stateIndex = 0;
      const nodes = [];
      function visit(node) {
        if (!node || typeof node !== 'object') return;
        if (Array.isArray(node)) return node.forEach(visit);
        nodes.push(node); visit(node.props?.children);
      }
      visit(component(props));
      return nodes;
    }
    const choices = () => render().filter((node) => node.props?.className?.startsWith('bio-trend-event-choice '));
    const tooltip = () => render().find((node) => node.props?.className?.startsWith('bio-trend-tooltip'));
    assert.equal(choices().length, 0, 'period boundaries no longer compete with persisted events');
    const markers = render().filter((node) => node.props?.className?.startsWith('bio-trend-event is-'));
    assert.equal(markers.length, 1);
    assert.equal(markers[0].props.tabIndex, 0);
    assert.equal(render().filter((node) => node.props?.className === 'bio-trend-event is-location-change').length, 0);
    props.events.push({ ...props.events[0], id: 11, instant: '2026-03-01T11:00:00Z' });
    assert.equal(choices().length, 2);
    for (const [index, expected, time] of [[0, locale.movementEvent, '10:00:00 UTC'], [1, locale.movementEvent, '11:00:00 UTC']]) {
      const button = choices()[index];
      assert.equal(button.type, 'button');
      assert.equal(button.props.type, 'button');
      assert.ok(button.props['aria-label'].startsWith(expected));
      assert.ok(button.props['aria-label'].includes(time));
      button.props.onMouseEnter();
      assert.ok(tooltip().props['aria-label'].includes(time), 'independent hover handler selects this detail');
      button.props.onMouseLeave();
      button.props.onFocus();
      assert.ok(tooltip().props['aria-label'].includes(time), 'keyboard focus selects this detail');
      button.props.onClick();
      assert.equal(choices()[index].props['aria-pressed'], true, 'click/tap pins this event');
      choices()[index].props.onKeyDown({ key: 'Escape' });
      assert.equal(tooltip(), undefined);
    }
    // Arbitrarily many legacy movements, including date-only records, keep independent controls.
    props.events.push(...[8, 9, 10].map((id) => ({ id, date: '2026-03-01', title: locale.movementEvent, kind: 'movement', detail: `Legacy ${id}` })));
    assert.equal(choices().length, 5);
    for (const button of choices().slice(2)) {
      assert.doesNotMatch(button.props['aria-label'], /UTC|00:00/);
      button.props.onClick();
      assert.equal(button.props.children[0].props.children, locale.movementEvent, 'visible choice text has no date');
      assert.ok(tooltip().props['aria-label'].startsWith(locale.movementEvent));
      button.props.onKeyDown({ key: 'Escape' });
    }
    // Exact coincidence removes only the derived transition, with no duplicate controls needed.
    props.events = [{ ...props.events[0], instant: '2026-03-01T09:00:00+01:00' }];
    assert.equal(choices().length, 0);
    assert.equal(render().filter((node) => node.props?.className === 'bio-trend-event is-movement').length, 1);
  }
  // The harness invokes handlers; it does not simulate browser SVG hit testing or native keyboard clicks.
  assert.match(css, /\.bio-trend-event\[data-overlapping\] \.bio-trend-event-hit-area \{ pointer-events: none; \}/);
  assert.match(css, /min-height: 44px/);
});

test('event dates preserve exact seconds and offsets without inventing historical times', () => {
  const event = (instant) => ({ id: 1, title: 'Move', date: '2026-03-01', instant });
  assert.match(chart.formatEventDate(event('2026-03-01T08:00:01.123Z')), /08:00:01\.123 UTC$/);
  assert.equal(chart.formatEventDate(event('2026-03-01T09:00:00+01:00')), chart.formatEventDate(event('2026-03-01T08:00:00Z')));
  assert.doesNotMatch(chart.formatEventDate(event(undefined)), /UTC|00:00/);
});

test('period change and movement notes are translated but not shown in the movement tooltip', () => {
  for (const language of ['fr', 'en']) {
    const locale = compile(readFileSync(new URL(`../src/i18n/${language}.ts`, import.meta.url), 'utf8'))[language];
    for (const key of ['locationPeriodChange', 'locationPeriodChangeNote', 'movementAtPeriodBoundaryNote']) assert.ok(locale[key], `${language}.${key}`);
    assert.notEqual(locale.locationPeriodChange, locale.movementEvent);
  }
  assert.doesNotMatch(chartSource, /labels\.locationChangeNote/);
  assert.doesNotMatch(chartSource, /labels\.movementAtBoundaryNote/);
  assert.doesNotMatch(chartSource, /closestBoundary/);
  assert.match(css, /\.bio-trend-event\.is-location-change \.bio-trend-event-line/);
});

const tracking = compile(`${readFileSync(new URL('../src/components/BoxTrackingChart.tsx', import.meta.url), 'utf8')}\nexport { prepareSharedChartData };`, {
  react: {}, 'react/jsx-runtime': {}, '../i18n': {},
  '../utils/chartWindow': utility('chartWindow'), '../utils/biologicalTimeline': utility('biologicalTimeline'),
  './BiologicalTrendChart': chart, './ChartWindowControls': {},
  '../hooks/useTemperatureHistory': {}, './TemperatureHistoryStatus': {},
  '../hooks/useHistoricalTemperatures': {}, './HistoricalTemperatureStatus': {}, '../utils/historicalTemperature': {},
});

function withParisCalendar(callback) {
  const previous = process.env.TZ;
  try {
    process.env.TZ = 'Europe/Paris';
    assert.equal(new Date('2026-10-03T22:00:00Z').getDate(), 4);
    callback();
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
}

const rangeEvent = (date) => ({ id: date, date, instant: date.length > 10 ? date : undefined, title: 'Move', kind: 'movement' });
const prepareRange = (events, start, end) => tracking.prepareSharedChartData(
  { measurements: [], polypStates: [] }, [], events, start, end,
).events;

for (const [label, date, included] of [
  ['selected end date', '2026-10-03T12:00:00Z', true],
  ['last millisecond of end date', '2026-10-03T23:59:59.999+02:00', true],
  ['midnight after end date', '2026-10-04T00:00:00+02:00', false],
  ['after end date', '2026-10-04T12:00:00Z', false],
  ['before start date', '2026-10-01T23:59:59.999+02:00', false],
  ['midnight on start date', '2026-10-02T00:00:00+02:00', true],
  ['another explicit UTC offset', '2026-10-04T00:59:59.999+03:00', true],
  ['UTC previous date but Paris start date', '2026-10-01T22:30:00Z', true],
  ['UTC end date but Paris following date', '2026-10-03T22:30:00Z', false],
  ['date-only end date', '2026-10-03', true],
  ['date-only before start', '2026-10-01', false],
  ['date-only after end', '2026-10-04', false],
]) {
  test(`inclusive chart range: ${label}`, () => withParisCalendar(() => {
    const event = rangeEvent(date);
    const prepared = prepareRange([event], '2026-10-02', '2026-10-03');
    const geometry = chart.buildGeometry([], [], [event], '2026-10-02', '2026-10-03', false);
    assert.equal(prepared.length, Number(included), 'tracking preparation');
    assert.equal(geometry.eventPoints.length, Number(included), 'shared export/overview geometry');
    if (included) {
      assert.equal(prepared[0], event);
      assert.equal(geometry.eventPoints[0].event, event);
      assert.equal(geometry.eventPoints[0].event.date, date, 'full timestamp retained');
      assert.equal(geometry.eventPoints[0].event.instant, event.instant);
      assert.equal(geometry.eventPoints[0].x, geometry.xPosition(date), 'existing positioning unchanged');
    }
  }));
}

test('inclusive range uses the next local midnight across DST rather than a fixed 24 hours', () => withParisCalendar(() => {
  for (const [start, end, last, next] of [
    ['2026-10-24', '2026-10-25', '2026-10-25T22:59:59.999Z', '2026-10-25T23:00:00Z'],
    ['2026-03-28', '2026-03-29', '2026-03-29T21:59:59.999Z', '2026-03-29T22:00:00Z'],
  ]) {
    const events = [rangeEvent(last), rangeEvent(next)];
    assert.deepEqual(Array.from(prepareRange(events, start, end), (event) => event.date), [last]);
    assert.deepEqual(Array.from(chart.buildGeometry([], [], events, start, end, false).eventPoints, ({ event }) => event.date), [last]);
  }
}));

test('end-date filtering retains exact-instant deduplication and date-only derived transitions', () => withParisCalendar(() => {
  const periods = [
    { id: 1, name: 'A', startsAt: '2026-10-01', endsAt: '2026-10-03T12:00:00Z' },
    { id: 2, name: 'B', startsAt: '2026-10-03T12:00:00Z', endsAt: null },
  ];
  const points = (events, locations = periods) => chart.buildGeometry([], locations, events, '2026-10-02', '2026-10-03', false).eventPoints;
  const movement = rangeEvent('2026-10-03T15:00:00+03:00');
  const exact = points(prepareRange([movement], '2026-10-02', '2026-10-03'));
  assert.equal(exact.length, 1);
  assert.equal(exact[0].event, movement);
  assert.equal(exact[0].coincidesWithBoundary, true);
  assert.equal(points([rangeEvent('2026-10-03T12:00:00.001Z')]).length, 2, 'one millisecond apart is not deduplicated');
  assert.equal(points([rangeEvent('2026-10-03')]).length, 2, 'date-only does not prove an exact timestamp match');
  const historical = points([], periods.map((period) => ({ ...period, startsAt: period.startsAt.slice(0, 10), endsAt: period.endsAt?.slice(0, 10) ?? null })));
  assert.equal(historical.length, 1);
  assert.equal(historical[0].event.kind, 'location_change');
  assert.equal(historical[0].event.date, '2026-10-03');
  assert.equal(historical[0].event.instant, undefined, 'no historical time invented');
}));
