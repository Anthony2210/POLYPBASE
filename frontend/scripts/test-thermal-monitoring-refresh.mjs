import assert from 'node:assert/strict';
import { getEventListeners } from 'node:events';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/hooks/useThermalMonitoring.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const tick = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));
const monitoring = (overrides = {}) => ({
  zone_id: 17, start: '2026-11-04T08:00:00Z', end: '2026-11-04T12:00:00Z',
  resolution_minutes: 1, target_temperature_c: 0,
  probes: [1, 2].map(id => ({
    id, code: `P${id}`, location: '', is_active: true, external_channel: null,
    points: [[Date.parse('2026-11-04T08:00:00Z'), 0, null, null]],
  })),
  probe_average: [[Date.parse('2026-11-04T08:00:00Z'), 0, 2]],
  summary: {
    mean_temperature_c: 0, target_deviation_c: 0, min_temperature_c: 0,
    max_temperature_c: 0, contributing_probe_count: 2, measurement_count: 2,
  },
  ...overrides,
});
const zone = (id = 17, organizationId = 1) => ({
  id, organization: { id: organizationId }, probes: [],
  latest_temperature: { average_temperature_c: 99 },
  thermal_control: { temperature_c: 99 },
});

// Run the real hook with stub React lifecycles and timers. Cancelled transports
// can still resolve, deliberately exercising publication guards independently.
function harness(initial = zone(), visibility = 'visible') {
  const slots = [], pending = [], requests = [], timers = new Map();
  const events = new EventTarget(), document = new EventTarget();
  document.visibilityState = visibility;
  let cursor = 0, dirty = false, mounted = true, output, input = initial;
  let clock = 0, nextTimer = 0;
  let context = new AbortController();
  class ApiResourceCancelledError extends Error {}
  const hooks = {
    useState(initialValue) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { value: initialValue };
      return [slots[index].value, value => {
        assert.ok(mounted, 'No state updates after unmount');
        slots[index].value = typeof value === 'function' ? value(slots[index].value) : value;
        dirty = true;
      }];
    },
    useRef(initialValue) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = { current: initialValue };
      return slots[index];
    },
    useMemo(fn, deps) {
      const index = cursor++, old = slots[index];
      if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) {
        slots[index] = { deps, value: fn() };
      }
      return slots[index].value;
    },
    useCallback(fn, deps) { return hooks.useMemo(() => fn, deps); },
    useEffect(fn, deps) {
      const index = cursor++, old = slots[index];
      if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) {
        pending.push(() => {
          old?.cleanup?.();
          slots[index] = { deps, cleanup: fn() };
        });
      }
    },
  };
  const client = {
    ApiResourceCancelledError,
    getOrganizationResourceSignal: () => context.signal,
    apiGet(url, options) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      requests.push({ url, options, resolve, reject });
      return promise;
    },
  };
  const exports = {};
  vm.runInNewContext(compiled, {
    exports, AbortController, document,
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, at: clock + delay }); return id; },
    clearTimeout(id) { timers.delete(id); },
    addEventListener: events.addEventListener.bind(events),
    removeEventListener: events.removeEventListener.bind(events),
    require(name) {
      if (name === 'react') return hooks;
      if (name === '../api/client') return client;
      assert.equal(name, '../utils/errors');
      return { getErrorMessage: error => error.message };
    },
  });
  function render(next = input, commit = true) {
    input = next; cursor = 0; dirty = false;
    output = exports.default(input);
    if (commit) while (pending.length) pending.shift()();
    else pending.length = 0;
    return plain(output);
  }
  async function settle() {
    await tick();
    let count = 0;
    while (dirty) { assert.ok(++count < 20, 'No render loop'); render(); }
  }
  render();
  return {
    requests, timers, document, events, render, settle, ApiResourceCancelledError,
    get value() { return plain(output); },
    get input() { return input; },
    get signal() { return context.signal; },
    get retry() { return output.retry; },
    async advance(ms) {
      const end = clock + ms;
      let count = 0;
      while (true) {
        const due = [...timers].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        assert.ok(++count < 100, 'No zero-delay timer loop');
        clock = due[1].at; timers.delete(due[0]); due[1].fn(); await settle();
      }
      clock = end; await settle();
    },
    async event(type, state = 'visible') {
      document.visibilityState = state;
      (type === 'focus' ? events : document).dispatchEvent(new Event(type));
      await settle();
    },
    replaceContext() { const old = context; context = new AbortController(); old.abort(); },
    abortContext() { context.abort(); },
    unmount() { slots.forEach(slot => slot.cleanup?.()); mounted = false; },
  };
}
const loading = h => assert.deepEqual(h.value, { data: null, error: null, isLoading: true });

test('uses only the no-store fixed-window endpoint and preserves server observations, zeros and nulls', async () => {
  const h = harness(); loading(h);
  assert.equal(typeof h.retry, 'function');
  assert.equal(h.requests[0].url, '/api/thermal-zones/17/thermal-monitoring/');
  assert.equal(h.requests[0].options.cache, 'no-store');
  assert.equal(h.requests[0].options.signal.aborted, false);
  const data = monitoring();
  h.requests[0].resolve(data); await h.settle();
  assert.deepEqual(h.value, { data, error: null, isLoading: false });
  const retry = h.retry;
  h.render(); assert.equal(h.retry, retry); assert.equal(h.requests.length, 1);
  // No observation expiry or thermal-control freshness timer is applied here.
  assert.equal(h.timers.size, 1);
  await h.advance(29_999); assert.deepEqual(h.value.data, data);
  await h.advance(1); loading(h);
  const empty = monitoring({ target_temperature_c: null, probes: [], probe_average: [], summary: {
    mean_temperature_c: null, target_deviation_c: null, min_temperature_c: null,
    max_temperature_c: null, contributing_probe_count: 0, measurement_count: 0,
  } });
  h.requests[1].resolve(empty); await h.settle();
  assert.deepEqual(h.value, { data: empty, error: null, isLoading: false });
  h.unmount();
});

test('slow reads never overlap focus, retry, visibility or polls; polling starts 30s after settlement', async () => {
  const h = harness();
  await h.advance(95_000);
  h.retry(); h.retry(); await h.event('focus'); await h.event('visibilitychange');
  assert.equal(h.requests.length, 1); assert.equal(h.timers.size, 0);
  assert.equal(h.requests[0].options.signal.aborted, false);
  h.requests[0].resolve(monitoring()); await h.settle();
  await h.advance(29_999); assert.equal(h.requests.length, 1);
  await h.advance(1); assert.equal(h.requests.length, 2); loading(h);
  await h.advance(95_000); h.retry(); await h.event('focus');
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].options.signal.aborted, false);
  h.requests[1].resolve(monitoring()); await h.settle();
  await h.advance(29_999); assert.equal(h.requests.length, 2);
  await h.advance(1); assert.equal(h.requests.length, 3); loading(h);
  h.unmount();
});

test('each focus and retry refresh clears the old snapshot and coalesces pending retries', async () => {
  const h = harness(); h.requests[0].resolve(monitoring()); await h.settle();
  await h.event('focus'); loading(h);
  h.retry(); await h.settle(); assert.equal(h.requests.length, 2);
  h.requests[1].resolve(monitoring()); await h.settle();
  h.retry(); await h.settle(); loading(h);
  assert.equal(h.requests.length, 3);
  h.unmount();
});

test('pauses hidden polls and retries, then catches up immediately on return', async () => {
  const h = harness(); h.requests[0].resolve(monitoring()); await h.settle();
  await h.event('visibilitychange', 'hidden'); assert.equal(h.timers.size, 0);
  await h.advance(120_000); h.retry(); await h.event('focus', 'hidden');
  assert.equal(h.requests.length, 1);
  await h.event('visibilitychange'); loading(h); assert.equal(h.requests.length, 2);
  await h.event('focus'); assert.equal(h.requests.length, 2);
  h.unmount();
});

test('hidden mount waits for visibility; hiding never cancels an existing slow read', async () => {
  const h = harness(zone(), 'hidden'); loading(h);
  await h.advance(120_000); h.retry(); assert.equal(h.requests.length, 0);
  await h.event('visibilitychange'); assert.equal(h.requests.length, 1);
  await h.event('visibilitychange', 'hidden');
  assert.equal(h.requests[0].options.signal.aborted, false);
  h.requests[0].resolve(monitoring()); await h.settle(); assert.equal(h.timers.size, 0);
  await h.advance(120_000); assert.equal(h.requests.length, 1);
  await h.event('visibilitychange'); loading(h); assert.equal(h.requests.length, 2);
  await h.event('visibilitychange', 'hidden'); await h.event('visibilitychange');
  assert.equal(h.requests.length, 2);
  h.unmount();
});

for (const [name, change] of [
  ['zone ID', old => ({ ...old, id: 18 })],
  ['organization ID', old => ({ ...old, organization: { id: 2 } })],
  ['same-ID zone publication', old => ({ ...old })],
]) {
  test(`${name} hides snapshots and errors before effects and refuses reversed old responses`, async () => {
    const h = harness(); h.requests[0].resolve(monitoring()); await h.settle();
    const staleRetry = h.retry;
    const next = change(h.input);
    assert.deepEqual(h.render(next, false), { data: null, error: null, isLoading: true });
    h.render(next); assert.equal(h.requests.length, 2);
    const newer = change(next);
    h.render(newer); assert.equal(h.requests[1].options.signal.aborted, true);
    staleRetry(); assert.equal(h.requests.length, 3);
    const data = monitoring({ zone_id: newer.id, target_temperature_c: -2 });
    h.requests[2].resolve(data); await h.settle();
    h.requests[1].resolve(monitoring({ zone_id: next.id, target_temperature_c: 99 })); await h.settle();
    assert.deepEqual(h.value.data, data);
    await h.event('focus'); h.requests[3].reject(new Error('Offline')); await h.settle();
    assert.equal(h.value.error, 'Offline');
    assert.deepEqual(h.render(change(newer), false), { data: null, error: null, isLoading: true });
    h.unmount();
  });
}

test('context cancellation immediately aborts reads, clears data, and isolates reversed responses', async () => {
  const h = harness(), oldSignal = h.signal;
  h.requests[0].resolve(monitoring()); await h.settle();
  await h.event('focus');
  assert.equal(getEventListeners(oldSignal, 'abort').length, 1);
  h.replaceContext(); await h.settle(); loading(h);
  assert.equal(h.requests[1].options.signal.aborted, true);
  assert.equal(getEventListeners(oldSignal, 'abort').length, 0);
  assert.equal(h.requests.length, 3);
  const data = monitoring({ target_temperature_c: -2 });
  h.requests[2].resolve(data); await h.settle();
  h.requests[1].resolve(monitoring({ target_temperature_c: 99 })); await h.settle();
  assert.deepEqual(h.value.data, data);
  h.unmount();
});

test('errors clear data; retry clears the error and polling recovers after failure', async () => {
  const h = harness(); h.requests[0].resolve(monitoring()); await h.settle();
  await h.event('focus'); h.requests[1].reject(new Error('Offline')); await h.settle();
  assert.deepEqual(h.value, { data: null, error: 'Offline', isLoading: false });
  h.retry(); await h.settle(); loading(h);
  h.retry(); assert.equal(h.requests.length, 3);
  h.requests[2].reject(new Error('Still offline')); await h.settle();
  assert.deepEqual(h.value, { data: null, error: 'Still offline', isLoading: false });
  await h.advance(29_999); assert.equal(h.requests.length, 3);
  await h.advance(1); loading(h);
  h.requests[3].resolve(monitoring()); await h.settle();
  assert.deepEqual(h.value, { data: monitoring(), error: null, isLoading: false });
  h.unmount();
});

test('refuses a wrong-zone response without a historical fallback and allows retry', async () => {
  const h = harness();
  h.requests[0].resolve(monitoring({ zone_id: 18 })); await h.settle();
  assert.equal(h.value.data, null); assert.equal(h.value.isLoading, false);
  assert.ok(h.value.error);
  assert.equal(h.requests.length, 1);
  h.retry(); await h.settle(); loading(h);
  h.requests[1].resolve(monitoring()); await h.settle();
  assert.deepEqual(h.value.data, monitoring()); h.unmount();
});

test('resource cancellation is not exposed as a network error and never preserves data', async () => {
  const h = harness(); h.requests[0].resolve(monitoring()); await h.settle();
  h.retry(); await h.settle();
  h.requests[1].reject(new h.ApiResourceCancelledError()); await h.settle();
  assert.deepEqual(h.value, { data: null, error: null, isLoading: false });
  h.unmount();
});

test('aborted context clears settled data and cannot restart through retry or foreground events', async () => {
  const h = harness(); h.requests[0].resolve(monitoring()); await h.settle();
  h.abortContext(); await h.settle();
  assert.deepEqual(h.value, { data: null, error: null, isLoading: false });
  assert.equal(h.timers.size, 0);
  h.retry(); await h.advance(90_000); await h.event('focus'); await h.event('visibilitychange');
  h.render({ ...h.input }); assert.equal(h.requests.length, 1);
  h.unmount();
});

test('cleanup removes timers/listeners, aborts work, and rejects late fulfillment or rejection', async () => {
  for (const outcome of ['resolve', 'reject']) {
    const h = harness(), signal = h.signal, retry = h.retry;
    h.requests[0].resolve(monitoring()); await h.settle();
    assert.equal(h.timers.size, 1);
    await h.event('focus');
    h.unmount();
    assert.equal(h.requests[1].options.signal.aborted, true);
    assert.equal(h.timers.size, 0);
    assert.equal(getEventListeners(signal, 'abort').length, 0);
    assert.equal(getEventListeners(h.events, 'focus').length, 0);
    assert.equal(getEventListeners(h.document, 'visibilitychange').length, 0);
    retry(); await h.event('focus'); await h.event('visibilitychange'); await h.advance(90_000);
    assert.equal(h.requests.length, 2);
    h.requests[1][outcome](outcome === 'resolve' ? monitoring() : new Error('Late error'));
    await h.settle();
  }
  const h = harness(); h.requests[0].resolve(monitoring()); await h.settle();
  h.unmount(); assert.equal(h.timers.size, 0);
});
