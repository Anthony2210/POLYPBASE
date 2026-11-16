import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import ts from 'typescript';

// Executes the real location chart read, its chart view, probe monitoring and the
// loader together, as the location page composes them, with deferred responses and
// a controlled clock. Only transport and leaf views are stubbed. The separate
// six-hour trend is no longer mounted on that page (2026-11-12), so the opening
// read is the two-hour chart window and nothing falls back to a trend read.
const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const START = Date.UTC(2026, 10, 9, 12);
const flushPromises = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };

function bench({ visibilityState = 'visible' } = {}) {
  let now = START;
  let timerId = 0;
  const timers = new Map();
  const windowEvents = new Map();
  const documentEvents = new Map();
  let mounted = true;
  const document = {
    visibilityState, documentElement: { lang: 'en' },
    addEventListener: (name, fn) => documentEvents.set(fn, name),
    removeEventListener: (name, fn) => documentEvents.delete(fn),
  };
  const addTimer = (fn, delay, every = null) => { timers.set(++timerId, { fn, at: now + delay, every }); return timerId; };
  const removeTimer = (id) => timers.delete(id);
  class FakeDate extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const globals = {
    AbortController, URLSearchParams, Intl, Math, Number, Set, Map, Date: FakeDate, document,
    setTimeout: addTimer, clearTimeout: removeTimer,
    setInterval: (fn, delay) => addTimer(fn, delay, delay), clearInterval: removeTimer,
    addEventListener: (name, fn) => windowEvents.set(name, fn), removeEventListener: (name) => windowEvents.delete(name),
  };

  // A minimal hook runtime: render, commit effects, repeat while state changes.
  let slots = [];
  let cursor = 0;
  let pending = [];
  let dirty = false;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const memo = (factory, deps) => {
    const index = cursor++;
    if (!same(slots[index]?.deps, deps)) slots[index] = { deps, value: factory() };
    return slots[index].value;
  };
  const effect = (run, deps) => {
    const index = cursor++;
    if (same(slots[index]?.deps, deps)) return;
    pending.push(() => { slots[index]?.cleanup?.(); slots[index] = { deps, cleanup: run() }; });
  };
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: typeof initial === 'function' ? initial() : initial };
      const slot = slots[index];
      return [slot.value, (next) => {
        const value = typeof next === 'function' ? next(slot.value) : next;
        if (!Object.is(value, slot.value)) { slot.value = value; dirty = true; }
      }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useMemo: memo,
    useCallback: (callback, deps) => memo(() => callback, deps),
    useEffect: effect,
    useLayoutEffect: effect,
    useId: () => { cursor++; return 'test-id'; },
  };

  function load(path, imports = {}) {
    const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
    });
    const exports = {};
    vm.runInNewContext(outputText, { exports, ...globals, require(name) {
      assert.ok(Object.hasOwn(imports, name), `Unexpected import: ${name}`);
      return imports[name];
    } });
    return exports;
  }

  class ApiResourceCancelledError extends Error {}
  const requests = [];
  const context = { signal: new AbortController().signal, controller: null };
  context.controller = new AbortController();
  context.signal = context.controller.signal;
  const client = {
    ApiResourceCancelledError,
    getOrganizationResourceSignal: () => context.signal,
    apiGet(path, options) {
      const url = new URL(path, 'https://qa.invalid');
      return new Promise((resolve, reject) => requests.push({
        path, options, resolve, reject,
        zoneId: Number(url.pathname.split('/')[3]),
        start: Date.parse(url.searchParams.get('start')), end: Date.parse(url.searchParams.get('end')),
        get minutes() { return (this.end - this.start) / MINUTE; },
      }));
    },
  };
  const errors = { getErrorMessage: (error) => error.message };
  const probeChart = load('../src/utils/probeChart.ts');
  const probeReadings = load('../src/utils/probeReadings.ts');
  const probeSeries = load('../src/utils/probeSeries.ts', { './probeChart': probeChart });
  const useProbeReadings = load('../src/hooks/useProbeReadings.ts', { react: hooks, '../api/client': client, '../utils/probeReadings': probeReadings });
  const useLiveWindowEnd = load('../src/hooks/useLiveWindowEnd.ts', { react: hooks });
  const useProbeMonitoring = load('../src/hooks/useProbeMonitoring.ts', {
    react: hooks, '../api/client': client, '../utils/errors': errors, '../utils/probeChart': probeChart,
  }).default;
  const chartView = () => null;
  const probePeriod = load('../src/utils/probePeriod.ts');
  const charts = load('../src/components/ZoneProbeCharts.tsx', {
    react: hooks, 'react/jsx-runtime': jsxRuntime, 'react-dom': { createPortal: (node) => node }, '../api/client': client,
    '../hooks/useAnchoredPopover': { useAnchoredPopover: () => ({ anchorRef: { current: null }, panelRef: { current: null }, position: {}, id: 'popover' }) },
    '../hooks/useLiveWindowEnd': useLiveWindowEnd,
    '../hooks/useProbeReadings': useProbeReadings, '../utils/dateFormat': load('../src/utils/dateFormat.ts'),
    '../utils/environmentFormat': load('../src/utils/environmentFormat.ts'), '../utils/errors': errors,
    '../utils/probeChart': probeChart, '../utils/probeReadings': probeReadings, '../utils/probeSeries': probeSeries,
    '../utils/probePeriod': probePeriod,
    './PolypbaseIcon': { default: () => null }, './ProbeSeriesChart': { default: chartView, seriesClassName: () => '', averageSeriesStyle: () => undefined },
  });
  const t = (key) => key;

  let zone = makeZone(7);
  let tree;
  function renderOnce() {
    cursor = 0;
    dirty = false;
    pending = [];
    const chart = charts.useZoneChartReadings(zone, true);
    const monitoring = useProbeMonitoring(zone, chart.initial);
    const view = charts.ZoneProbeChart({ chart, zone, t });
    tree = { chart, monitoring, view };
    const effects = pending;
    pending = [];
    for (const run of effects) run();
  }
  function settle() {
    if (!mounted) return;
    let renders = 0;
    do {
      assert.ok((renders += 1) < 25, 'Render loop: state never settled');
      renderOnce();
    } while (dirty);
  }
  // Loaders notify through setState outside a render; a late notification is picked up here.
  async function step() {
    for (let i = 0; i < 4; i += 1) { await flushPromises(); settle(); }
  }

  async function advance(milliseconds) {
    const target = now + milliseconds;
    for (;;) {
      const due = [...timers.entries()].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
      if (!due) break;
      const [id, timer] = due;
      now = timer.at;
      if (timer.every) timer.at += timer.every; else timers.delete(id);
      timer.fn();
      await step();
    }
    now = target;
    await step();
  }

  const find = (predicate, node = tree.view) => {
    if (Array.isArray(node)) return node.flatMap((child) => find(predicate, child));
    if (!React.isValidElement(node)) return [];
    return [...(predicate(node) ? [node] : []), ...find(predicate, node.props.children ?? null)];
  };
  const text = (node) => {
    if (Array.isArray(node)) return node.map(text).join('');
    if (React.isValidElement(node)) return text(node.props.children ?? null);
    return node == null || typeof node === 'boolean' ? '' : String(node);
  };

  return {
    requests, timers, context, document, windowEvents, documentEvents, probePeriod,
    get now() { return now; },
    get tree() { return tree; },
    start: async () => { settle(); await step(); },
    step, advance,
    setZone: async (next) => { zone = next; await step(); },
    series: () => requests.filter((request) => request.path.includes('probe-readings')),
    ofLength: (minutes) => requests.filter((request) => request.minutes === minutes),
    busy: () => find((node) => (node.props.className ?? '').includes('zone-probe-chart-section'))[0].props['aria-busy'],
    error: () => find((node) => node.props.className === 'probe-series-status').map(text).join(''),
    retryButton: () => find((node) => node.type === 'button' && node.props.children === 'probeRetry')[0],
    empty: () => find((node) => node.props.className === 'probe-series-state').map(text).join(''),
    plotted: () => find((node) => node.type === chartView)[0].props.series.map((entry) => entry.readings.map((reading) => reading[1])),
    visible: async (state) => {
      document.visibilityState = state;
      [...documentEvents.keys()].forEach((fn) => fn());
      windowEvents.get('focus')?.();
      await step();
    },
    unmount() { mounted = false; for (const slot of slots) slot?.cleanup?.(); slots = []; },
    changeOrganization() {
      context.controller.abort();
      context.controller = new AbortController();
      context.signal = context.controller.signal;
    },
  };
}

function makeZone(id) {
  return {
    id, target_temperature_c: '22.00', probe_monitoring: { has_collected_probes: true, manual_entry_available: false, last_reading_at: null, fallback_after_hours: 6 },
    probes: [{ id: id * 10, code: `P${id}`, location: '', is_active: true, is_collected: true }],
  };
}
const raw = (time, value) => [time, null, value.toFixed(2), 1, value, value, time];
function response(request, { resolution = 1, zoneId = request.zoneId, values = [22, 22.5], start = request.start } = {}) {
  const instants = values.map((_, index) => request.end - (values.length - index) * MINUTE);
  return {
    zone_id: zoneId, start: new Date(start).toISOString(), end: new Date(request.end).toISOString(),
    resolution_minutes: resolution, target_temperature_c: '22.00',
    monitoring: { has_collected_probes: true, manual_entry_available: false, last_reading_at: new Date(request.end).toISOString(), fallback_after_hours: 6 },
    probes: [{
      id: zoneId * 10, code: `P${zoneId}`, location: '', is_active: true, external_channel: 1, sampling_interval_seconds: 60,
      points: instants.map(() => [0, 0, null, null]),
      point_sources: resolution === 1
        ? instants.map((time, index) => [raw(time, values[index])])
        : instants.map((time, index) => [[time, time + 15 * MINUTE, (values[index] * 15).toFixed(2), 15, values[index], values[index], time]]),
    }],
  };
}
async function answer(view, request, options) { request.resolve(response(request, options)); await view.step(); }
async function fail(view, request, message = 'Offline') { request.reject(new Error(message)); await view.step(); }

const DEFAULT_MINUTES = 120;

test('the approved default chart period is two hours', () => {
  const view = bench();
  assert.equal(view.probePeriod.DEFAULT_PERIOD_HOURS, 2);
  assert.deepEqual({ ...view.probePeriod.DEFAULT_CHART_PERIOD }, { kind: 'relative', hours: 2 });
});

test('a successful opening starts one two-hour series request and reuses it for monitoring', async () => {
  const view = bench();
  await view.start();
  assert.deepEqual(view.requests.map((request) => request.minutes), [DEFAULT_MINUTES], 'One request at opening, nothing else');
  assert.equal(view.busy(), true, 'Pending is loading, not empty');
  assert.equal(view.empty(), '');
  await answer(view, view.series()[0], { values: [0, 22.5] });
  assert.deepEqual(view.requests.map((request) => request.minutes), [DEFAULT_MINUTES], 'Monitoring does not fetch at opening');
  assert.deepEqual([...view.plotted()[0]], [0, 22.5], 'Zero is plotted as a measurement');
  assert.equal(view.busy(), false);
  assert.equal(view.tree.monitoring.monitoring.last_reading_at, new Date(view.series()[0].end).toISOString());
  assert.equal(view.tree.chart.initial.data.zone_id, 7);
  view.unmount();
});

test('normal refresh: monitoring polls alone, the chart follows its own live window', async () => {
  const view = bench();
  await view.start();
  await answer(view, view.series()[0]);
  await view.advance(MINUTE + 200);
  assert.equal(view.ofLength(1).length, 1, 'Monitoring refreshes alone');
  assert.equal(view.ofLength(DEFAULT_MINUTES).length, 1, 'The chart does not refresh every minute');
  await view.advance(5 * MINUTE);
  assert.equal(view.ofLength(DEFAULT_MINUTES).length, 2, 'The chart window moves once per refresh period');
  assert.ok(view.requests.every((request) => request.minutes === 1 || request.minutes === DEFAULT_MINUTES),
    'No six-hour trend read remains');
  view.unmount();
});

test('a failed opening read lets monitoring read for itself at once and shows the chart error, not an empty state', async () => {
  const view = bench();
  await view.start();
  await fail(view, view.series()[0]);
  assert.equal(view.ofLength(1).length, 1, 'Monitoring reads for itself at once');
  assert.equal(view.now, START, 'No time has passed');
  assert.equal(view.tree.chart.initial.settled, true);
  assert.equal(view.tree.chart.initial.data, null, 'A failure is not a result');
  assert.match(view.error(), /Offline/);
  assert.equal(view.empty(), '', 'An error is not an empty result');
  assert.equal(view.busy(), false);
  assert.equal(view.series().length, 2, 'Exactly one chart read and one monitoring read');
  view.unmount();
});

test('a condensed shared response is plotted and still serves monitoring without another read', async () => {
  const view = bench();
  await view.start();
  await answer(view, view.series()[0], { resolution: 15, values: [20, 20.5] });
  assert.equal(view.ofLength(1).length, 0, 'Its server status remains valid');
  assert.equal(view.series().length, 1);
  assert.equal(view.tree.chart.readings.data.resolution_minutes, 15, 'The condensed response is kept, not replaced');
  assert.equal(view.error(), '');
  view.unmount();
});

test('a shared response with no reading is a real empty state, without another read', async () => {
  const view = bench();
  await view.start();
  await answer(view, view.series()[0], { values: [] });
  assert.equal(view.series().length, 1);
  assert.equal(view.empty(), 'probeNoReadings');
  assert.equal(view.error(), '');
  assert.equal(view.busy(), false);
  view.unmount();
});

test('a failed chart read does not loop, and a manual retry sends one request and recovers', async () => {
  const view = bench();
  await view.start();
  await fail(view, view.series()[0], 'Chart offline');
  for (let i = 0; i < 5; i += 1) await view.step();
  await view.advance(59_000);
  assert.equal(view.ofLength(DEFAULT_MINUTES).length, 1, 'No automatic burst');
  view.retryButton().props.onClick();
  await view.advance(1);
  assert.equal(view.ofLength(DEFAULT_MINUTES).length, 2, 'One request per retry');
  assert.equal(view.error(), '', 'A retry clears the error while it loads');
  await answer(view, view.ofLength(DEFAULT_MINUTES)[1], { values: [0, 1] });
  assert.equal(view.error(), '');
  assert.deepEqual([...view.plotted()[0]], [0, 1]);
  view.unmount();
});

test('retrying the chart does not reopen the shared wait or interrupt monitoring', async () => {
  const view = bench();
  await view.start();
  await fail(view, view.series()[0]);
  const monitoring = view.ofLength(1)[0];
  view.tree.chart.readings.retry();
  await view.step();
  assert.equal(view.tree.chart.initial.settled, true, 'The first outcome is final');
  assert.equal(monitoring.options.signal.aborted, false);
  assert.equal(view.ofLength(1).length, 1);
  view.unmount();
});

test('the chart live window moving after a failure does not reopen the shared wait', async () => {
  const view = bench();
  await view.start();
  await fail(view, view.series()[0]);
  await answer(view, view.ofLength(1)[0]);
  const settledStates = [];
  for (let i = 0; i < 6; i += 1) { await view.advance(MINUTE); settledStates.push(view.tree.chart.initial.settled); }
  assert.ok(settledStates.every(Boolean));
  view.unmount();
});

test('choosing a custom range before the first response cancels it and releases monitoring at once', async () => {
  const view = bench();
  await view.start();
  const first = view.series()[0];
  view.tree.chart.setCustom({ start: START - 3 * 24 * HOUR, end: START - 2 * 24 * HOUR });
  await view.step();
  await view.advance(200);
  assert.equal(first.options.signal.aborted, true);
  assert.equal(view.ofLength(24 * 60).length, 1, 'The custom range is read');
  assert.equal(view.ofLength(1).length, 1, 'Monitoring does not wait for a non-default read');
  view.unmount();
});

test('a rapid zone change cancels old reads and never publishes the old zone', async () => {
  const view = bench();
  await view.start();
  const chart7 = view.series()[0];
  await view.setZone(makeZone(8));
  assert.equal(chart7.options.signal.aborted, true, 'The previous zone read is cancelled');
  const chart8 = view.series().find((request) => request.zoneId === 8 && request.minutes === DEFAULT_MINUTES);
  assert.ok(chart8, 'The new zone starts its own shared read');
  await answer(view, chart7, { values: [99, 99] });
  assert.equal(view.tree.chart.readings.data, null, 'A late old-zone response is ignored');
  await answer(view, chart8, { values: [18, 18.5] });
  assert.deepEqual([...view.plotted()[0]], [18, 18.5]);
  assert.equal(view.tree.chart.initial.data.zone_id, 8);
  assert.equal(view.tree.chart.readings.data.zone_id, 8);
  view.unmount();
});

test('a zone change after a failure cancels the old monitoring read', async () => {
  const view = bench();
  await view.start();
  await fail(view, view.series()[0]);
  const monitoring7 = view.ofLength(1)[0];
  await view.setZone(makeZone(8));
  assert.equal(monitoring7.options.signal.aborted, true);
  assert.equal(view.tree.chart.initial.settled, false, 'The new zone waits for its own first outcome');
  view.unmount();
});

test('an organization switch discards pending reads and any earlier shared result', async () => {
  const view = bench();
  await view.start();
  const old = view.series()[0];
  await answer(view, old, { values: [30, 30.5] });
  assert.equal(view.tree.chart.initial.data.zone_id, 7);
  view.changeOrganization();
  await view.step();
  assert.equal(view.tree.chart.initial.data, null, 'A result from the previous organization is never reused');
  assert.equal(view.tree.chart.initial.settled, false);
  assert.equal(view.tree.chart.readings.data, null, 'Nor is it still plotted');
  const next = view.series().at(-1);
  assert.notEqual(next, old);
  assert.equal(next.minutes, DEFAULT_MINUTES);
  assert.equal(next.options.signal.aborted, false);
  await fail(view, next);
  assert.equal(view.tree.chart.initial.settled, true);
  view.tree.chart.readings.retry();
  await view.advance(1);
  await answer(view, view.ofLength(DEFAULT_MINUTES).at(-1), { values: [19, 19.5] });
  assert.deepEqual([...view.plotted()[0]], [19, 19.5]);
  view.unmount();
});

test('unmounting cancels every pending read and timer', async () => {
  const view = bench();
  await view.start();
  await fail(view, view.series()[0]);
  view.tree.chart.readings.retry();
  await view.advance(1);
  const pending = [view.ofLength(DEFAULT_MINUTES).at(-1), view.ofLength(1)[0]];
  view.unmount();
  assert.ok(pending.every((request) => request.options.signal.aborted));
  assert.equal(view.timers.size, 0);
  assert.equal(view.documentEvents.size, 0);
  const count = view.requests.length;
  await view.advance(10 * MINUTE);
  assert.equal(view.requests.length, count);
});

test('a hidden page postpones monitoring and the chart refresh until it is visible again, once', async () => {
  const view = bench({ visibilityState: 'hidden' });
  await view.start();
  await fail(view, view.series()[0]);
  assert.equal(view.ofLength(1).length, 0, 'No monitoring read while hidden');
  await view.advance(10 * MINUTE);
  assert.equal(view.ofLength(1).length, 0, 'No polling while hidden');
  assert.equal(view.ofLength(DEFAULT_MINUTES).length, 1, 'The chart window does not move while hidden');
  await view.visible('visible');
  await view.advance(300);
  const active = (minutes) => view.ofLength(minutes).filter((request) => !request.options.signal.aborted);
  // Focus and visibility events arrive together; a superseded read is aborted.
  assert.equal(active(1).length, 1, 'One live monitoring read');
  const charts = view.ofLength(DEFAULT_MINUTES).length;
  await view.visible('visible');
  await view.advance(300);
  assert.equal(view.ofLength(DEFAULT_MINUTES).length, charts, 'Repeated focus events do not add chart reads');
  assert.equal(active(1).length, 1, 'Nor more than one live monitoring read');
  view.unmount();
});

test('hiding the page after a success keeps the result and stops refreshing', async () => {
  const view = bench();
  await view.start();
  await answer(view, view.series()[0], { values: [21, 21.5] });
  await view.visible('hidden');
  const count = view.requests.length;
  await view.advance(10 * MINUTE);
  assert.equal(view.requests.length, count);
  assert.deepEqual([...view.plotted()[0]], [21, 21.5]);
  view.unmount();
});
