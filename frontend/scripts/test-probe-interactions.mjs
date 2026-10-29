import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import * as d3Scale from 'd3-scale';
import * as d3Shape from 'd3-shape';
import ts from 'typescript';

function loadModule(path, imports = {}, globals = {}, extra = '') {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8') + extra, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports, URLSearchParams,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), 'Unexpected import: ' + name);
      return imports[name];
    },
    ...globals,
  });
  return exports;
}

// Explicit render/commit phases exercise the frame before passive effects.
// Browser focus, touch and screen-reader behavior still require browser QA.
function harness(path, imports = {}, globals = {}, extra = '', exportName = 'default') {
  const slots = [];
  let cursor = 0;
  let effects = new Map();
  const sameDeps = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const useEffect = (effect, deps) => {
    const index = cursor++;
    if (!sameDeps(slots[index]?.deps, deps)) effects.set(index, { effect, deps });
  };
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, (next) => {
        slots[index].value = typeof next === 'function' ? next(slots[index].value) : next;
      }];
    },
    useRef(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initial };
      return slots[index];
    },
    useId() { return 'probe-test-' + cursor++; },
    useMemo(factory, deps) {
      const index = cursor++;
      if (!sameDeps(slots[index]?.deps, deps)) slots[index] = { deps, value: factory() };
      return slots[index].value;
    },
    useCallback(callback, deps) { return hooks.useMemo(() => callback, deps); },
    useEffect,
    useLayoutEffect: useEffect,
  };
  const component = loadModule(path, { react: hooks, 'react/jsx-runtime': jsxRuntime, ...imports }, globals, extra)[exportName];
  let props;
  let tree;
  return {
    get tree() { return tree; },
    render(next = props) {
      props = next; cursor = 0; effects = new Map(); tree = component(props); return tree;
    },
    commit() {
      for (const [index, { effect, deps }] of effects) {
        slots[index]?.cleanup?.();
        slots[index] = { deps, cleanup: effect() };
      }
      effects = new Map();
    },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
    find(predicate) {
      function visit(node) {
        if (Array.isArray(node)) return node.flatMap(visit);
        if (!React.isValidElement(node)) return [];
        return [...(predicate(node) ? [node] : []), ...visit(node.props.children)];
      }
      return visit(tree);
    },
  };
}

const chartUtils = loadModule('../src/utils/probeChart.ts');
const gestureUtils = loadModule('../src/utils/probeGestures.ts');
const probeStatus = loadModule('../src/utils/probeStatus.ts');
class ApiResourceCancelledError extends Error {}
const errors = { getErrorMessage: (error) => error.message };
const tick = () => new Promise((resolve) => setImmediate(resolve));
const noop = () => null;
const pendingApi = () => {
  const requests = [];
  return {
    requests, ApiResourceCancelledError,
    apiGet(path, options) {
      return new Promise((resolve, reject) => requests.push({ path, options, resolve, reject }));
    },
  };
};
const chartComponent = () => null;
const response = (value = 0) => ({
  target_temperature_c: '0', resolution_minutes: 1,
  probes: [1, 2].map((id) => ({ id, code: 'Probe ' + id, is_active: true,
    points: [[Date.now() - 1000, value + id - 1, null, null]] })),
});
const zone = { id: 7, name: 'Zone', target_temperature_c: '0',
  probes: [1, 2].map((id) => ({ id, code: 'Probe ' + id, is_collected: true, is_active: true })) };

function modalView(snapshot = {}, { wasInert = false } = {}) {
  const model = { snapshot, calls: [], closed: 0, handlers: {}, root: { inert: wasInert } };
  const document = { activeElement: null, getElementById: (id) => id === 'root' ? model.root : null };
  const view = harness('../src/components/ProbeReadingsModal.tsx', {
    '../hooks/useProbeReadings': { default: (zoneId, requested) => {
      model.calls.push({ zoneId, window: requested });
      return { data: null, window: requested, isLoading: true, error: null, retry: noop, revalidate: noop, ...model.snapshot };
    } },
    '../utils/dateFormat': { getDocumentLocale: () => 'en' },
    '../utils/errors': errors,
    '../utils/probeChart': chartUtils,
    './ModalPortal': { default: noop }, './PolypbaseIcon': { default: noop },
    './ProbeTemperatureChart': { default: chartComponent },
  }, {
    document,
    addEventListener(name, fn) { model.handlers[name] = fn; },
    removeEventListener(name, fn) { if (model.handlers[name] === fn) delete model.handlers[name]; },
  });
  view.render({ zone, onClose() { model.closed++; }, t: (key) => key });
  return { view, model, document, charts: () => view.find((node) => node.type === chartComponent),
    button: (label) => view.find((node) => node.type === 'button'
      && (node.props['aria-label'] === label || node.props.children === label))[0] };
}

for (const action of ['previous', 'period', 'zoom']) {
  test('charts keep their keyed slots and render the hook snapshot during ' + action, () => {
    const renderedWindow = { start: Date.now() - 86400000, end: Date.now() - 60000 };
    const data = response(0);
    const { view, model, charts, button } = modalView({ data, window: renderedWindow, isLoading: false });
    const original = charts();
    const container = view.find((node) => node.props.className === 'probe-readings-charts')[0];
    view.commit();
    if (action === 'previous') button('probeWindowPrevious').props.onClick();
    if (action === 'period') button('probePeriod_7d').props.onClick();
    if (action === 'zoom') original[0].props.onZoom(Date.now() - 2 * 3600000, Date.now() - 3600000);
    model.snapshot = { data, window: renderedWindow, isLoading: true };
    view.render();
    assert.notDeepEqual(model.calls.at(-1).window, renderedWindow, 'The requested and rendered windows differ');
    assert.equal(model.calls.at(-1).zoneId, zone.id);
    assert.deepEqual(charts().map((node) => [node.type, node.key]), original.map((node) => [node.type, node.key]));
    const loadingContainer = view.find((node) => node.props.className === 'probe-readings-charts')[0];
    assert.equal(loadingContainer.type, container.type);
    assert.equal(loadingContainer.key, container.key);
    assert.equal(loadingContainer.props['aria-busy'], true);
    assert.equal(view.find((node) => node.props.className === 'probe-readings-progress is-loading').length, 1);
    for (const [index, node] of charts().entries()) {
      assert.equal(node.props.points, data.probes[index].points);
      assert.equal(node.props.window, renderedWindow, 'Never pair retained data with the newly requested axis');
      assert.equal(node.props.isInitialLoading, false);
    }
    const range = view.find((node) => node.props.className === 'probe-readings-range')[0];
    const format = new Intl.DateTimeFormat('en', { dateStyle: 'medium', timeStyle: 'short' });
    assert.equal(range.props.children[0], format.format(renderedWindow.start));
    assert.equal(range.props.children[2], format.format(renderedWindow.end));
    model.snapshot = { data: response(22), window: model.calls.at(-1).window, isLoading: false };
    view.render();
    assert.deepEqual(charts().map((node) => node.key), original.map((node) => node.key));
    assert.equal(charts()[0].props.points[0][1], 22);
    assert.equal(charts()[0].props.window, model.snapshot.window);
    view.unmount();
  });
}

test('initial loading, data and nonblocking error retain the same chart keys', () => {
  let retries = 0;
  const { view, model, charts, button } = modalView();
  const initial = charts();
  assert.equal(initial.length, 2);
  assert.ok(initial.every((node) => node.props.points.length === 0 && node.props.isInitialLoading));
  view.commit();
  model.snapshot = { data: response(), isLoading: false };
  view.render();
  assert.deepEqual(charts().map((node) => node.key), initial.map((node) => node.key));
  const dataPoints = charts().map((node) => node.props.points);
  model.snapshot = { ...model.snapshot, error: new Error('Offline'), retry() { retries++; } };
  view.render();
  assert.deepEqual(charts().map((node) => node.key), initial.map((node) => node.key));
  assert.deepEqual(charts().map((node) => node.props.points), dataPoints);
  const status = view.find((node) => node.props.className === 'probe-readings-status')[0];
  assert.equal(status.props.role, 'status');
  assert.equal(status.props.children[0].props.children, 'Offline');
  button('probeRetry').props.onClick(); assert.equal(retries, 1);
  view.unmount();
});

test('both probes share cursor, axes, description and reset callbacks without visible gesture controls', () => {
  const { view, charts } = modalView({ data: response(), isLoading: false });
  let [first, second] = charts();
  assert.equal(first.props.cursorTime, null);
  assert.equal(first.props.onCursorTimeChange, second.props.onCursorTimeChange);
  assert.equal(first.props.onResetZoom, second.props.onResetZoom);
  assert.equal(first.props.window, second.props.window);
  assert.equal(first.props.temperatureDomain, second.props.temperatureDomain);
  assert.equal(first.props.target, 0);
  assert.equal(first.props.labels.keyboardDescription, 'probeChartKeyboardDescription');
  assert.equal('reading' in first.props.labels, false);
  first.props.onCursorTimeChange(0); view.render();
  assert.ok(charts().every((node) => node.props.cursorTime === 0));
  charts()[1].props.onCursorTimeChange(null); view.render();
  assert.ok(charts().every((node) => node.props.cursorTime === null));
  assert.equal(view.find((node) => ['input', 'details', 'form', 'output'].includes(node.type)).length, 0);
  const text = view.find(() => true).flatMap((node) => React.Children.toArray(node.props.children))
    .filter((child) => typeof child === 'string').join(' ');
  assert.doesNotMatch(text, /probeZoomHint|probeChooseReading|probeResolution|probeZoomStart|probeZoomEnd/);
});

test('monitoring revalidates at the boundary, retains server decisions on failure and aborts late reads', async () => {
  const api = pendingApi();
  const timers = new Map();
  const events = {};
  let timerId = 0;
  const globals = {
    AbortController,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    document: { visibilityState: 'visible', addEventListener() {}, removeEventListener() {} },
    addEventListener(name, fn) { events[name] = fn; }, removeEventListener(name) { delete events[name]; },
  };
  const view = harness('../src/hooks/useProbeMonitoring.ts', {
    '../api/client': api, '../utils/errors': errors, '../utils/probeChart': chartUtils,
  }, globals);
  const fresh = { has_collected_probes: true, manual_entry_available: false,
    last_reading_at: new Date(Date.now() - 6 * 3600000 + 10000).toISOString(), fallback_after_hours: 6 };
  const zone = { id: 7, probes: [{ is_collected: true }], probe_monitoring: fresh };
  view.render(zone); view.commit();
  api.requests[0].resolve({ monitoring: fresh }); await tick(); view.render();
  assert.equal(view.tree.monitoring.manual_entry_available, false);
  const timer = [...timers.values()][0]; assert.ok(timer.delay > 0 && timer.delay <= 11000);
  timer.fn();
  assert.equal(view.tree.monitoring.manual_entry_available, false, 'A timer cannot grant manual access');
  const stale = { ...fresh, manual_entry_available: true };
  api.requests[1].resolve({ monitoring: stale }); await tick(); view.render();
  assert.equal(view.tree.monitoring.manual_entry_available, true);
  events.focus(); api.requests[2].reject(new Error('Offline')); await tick(); view.render();
  assert.equal(view.tree.error, 'Offline');
  assert.equal(view.tree.monitoring.manual_entry_available, true);
  view.tree.revalidate(); view.render(); view.commit(); view.render();
  assert.equal(view.tree.monitoring.manual_entry_available, true, 'Retry retains the last server status');
  const late = api.requests[3];
  view.unmount(); assert.equal(late.options.signal.aborted, true);
  late.resolve({ monitoring: fresh }); await tick(); view.render();
  assert.equal(view.tree.monitoring.manual_entry_available, true);
});

function panelView(zone) {
  let refreshes = 0;
  const modal = () => null;
  const view = harness('../src/components/ZonesView.tsx', {
    '../api/client': { ApiResourceCancelledError, ApiError: Error },
    '../hooks/useProbeMonitoring': { default: (value) => ({ monitoring: value.probe_monitoring, error: null, revalidate() { refreshes++; } }) },
    '../utils/dateFormat': { formatDisplayDate: (date) => date, formatDisplayDateTime: (date) => date, getDocumentLocale: () => 'en' },
    '../utils/probeStatus': probeStatus,
    '../utils/errors': errors, '../utils/temperatureScale': { buildTemperatureScale: () => null },
    './BoxTrackingPreview': { default: noop }, './DetailBackButton': { default: noop },
    './ModalPortal': { default: noop }, './PageLoader': { default: noop }, './PolypbaseIcon': { default: noop },
    './ProbeReadingsModal': { default: modal }, './ZoneMovementHistory': { ZoneRecentMovements: noop },
  }, {}, '\nexport { TemperatureControlPanel };', 'TemperatureControlPanel');
  view.render({ zone, canRecordManualTemperature: true, onRecordManualTemperature() {}, t: (key) => key });
  return { view, modal, get refreshes() { return refreshes; } };
}

for (const origin of [true, false, undefined]) {
  test('aggregate provenance is no longer displayed: ' + origin, () => {
    const { view } = panelView({ id: 7, target_temperature_c: null, probes: [{ is_collected: true, is_active: true }],
      latest_temperature: { date: '2026-10-29', average_temperature_c: 0, measurement_count: 1, computed_from_probes: origin },
      probe_monitoring: { has_collected_probes: true, manual_entry_available: false },
    });
    assert.equal(view.find((node) => node.props.className === 'zone-probe-source').length, 0);
    assert.equal(view.find((node) => node.props.children === 'probeComputedFromProbes').length, 0);
    assert.equal(view.find((node) => node.props.className === 'zone-probe-reading').length, 1);
  });
}

test('inactive probes retain history access and closing returns focus to its trigger', () => {
  const panel = panelView({ id: 7, target_temperature_c: null, latest_temperature: null,
    probes: [{ is_collected: true, is_active: false }],
    probe_monitoring: { has_collected_probes: false, manual_entry_available: true },
  });
  const { view } = panel;
  let focused = 0;
  const trigger = view.find((node) => node.props.className === 'secondary-button zone-probe-readings-action')[0];
  trigger.ref.current = { focus() { focused++; } };
  view.commit(); trigger.props.onClick(); view.render(); view.commit();
  view.find((node) => node.type === panel.modal)[0].props.onClose(); view.render(); view.commit();
  assert.equal(focused, 1); assert.equal(panel.refreshes, 1);
  assert.equal(view.find((node) => node.props['aria-label'] === 'manualTemperatureAction').length, 1);
  assert.equal(view.find((node) => node.props.className === 'zone-probe-stale').length, 0);
});

for (const manual_entry_available of [true, false]) {
  test('active probe manual-entry affordance follows the server: ' + manual_entry_available, () => {
    const { view } = panelView({ id: 7, target_temperature_c: null, probes: [{ is_collected: true, is_active: true }],
      probe_monitoring: { has_collected_probes: true, manual_entry_available, last_reading_at: null, fallback_after_hours: 6 },
    });
    assert.equal(view.find((node) => node.props['aria-label'] === 'manualTemperatureAction').length, manual_entry_available ? 1 : 0);
  });
}


function chartView(overrides = {}) {
  const events = new Map();
  const removed = [];
  const captures = new Set();
  const svgNode = {
    focus() {}, getBoundingClientRect: () => ({ left: 0, height: 300 }),
    setPointerCapture(id) { captures.add(id); },
    hasPointerCapture: (id) => captures.has(id), releasePointerCapture(id) { captures.delete(id); },
    addEventListener(name, fn, options) { events.set(name, { fn, options }); },
    removeEventListener(name, fn) { removed.push({ name, fn }); events.delete(name); },
  };
  const calls = { cursor: [], zoom: [], reset: 0 };
  const props = {
    code: 'Probe', locale: 'en', labels: {
      summary: () => 'Summary', keyboardDescription: 'Arrow keys browse; plus and minus zoom',
      target: 'Target', minimum: 'Minimum', maximum: 'Maximum', latest: 'Latest', noReadings: 'No readings',
    },
    points: [[0, 0, null, null], [60000, 1, null, null]], resolutionMinutes: 1,
    temperatureDomain: [-1, 2], target: 0, window: { start: 0, end: 120000 }, cursorTime: null,
    onCursorTimeChange(time) { calls.cursor.push(time); props.cursorTime = time; },
    onZoom(...args) { calls.zoom.push(args); }, onResetZoom() { calls.reset++; },
    ...overrides,
  };
  const view = harness('../src/components/ProbeTemperatureChart.tsx', {
    'd3-scale': d3Scale, 'd3-shape': d3Shape,
    '../utils/environmentFormat': { formatTemperature: (value) => String(value) + ' C' },
    '../utils/probeChart': chartUtils, '../utils/probeGestures': gestureUtils,
  }, { ResizeObserver: class { observe() {} disconnect() {} } });
  view.render(props);
  const svg = () => view.find((node) => node.type === 'svg')[0];
  svg().ref.current = svgNode;
  view.find((node) => node.props.className === 'probe-chart-canvas')[0].ref.current = { clientWidth: 640 };
  view.commit(); view.render(props);
  return { view, props, calls, svg, svgNode, events, removed, captures,
    render: () => view.render(props),
    event: (extra = {}) => ({ pointerId: 1, pointerType: 'touch', clientX: 42, clientY: 40,
      timeStamp: 100, currentTarget: svgNode, ...extra }),
  };
}

test('touch tooltip uses the controlled cursor and survives touch leave, while mouse leave clears it', () => {
  const { view, props, calls, svg, event, render, captures } = chartView();
  const tap = event();
  svg().props.onPointerDown(tap); render();
  svg().props.onPointerUp(tap); render();
  assert.equal(captures.size, 0);
  assert.equal(props.cursorTime, 0);
  svg().props.onPointerLeave(tap); render();
  const tooltip = view.find((node) => node.props.className === 'probe-chart-tooltip')[0];
  assert.ok(tooltip);
  assert.equal(tooltip.props['aria-hidden'], 'true');
  assert.equal(tooltip.props.children[1].props.children, '0 C');
  svg().props.onPointerLeave(event({ pointerType: 'mouse' })); render();
  assert.equal(calls.cursor.at(-1), null);
  assert.equal(view.find((node) => node.props.className === 'probe-chart-tooltip').length, 0);
  view.unmount();
});

test('SVG frame and native wheel listener survive empty, loading and replaced points', () => {
  const { view, props, svg, events, removed, render } = chartView({ points: [], isInitialLoading: true });
  const original = svg();
  const wheel = events.get('wheel');
  assert.equal(wheel.options.passive, false);
  assert.equal(original.props.tabIndex, 0);
  assert.equal(original.props['aria-description'], props.labels.keyboardDescription);
  assert.equal(view.find((node) => node.props.className === 'probe-chart-empty').length, 0);
  for (const state of [
    { points: [], isInitialLoading: false },
    { points: [[0, 0, -1, 1]], isInitialLoading: false },
    { points: [], isInitialLoading: true },
  ]) {
    Object.assign(props, state); render(); view.commit();
    assert.equal(svg().type, original.type);
    assert.equal(svg().key, original.key);
    assert.equal(svg().ref, original.ref, 'Preserve the mounted SVG ref across point changes');
    assert.equal(svg().props.height, original.props.height);
    assert.equal(events.get('wheel'), wheel);
  }
  assert.equal(view.find((node) => ['input', 'output'].includes(node.type)).length, 0);
  view.unmount();
  assert.equal(events.size, 0);
  assert.equal(removed[0].fn, wheel.fn);
});

test('keyboard reads zero and bucket extrema through the tooltip instead of a range input', () => {
  const { view, props, calls, svg, render } = chartView({
    points: [[0, 20, 19, 21], [60000, 0, -1, 1]], temperatureDomain: [-2, 22], cursorTime: 0,
  });
  let prevented = 0;
  svg().props.onKeyDown({ key: 'ArrowRight', preventDefault() { prevented++; } }); render();
  assert.equal(props.cursorTime, 60000);
  const tooltip = view.find((node) => node.props.className === 'probe-chart-tooltip')[0];
  assert.equal(tooltip.props.children[1].props.children, '0 C');
  assert.match(view.find((node) => node.props.className === 'sr-only')[0].props.children, /0 C/);
  assert.ok(tooltip.props.children[0].props.children);
  const extrema = React.Children.toArray(tooltip.props.children[2].props.children).join('');
  assert.match(extrema, /Minimum -1 C, Maximum 1 C/);
  svg().props.onKeyDown({ key: '+', preventDefault() { prevented++; } });
  assert.equal(calls.zoom.length, 1);
  svg().props.onKeyDown({ key: 'Escape', preventDefault() { assert.fail('Escape belongs to the parent'); } });
  assert.equal(calls.reset, 0); assert.equal(prevented, 2);
  view.unmount();
});

test('period selection and gesture reset preserve their base period and navigation duration', () => {
  const { view, model, charts, button } = modalView({ data: response(), isLoading: false });
  const initial = { ...model.calls.at(-1).window };
  assert.equal(button('probeWindowNext').props.disabled, true);
  assert.equal(button('probeResetZoom'), undefined);
  const slot = view.find((node) => node.props.className === 'probe-reset-slot')[0];
  for (const [period, days] of [['24h', 1], ['7d', 7], ['30d', 30], ['90d', 90]]) {
    button('probePeriod_' + period).props.onClick(); view.render();
    const base = model.calls.at(-1).window;
    assert.equal(base.end, initial.end);
    assert.equal(base.end - base.start, days * 86400000);
    assert.equal(button('probePeriod_' + period).props['aria-pressed'], true);
    charts()[0].props.onCursorTimeChange(base.start);
    charts()[0].props.onZoom(base.end - 7200000, base.end - 3600000); view.render();
    assert.equal(button('probePeriod_' + period).props['aria-pressed'], false);
    assert.ok(button('probeResetZoom'));
    assert.equal(view.find((node) => node.props.className === 'probe-reset-slot')[0].key, slot.key);
    const zoom = { ...model.calls.at(-1).window };
    button('probeWindowPrevious').props.onClick(); view.render();
    const shifted = model.calls.at(-1).window;
    assert.equal(shifted.end - shifted.start, zoom.end - zoom.start);
    assert.equal(shifted.end, zoom.start);
    assert.equal(button('probeWindowNext').props.disabled, false);
    assert.ok(charts().every((node) => node.props.cursorTime === null));
    button('probeWindowNext').props.onClick(); view.render();
    assert.deepEqual({ ...model.calls.at(-1).window }, zoom);
    charts()[1].props.onResetZoom(); view.render();
    assert.deepEqual({ ...model.calls.at(-1).window }, { ...base });
    assert.equal(button('probeResetZoom'), undefined);
    assert.equal(button('probePeriod_' + period).props['aria-pressed'], true);
  }
  charts()[0].props.onZoom(initial.end - 7200000, initial.end - 3600000); view.render();
  button('probeResetZoom').props.onClick(); view.render();
  assert.equal(model.calls.at(-1).window.end - model.calls.at(-1).window.start, 90 * 86400000);
});

for (const wasInert of [false, true]) {
  test('focus trap wraps visible controls, Escape closes and inert is restored: ' + wasInert, () => {
    const { view, model, document } = modalView({}, { wasInert });
    const focus = [];
    const first = { getClientRects: () => [1], focus() { focus.push('first'); } };
    const last = { getClientRects: () => [1], focus() { focus.push('last'); } };
    const hidden = { getClientRects: () => [], focus() { assert.fail('Hidden control received focus'); } };
    let selector;
    const dialog = { focus(options) { focus.push('dialog'); assert.equal(options.preventScroll, true); },
      contains: (element) => [first, last, hidden, dialog].includes(element),
      querySelectorAll(value) { selector = value; return [first, hidden, last]; } };
    view.find((node) => node.props.role === 'dialog')[0].ref.current = dialog;
    document.activeElement = dialog;
    view.commit();
    assert.equal(model.root.inert, true);
    assert.deepEqual(focus, ['dialog']);
    let prevented = 0;
    function tab(active, shiftKey) {
      document.activeElement = active;
      model.handlers.keydown({ key: 'Tab', shiftKey, preventDefault() { prevented++; } });
    }
    tab(dialog, true); assert.equal(focus.at(-1), 'last');
    tab(first, true); assert.equal(focus.at(-1), 'last');
    tab(last, false); assert.equal(focus.at(-1), 'first');
    tab({}, false); assert.equal(focus.at(-1), 'first');
    tab({}, true); assert.equal(focus.at(-1), 'last');
    assert.equal(prevented, 5);
    tab(first, false); assert.equal(prevented, 5, 'Normal Tab must remain native');
    assert.match(selector, /button:not\(:disabled\)/);
    assert.match(selector, /\[tabindex="0"\]/);
    model.handlers.keydown({ key: 'Escape' }); assert.equal(model.closed, 1);
    view.unmount();
    assert.equal(model.root.inert, wasInert);
    assert.equal(model.handlers.keydown, undefined);
  });
}


test('shared cursor never assigns a neighbouring measurement to another timestamp', () => {
  const first = chartView({ points: [[0, 0, null, null], [60000, 25, null, null]], cursorTime: 0 });
  const second = chartView({ points: [[60000, 26, null, null]], cursorTime: 0 });
  const tooltip = (chart) => chart.view.find((node) => node.props.className === 'probe-chart-tooltip')[0];
  assert.equal(tooltip(first).props.children[1].props.children, '0 C');
  assert.equal(tooltip(second), undefined, 'No floating tooltip when this probe has no measurement at the cursor');
  first.svg().props.onPointerMove(first.event({ pointerType: 'mouse', clientX: 46 })); first.render();
  assert.equal(first.props.cursorTime, 0, 'Hover snaps to the actual sampled instant');
  first.view.unmount(); second.view.unmount();
});


test('empty graphs and gaps never open a floating tooltip', () => {
  for (const points of [[], [[0, 0, null, null], [3600000, 25, null, null]]]) {
    const chart = chartView({ points, cursorTime: 1800000, window: { start: 0, end: 7200000 } });
    assert.equal(chart.view.find((node) => node.props.className === 'probe-chart-tooltip').length, 0);
    assert.equal(chart.view.find((node) => node.type === 'svg').length, 1);
    chart.view.unmount();
  }
});


test('wheel zoom requires pointer presence, not a graph scrolled beneath the pointer', () => {
  const chart = chartView();
  const wheel = chart.events.get('wheel').fn;
  let prevented = 0;
  const event = { deltaY: -100, deltaMode: 0, clientX: 200, clientY: 40, preventDefault() { prevented++; } };
  wheel(event);
  assert.equal(chart.calls.zoom.length, 0);
  assert.equal(prevented, 0);
  chart.svg().props.onPointerMove(chart.event({ pointerType: 'mouse', clientX: 200 }));
  chart.render(); wheel(event);
  assert.equal(chart.calls.zoom.length, 1);
  assert.equal(prevented, 1);
  chart.svg().props.onPointerLeave(chart.event({ pointerType: 'mouse' }));
  chart.render(); wheel(event);
  assert.equal(chart.calls.zoom.length, 1);
  assert.equal(prevented, 1, 'Wheel retargeted after native scroll must not zoom or block scrolling');
  chart.view.unmount();
});


test('keyboard zoom ignores a cursor outside the current pinch window', () => {
  const chart = chartView({ window: { start: 0, end: 7200000 }, cursorTime: -36000000 });
  chart.svg().props.onKeyDown({ key: '+', preventDefault() {} });
  const [start, end] = chart.calls.zoom[0];
  assert.equal((start + end) / 2, 3600000);
  assert.ok(start >= 0 && end <= 7200000);
  chart.view.unmount();
});
