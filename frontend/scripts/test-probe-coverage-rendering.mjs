import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import * as d3Scale from 'd3-scale';
import * as d3Shape from 'd3-shape';
import ts from 'typescript';

function load(path, imports = {}) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, require(name) {
    assert.ok(Object.hasOwn(imports, name), name);
    return imports[name];
  } });
  return exports;
}
const utils = load('../src/utils/probeChart.ts');
const gestures = load('../src/utils/probeGestures.ts');
const M = 60000;
const labels = { target: 'Target', minimum: 'Minimum', maximum: 'Maximum', latest: 'Latest instantaneous',
  noReadings: 'No readings', keyboardDescription: 'Browse', summary: (min, max) => `${min} to ${max} over the period.`,
    previewCoverage: 'Incomplete preview: statistics cover only cached contributions, not the whole selected period.',
  aggregateAverage: 'Aggregate average', aggregateCoverage: 'Contributing intervals and observations',
  partialCoverage: 'Partial interval: statistics include the whole contributing bucket', measurementCount: 'Measurements', rawReading: 'Raw observation' };
const bucket = (start, end, sum = '0', count = 2, min = -1, max = 1, last = end - 1) => [start, end, sum, count, min, max, last];

// Render real JSX and event handlers with controlled hook state, without a DOM.
// Native browser gesture and assistive-technology behavior still need browser QA.
function chart(overrides = {}) {
  const slots = [];
  let slot = 0;
  const hooks = {
    useState(initial) { const i = slot++; slots[i] ??= { value: typeof initial === 'function' ? initial() : initial === 0 ? 640 : initial };
      return [slots[i].value, (value) => { slots[i].value = value; }]; },
    useRef(initial) { const i = slot++; return slots[i] ??= { current: initial }; },
    useMemo(factory) { return factory(); }, useId() { return 'coverage-test'; }, useEffect() {},
  };
  const Component = load('../src/components/ProbeTemperatureChart.tsx', { react: hooks,
    'react/jsx-runtime': jsxRuntime, 'd3-scale': d3Scale, 'd3-shape': d3Shape,
    '../utils/probeChart': utils, '../utils/probeGestures': gestures,
    '../utils/environmentFormat': { formatTemperature: (value) => `${value} C` },
  }).default;
  let tree;
  const props = { code: 'Probe', locale: 'en', labels, points: [[0, 0, -1, 1]],
    pointSources: [[bucket(0, 10 * M)]], resolutionMinutes: 15,
    window: { start: 5 * M, end: 30 * M }, target: null, temperatureDomain: [-2, 4], cursorTime: null,
    onCursorTimeChange(time) { props.cursorTime = time; }, onZoom() {}, onResetZoom() {}, ...overrides };
  function render() { slot = 0; tree = Component(props); }
  function find(predicate, node = tree) {
    if (Array.isArray(node)) return node.flatMap((child) => find(predicate, child));
    if (!React.isValidElement(node)) return [];
    return [...(predicate(node) ? [node] : []), ...find(predicate, node.props.children ?? null)];
  }
  render();
  const svg = () => find((node) => node.type === 'svg')[0];
  const text = (node = tree) => Array.isArray(node) ? node.map(text).join(' ').replace(/\s+/g, ' ') : React.isValidElement(node)
    ? text(node.props.children ?? null) : node == null ? '' : String(node);
  return { props, render, find, svg, text };
}

test('raw observations use the half-open window, excluding its exact end', () => {
  const window = { start: 5 * M, end: 30 * M };
  const points = [[5 * M, 0, null, null], [30 * M, 2, null, null]];
  const sources = [[[5 * M, null, '0', 1, 0, 0, 5 * M]], [[30 * M, null, '2', 1, 2, 2, 30 * M]]];
  const entries = utils.probeCoveragePoints(points, sources, window);
  assert.equal(entries.length, 1);
  assert.equal(entries[0].point[0], window.start);
  assert.equal(utils.summarizeProbeCoverage(entries).maximum, 0);
  const view = chart({ points, pointSources: sources, window, cursorTime: window.end });
  assert.equal(view.find((node) => node.props.className === 'probe-chart-point').length, 1);
  assert.equal(view.find((node) => node.props.className === 'probe-chart-tooltip').length, 0);
});

test('partial and preview warnings scope the SVG summary without claiming the whole period', () => {
  const partial = chart();
  assert.match(partial.svg().props['aria-label'], /whole contributing bucket/);
  assert.match(partial.svg().props['aria-label'], /Minimum -1 C, Maximum 1 C/);
  assert.doesNotMatch(partial.svg().props['aria-label'], /over the period/);
  assert.match(partial.text(partial.find((node) => node.props.className === 'probe-chart-coverage-note')[0]), /whole contributing bucket/);
  const preview = chart({ isPartialPreview: true, window: { start: 0, end: 30 * M }, cursorTime: 0 });
  assert.match(preview.svg().props['aria-label'], /Incomplete preview/);
  assert.doesNotMatch(preview.svg().props['aria-label'], /over the period/);
  assert.match(preview.text(preview.find((node) => node.props.className === 'probe-chart-coverage-note')[0]), /not the whole selected period/);
  assert.match(preview.text(preview.find((node) => node.props.className === 'probe-chart-tooltip')[0]), /Incomplete preview/);
  const both = chart({ isPartialPreview: true });
  assert.match(both.svg().props['aria-label'], /whole contributing bucket/);
  assert.match(both.svg().props['aria-label'], /Incomplete preview/);
  const complete = chart({ window: { start: 0, end: 30 * M } });
  assert.match(complete.svg().props['aria-label'], /over the period/);
  assert.equal(complete.find((node) => node.props.className === 'probe-chart-coverage-note').length, 0);
  const empty = chart({ points: [], pointSources: [], isPartialPreview: true });
  assert.match(empty.svg().props['aria-label'], /Incomplete preview.*No readings/);
});

test('long raw and bucket provenance stays complete in a bounded keyboard-accessible scroll region', () => {
  for (const raw of [true, false]) {
    const sources = Array.from({ length: 60 }, (_, i) => raw
      ? [i * M, null, '0', 1, 0, 0, i * M]
      : bucket(i * 2 * M, (i * 2 + 1) * M));
    const view = chart({ pointSources: [sources], cursorTime: 0, window: { start: 0, end: 120 * M } });
    const tooltip = view.find((node) => node.props.className === 'probe-chart-tooltip')[0];
    assert.ok(tooltip);
    assert.equal(tooltip.props.tabIndex, 0);
    assert.equal(tooltip.props.role, 'region');
    assert.equal(tooltip.props['aria-hidden'], undefined);
    assert.equal(view.find((node) => node.type === 'small', tooltip).length, 61);
    const finalSourceTime = new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' }).format(sources[59][0]);
    assert.ok(view.text(tooltip).includes(finalSourceTime), 'Never truncate provenance');
    const canvas = view.find((node) => node.props.className === 'probe-chart-canvas')[0];
    const tooltipNode = {};
    canvas.ref.current = { contains: (node) => node === tooltipNode };
    view.svg().props.onPointerLeave({ pointerType: 'mouse', relatedTarget: tooltipNode });
    assert.equal(view.props.cursorTime, 0, 'Moving to the scroll region must not dismiss it');
    tooltip.props.onPointerLeave({ pointerType: 'touch' });
    assert.equal(view.props.cursorTime, 0, 'Touch scrolling keeps the inspected group');
    tooltip.props.onPointerLeave({ pointerType: 'mouse', relatedTarget: null });
    assert.equal(view.props.cursorTime, null);
  }
  const css = readFileSync(new URL('../src/styles/components/probe-charts.css', import.meta.url), 'utf8');
  const rule = css.match(/\.probe-chart-tooltip\s*\{([^}]+)\}/)[1];
  assert.match(rule, /max-height: min\(280px, calc\(100% - 8px\)\)/);
  assert.match(rule, /overflow-y: auto/);
  assert.match(rule, /pointer-events: auto/);
  assert.match(rule, /touch-action: pan-y/);
});

test('partial buckets preserve timestamps, full statistics, zero and visible inspection anchors', () => {
  const sources = [[bucket(0, 10 * M, '0', 2, -5, 8)]];
  const entries = utils.probeCoveragePoints([[0, 0, -5, 8]], sources, { start: 5 * M, end: 8 * M });
  assert.equal(entries[0].sources[0], sources[0][0]);
  assert.equal(entries[0].sources[0][0], 0);
  assert.equal(entries[0].anchors[0], 5 * M);
  assert.equal(entries[0].partial, true);
  const summary = utils.summarizeProbeCoverage(entries);
  assert.equal(summary.minimum, -5);
  assert.equal(summary.maximum, 8);
  assert.equal(summary.latest[1], 0);
  assert.equal(summary.latestAggregate, true);
});

test('interval glyphs retain real bounds and do not interpolate across a grouped gap', () => {
  const view = chart({ pointSources: [[bucket(0, 10 * M), bucket(20 * M, 25 * M)]] });
  const intervals = view.find((node) => node.props.className?.startsWith('probe-chart-interval'));
  assert.equal(intervals.length, 2);
  const x = d3Scale.scaleTime().domain([new Date(5 * M), new Date(30 * M)]).range([42, 626]);
  assert.equal(intervals[0].props.children[1].props.x1, x(0));
  assert.equal(intervals[0].props.children[1].props.x2, x(10 * M));
  assert.equal(view.find((node) => node.props.className === 'probe-chart-line').length, 0);
  assert.equal(view.find((node) => node.type === 'circle').length, 0, 'Bucket means are not instantaneous markers');
  view.props.cursorTime = 15 * M; view.render();
  assert.equal(view.find((node) => node.props.className === 'probe-chart-tooltip').length, 0);
  view.props.cursorTime = 5 * M; view.render();
  const tooltip = view.find((node) => node.props.className === 'probe-chart-tooltip')[0];
  assert.ok(tooltip);
  const text = view.text(tooltip);
  assert.match(text, /Aggregate average/);
  assert.match(text, /Measurements : 4/);
  assert.match(text, /whole contributing bucket/);
  assert.equal((text.match(/–/g) ?? []).length, 2, 'Separate full intervals preserve the gap');
  assert.doesNotMatch(view.text(), /Latest instantaneous/);
});

test('keyboard reaches clipped-left intervals and skips unknown gaps with a synchronized cursor', () => {
  const first = chart({ pointSources: [[bucket(0, 10 * M), bucket(20 * M, 25 * M)]] });
  const key = (name) => { first.svg().props.onKeyDown({ key: name, preventDefault() {} }); first.render(); };
  key('ArrowRight');
  assert.equal(first.props.cursorTime, 5 * M);
  assert.match(first.text(first.find((node) => node.props.className === 'sr-only')[0]), /Aggregate average/);
  const second = chart({ cursorTime: first.props.cursorTime });
  assert.equal(second.find((node) => node.props.className === 'probe-chart-tooltip').length, 1);
  key('ArrowRight'); assert.equal(first.props.cursorTime, 20 * M);
  key('ArrowLeft'); assert.equal(first.props.cursorTime, 5 * M);
});

test('mouse and touch inspect a partial interval without selecting its hidden start', () => {
  for (const pointerType of ['mouse', 'touch']) {
    const view = chart();
    const captures = new Set();
    const node = { focus() {}, getBoundingClientRect: () => ({ left: 0 }),
      setPointerCapture(id) { captures.add(id); }, hasPointerCapture: (id) => captures.has(id),
      releasePointerCapture(id) { captures.delete(id); } };
    const event = { pointerId: 1, pointerType, button: 0, clientX: 42, clientY: 40, timeStamp: 100, currentTarget: node };
    view.svg().props.onPointerDown(event); view.render();
    view.svg().props.onPointerUp(event); view.render();
    assert.equal(view.props.cursorTime, 5 * M);
    assert.equal(view.find((element) => element.props.className === 'probe-chart-tooltip').length, 1);
    assert.equal(captures.size, 0);
  }
});

test('grouped raw observations keep individual positions, not an instantaneous mean marker', () => {
  const view = chart({ points: [[0, 1, 0, 2]], pointSources: [[[6 * M, null, '0', 1, 0, 0, 6 * M],
    [9 * M, null, '2', 1, 2, 2, 9 * M]]], cursorTime: 6 * M });
  assert.equal(view.find((node) => node.props.className === 'probe-chart-point').length, 2);
  assert.equal(view.find((node) => node.props.className === 'probe-chart-line').length, 0);
  assert.equal(view.find((node) => node.props.className === 'probe-chart-hover')[0].props.children[1], null);
  assert.match(view.text(), /Aggregate average/);
  view.props.cursorTime = 7 * M; view.render();
  assert.equal(view.find((node) => node.props.className === 'probe-chart-tooltip').length, 0);
});

test('a raw observation is drawn once and source details remain open when entering the scrollable tooltip', () => {
  const raw = chart({ points: [[6 * M, 0, null, null]], pointSources: [[[6 * M, null, '0.00', 1, 0, 0, 6 * M]]],
    resolutionMinutes: 1, cursorTime: null });
  assert.equal(raw.find((node) => node.props.className === 'probe-chart-point').length, 1);
  const view = chart({ cursorTime: 5 * M });
  view.svg().props.onPointerLeave({ pointerType: 'mouse', relatedTarget: { closest: () => ({}) } });
  view.render();
  assert.equal(view.props.cursorTime, 5 * M);
  assert.equal(view.find((node) => node.props.className === 'probe-chart-tooltip').length, 1);
  view.svg().props.onPointerLeave({ pointerType: 'mouse', relatedTarget: null });
  assert.equal(view.props.cursorTime, null);
});

test('latest summary follows actual source last-measured time, not group boundaries', () => {
  const entries = utils.probeCoveragePoints([[0, 2, 1, 3], [10 * M, 0, 0, 0]],
    [[bucket(0, 30 * M, '4', 2, 1, 3, 29 * M)], [[10 * M, null, '0', 1, 0, 0, 10 * M]]],
    { start: 0, end: 30 * M });
  const summary = utils.summarizeProbeCoverage(entries);
  assert.equal(summary.latest[1], 2);
  assert.equal(summary.latestAggregate, true);
  const raw = chart({ points: [[0, 0, null, null]], pointSources: [[[6 * M, null, '0', 1, 0, 0, 6 * M]]], cursorTime: 6 * M });
  assert.match(raw.text(), /Latest instantaneous/);
  assert.match(raw.text(), /Raw observation/);
  assert.match(raw.text(), /0 C/);
});
