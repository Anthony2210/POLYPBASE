import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
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
  const chart = () => null;
  const trend = () => null;
  const view = harness('../src/components/ZonesView.tsx', {
    '../api/client': { ApiResourceCancelledError, ApiError: Error },
    '../hooks/useProbeMonitoring': { default: (value) => ({ monitoring: value.probe_monitoring, error: null, revalidate() { refreshes++; } }) },
    // Probe status is independent of the thermal summary; keep the hook in its
    // pending state (no summary, no error). Its lifecycle has dedicated tests.
    '../hooks/useThermalControl': { default: (value) => {
      assert.equal(value, zone, 'Thermal control reads the rendered zone');
      return { summary: undefined, error: null };
    } },
    '../utils/dateFormat': { formatDisplayDate: (date) => date, formatDisplayDateTime: (date) => date, getDocumentLocale: () => 'en' },
    '../utils/probeStatus': probeStatus,
    '../utils/errors': errors, '../utils/temperatureScale': { buildTemperatureScale: () => null },
    './BoxTrackingPreview': { default: noop }, './DetailBackButton': { default: noop },
    './ModalPortal': { default: noop }, './PageLoader': { default: noop }, './PolypbaseIcon': { default: noop },
    './ZoneProbeCharts': { ZoneProbeChart: chart, ZoneThermalTrend: trend, useZoneChartReadings: () => ({ initial: { settled: true, data: null } }) }, './ZoneMovementHistory': { ZoneRecentMovements: noop },
  }, {}, '\nexport { TemperatureControlPanel };', 'TemperatureControlPanel');
  view.render({ zone, canRecordManualTemperature: true, onRecordManualTemperature() {}, t: (key) => key });
  return { view, trend, get refreshes() { return refreshes; } };
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

test('a collected inactive probe keeps its trend, without a modal or an empty status row', () => {
  const panel = panelView({ id: 7, target_temperature_c: null, latest_temperature: null,
    probes: [{ is_collected: true, is_active: false }],
    probe_monitoring: { has_collected_probes: false, manual_entry_available: true },
  });
  const { view } = panel;
  assert.equal(view.find((node) => node.type === panel.trend).length, 1);
  assert.equal(view.find((node) => node.props.className === 'secondary-button zone-probe-readings-action').length, 0);
  assert.equal(view.find((node) => node.props.role === 'dialog').length, 0);
  assert.equal(view.find((node) => node.props.className === 'zone-probe-status').length, 0);
  assert.equal(view.find((node) => node.props['aria-label'] === 'manualTemperatureAction').length, 1);
});

for (const manual_entry_available of [true, false]) {
  test('active probe manual-entry affordance follows the server: ' + manual_entry_available, () => {
    const { view } = panelView({ id: 7, target_temperature_c: null, probes: [{ is_collected: true, is_active: true }],
      probe_monitoring: { has_collected_probes: true, manual_entry_available, last_reading_at: null, fallback_after_hours: 6 },
    });
    assert.equal(view.find((node) => node.props['aria-label'] === 'manualTemperatureAction').length, manual_entry_available ? 1 : 0);
  });
}

function seededMonitoring({ visibilityState = 'visible', initialRead } = {}) {
  const api = pendingApi();
  const timers = new Map();
  const events = {};
  const documentEvents = {};
  let timerId = 0;
  const documentStub = { visibilityState,
    addEventListener(name, fn) { documentEvents[name] = fn; }, removeEventListener(name) { delete documentEvents[name]; } };
  const globals = {
    AbortController, document: documentStub,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener(name, fn) { events[name] = fn; }, removeEventListener(name) { delete events[name]; },
  };
  const view = harness('../src/hooks/useProbeMonitoring.ts', {
    '../api/client': api, '../utils/errors': errors, '../utils/probeChart': chartUtils,
  }, globals, '\nexport function probe(args) { return useProbeMonitoring(args.zone, args.initialRead); }', 'probe');
  const zone = { id: 7, probes: [{ is_collected: true }], probe_monitoring: { has_collected_probes: true, manual_entry_available: false, last_reading_at: null, fallback_after_hours: 6 } };
  const props = { zone, initialRead };
  view.render(props); view.commit();
  return { api, view, timers, events, documentEvents, documentStub, zone, props,
    update(next) { Object.assign(props, next); view.render(props); view.commit(); view.render(props); } };
}
const served = { has_collected_probes: true, manual_entry_available: false, last_reading_at: new Date().toISOString(), fallback_after_hours: 6 };

test('monitoring waits for the first chart read instead of adding its own request', () => {
  const m = seededMonitoring({ initialRead: { settled: false, data: null } });
  assert.equal(m.api.requests.length, 0);
  assert.equal(m.timers.size, 0);
  m.view.unmount();
});

test('monitoring reuses the first chart status once, then keeps polling on its own schedule', async () => {
  const m = seededMonitoring({ initialRead: { settled: false, data: null } });
  m.update({ initialRead: { settled: true, data: { monitoring: served } } });
  assert.equal(m.api.requests.length, 0, 'The chart response already carried this status');
  assert.equal(m.view.tree.monitoring, served);
  assert.equal(m.timers.size, 1);
  const [timer] = m.timers.values();
  assert.equal(timer.delay, 60000);
  timer.fn();
  assert.equal(m.api.requests.length, 1, 'Monitoring stays independent after opening');
  assert.match(m.api.requests[0].path, /probe-readings/);
  const url = new URL(m.api.requests[0].path, 'https://qa.invalid');
  assert.equal(Date.parse(url.searchParams.get('end')) - Date.parse(url.searchParams.get('start')), 60000);
  m.view.tree.revalidate(); m.view.render(m.props); m.view.commit();
  assert.equal(m.api.requests.length, 2, 'An explicit retry reads the server again instead of reusing the seed');
  m.view.unmount();
});

test('monitoring reads for itself when the first chart read fails', () => {
  const m = seededMonitoring({ initialRead: { settled: false, data: null } });
  m.update({ initialRead: { settled: true, data: null } });
  assert.equal(m.api.requests.length, 1);
  m.view.unmount();
});

test('a hidden page does not poll monitoring, and returning refreshes it', () => {
  const m = seededMonitoring({ visibilityState: 'hidden', initialRead: { settled: true, data: { monitoring: served } } });
  const [timer] = m.timers.values();
  timer.fn();
  assert.equal(m.api.requests.length, 0);
  assert.equal(m.timers.size, 0, 'Nothing is rescheduled while hidden');
  m.documentStub.visibilityState = 'visible';
  m.documentEvents.visibilitychange();
  assert.equal(m.api.requests.length, 1);
  m.view.unmount();
});

test('unmounting cancels the pending monitoring timer and in-flight read', () => {
  const m = seededMonitoring();
  assert.equal(m.api.requests.length, 1, 'Without a shared chart read the hook still reads immediately');
  m.view.unmount();
  assert.equal(m.api.requests[0].options.signal.aborted, true);
  assert.equal(m.events.focus, undefined);
  assert.equal(m.documentEvents.visibilitychange, undefined);
});
