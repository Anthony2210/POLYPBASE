import assert from 'node:assert/strict';
import { getEventListeners } from 'node:events';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/hooks/useThermalControl.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const tick = () => new Promise(resolve => setImmediate(resolve));
const plain = value => JSON.parse(JSON.stringify(value));
const epoch = Date.parse('2026-11-04T12:00:00Z');
const iso = offset => new Date(epoch + offset).toISOString();
const summary = (lifetime = 60_000, overrides = {}) => ({
  evaluated_at: iso(0), temperature_c: 0, included_probe_count: 2,
  oldest_measured_at: iso(-120_000), newest_measured_at: iso(-60_000),
  expires_at: lifetime === null ? null : iso(lifetime), eligible_probe_count: 3,
  no_reading_probe_count: 0, stale_probe_count: 1, unavailable_probe_count: 0,
  future_probe_count: 0, invalid_interval_probe_count: 0, ...overrides,
});
const zone = (id = 17, organizationId = 1) => ({
  id, organization: { id: organizationId }, probes: [], thermal_control: summary(),
  latest_temperature: { average_temperature_c: 99 },
});

// Execute the real hook with fake React lifecycles, monotonic time and network.
// Requests intentionally may complete after cancellation, as transports can do.
function harness(initial = zone()) {
  const slots = [], pending = [], requests = [], timers = new Map();
  const events = new EventTarget(), document = new EventTarget();
  document.visibilityState = 'visible';
  let cursor = 0, dirty = false, mounted = true, output, input = initial;
  let clock = 0, nextTimer = 0, wallClock = epoch;
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
    useMemo(fn, deps) {
      const index = cursor++, old = slots[index];
      if (!old || deps.some((value, i) => !Object.is(value, old.deps[i]))) {
        slots[index] = { deps, value: fn() };
      }
      return slots[index].value;
    },
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
    exports, AbortController, document, performance: { now: () => clock },
    Date: class extends Date { static now() { return wallClock; } },
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
    requests, timers, document, render, settle,
    get value() { return plain(output); },
    get input() { return input; },
    get signal() { return context.signal; },
    setWallClock(value) { wallClock = value; },
    jump(ms) { clock += ms; },
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
    async event(type, visibility = 'visible') {
      document.visibilityState = visibility;
      (type === 'focus' ? events : document).dispatchEvent(new Event(type));
      await settle();
    },
    replaceContext() { const old = context; context = new AbortController(); old.abort(); },
    abortContext() { context.abort(); },
    unmount() { slots.forEach(slot => slot.cleanup?.()); mounted = false; },
  };
}

// Undefined is deliberately checked separately from a real zero or null value.
const missing = h => assert.equal(h.value.summary, undefined);

test('fetches the direct no-store endpoint, preserves zero and never falls back to daily or unanchored embedded data', async () => {
  const h = harness(); missing(h);
  assert.equal(h.requests[0].url, '/api/thermal-zones/17/thermal-control/');
  assert.equal(h.requests[0].options.cache, 'no-store');
  assert.equal(h.requests[0].options.signal.aborted, false);
  h.requests[0].resolve(summary()); await h.settle();
  assert.deepEqual(h.value, { summary: summary(), error: null });
  h.render(); assert.equal(h.requests.length, 1);
  h.unmount();
});

test('polls 30 seconds after completion and shares pending requests on focus and visibility', async () => {
  const h = harness();
  h.requests[0].resolve(summary(null, { temperature_c: null, included_probe_count: 0 })); await h.settle();
  assert.equal(h.value.summary.temperature_c, null);
  await h.advance(29_999); assert.equal(h.requests.length, 1);
  await h.advance(1); assert.equal(h.requests.length, 2);
  await h.event('focus', 'hidden'); await h.event('visibilitychange', 'hidden');
  assert.equal(h.requests.length, 2);
  await h.event('visibilitychange'); assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].options.signal.aborted, false);
  await h.event('focus'); assert.equal(h.requests.length, 2);
  h.requests[1].resolve(summary(null, { temperature_c: null, included_probe_count: 0 })); await h.settle();
  await h.event('focus'); assert.equal(h.requests.length, 3);
  h.unmount();
});

test('expires the whole mean at the earliest boundary plus 1ms even while a poll is pending', async () => {
  const h = harness();
  h.requests[0].resolve(summary(30_000)); await h.settle();
  await h.advance(30_000);
  assert.equal(h.value.summary.temperature_c, 0, 'Exact expiry is inclusive');
  assert.equal(h.requests.length, 2);
  await h.advance(1); missing(h);
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].options.signal.aborted, false);
  const replacement = summary(null, { included_probe_count: 0, temperature_c: null, stale_probe_count: 3 });
  h.requests[1].resolve(replacement); await h.settle();
  assert.deepEqual(h.value.summary, replacement);
  h.unmount();
});

test('uses server-relative monotonic time, charges network latency, and ignores browser clock skew', async () => {
  const h = harness();
  h.setWallClock(epoch + 100 * 365 * 86_400_000);
  h.jump(4_000); h.requests[0].resolve(summary(10_000)); await h.settle();
  assert.equal(h.value.summary.temperature_c, 0);
  h.setWallClock(epoch - 100 * 365 * 86_400_000);
  await h.advance(6_000); assert.equal(h.value.summary.temperature_c, 0);
  await h.advance(1); missing(h); assert.equal(h.requests.length, 2);
  h.unmount();
});

test('an exact-instant response schedules 1ms; expired reception retries in 1 second, never immediately', async () => {
  const h = harness();
  h.requests[0].resolve(summary(0)); await h.settle();
  assert.equal(h.value.summary.temperature_c, 0);
  await h.advance(1); missing(h); assert.equal(h.requests.length, 2);
  h.requests[1].resolve(summary(0, { evaluated_at: iso(-1), expires_at: iso(-1) }));
  h.jump(1); await h.settle(); missing(h);
  await h.advance(999); assert.equal(h.requests.length, 2);
  await h.advance(1); assert.equal(h.requests.length, 3);
  h.unmount();
});

test('expired embedded data and slow expired responses remain absent until a safe fresh read arrives', async () => {
  const h = harness({ ...zone(), thermal_control: summary(-1) }); missing(h);
  h.jump(5_000); h.requests[0].resolve(summary(4_000)); await h.settle(); missing(h);
  await h.event('focus'); h.requests[1].resolve(summary()); await h.settle();
  assert.equal(h.value.summary.temperature_c, 0); h.unmount();
});

for (const [name, change] of [
  ['zone ID', old => ({ ...old, id: 18 })],
  ['organization ID', old => ({ ...old, organization: { id: 2 } })],
  ['same-ID mutation publication', old => ({ ...old })],
]) {
  test(`${name} immediately hides snapshots and errors before effects and rejects old requests`, async () => {
    const h = harness(); h.requests[0].resolve(summary()); await h.settle();
    await h.event('focus');
    const next = change(h.input);
    assert.deepEqual(h.render(next, false), { error: null });
    h.render(next); assert.ok(h.requests[1].options.signal.aborted);
    h.requests[1].resolve(summary(null, { temperature_c: 99 })); await h.settle(); missing(h);
    h.requests[2].resolve(summary(null, { temperature_c: -2 })); await h.settle();
    assert.equal(h.value.summary.temperature_c, -2);
    await h.event('focus'); h.requests[3].reject(new Error('Offline')); await h.settle();
    assert.equal(h.value.error, 'Offline');
    assert.deepEqual(h.render(change(next), false), { error: null });
    h.unmount();
  });
}

test('organization cancellation clears data, cancels work immediately and removes all listeners on cleanup', async () => {
  const h = harness(), oldSignal = h.signal;
  h.requests[0].resolve(summary()); await h.settle();
  await h.event('focus');
  assert.equal(getEventListeners(oldSignal, 'abort').length, 1);
  h.replaceContext(); await h.settle(); missing(h);
  assert.ok(h.requests[1].options.signal.aborted);
  assert.equal(getEventListeners(oldSignal, 'abort').length, 0);
  h.requests[1].resolve(summary(null, { temperature_c: 99 })); await h.settle(); missing(h);
  h.requests[2].resolve(summary()); await h.settle();
  assert.equal(h.value.summary.temperature_c, 0);
  const newSignal = h.signal;
  h.unmount(); assert.equal(getEventListeners(newSignal, 'abort').length, 0);
  assert.equal(h.timers.size, 0);
  const count = h.requests.length;
  await h.event('focus'); await h.event('visibilitychange'); assert.equal(h.requests.length, count);
});

test('aborted context cannot restart requests and late completions cannot write after unmount', async () => {
  const h = harness(); h.abortContext(); await h.settle();
  await h.advance(90_000); await h.event('focus'); assert.equal(h.requests.length, 1);
  h.render({ ...h.input }); assert.equal(h.requests.length, 1);
  h.unmount(); h.requests[0].resolve(summary()); await h.settle(); missing(h);
});

test('failures clear previously fresh markers, expose the error and recover on polling', async () => {
  const h = harness(); h.requests[0].resolve(summary()); await h.settle();
  await h.event('focus'); await h.event('focus');
  assert.equal(h.requests.length, 2);
  h.requests[1].reject(new Error('Offline')); await h.settle();
  missing(h); assert.equal(h.value.error, 'Offline');
  await h.advance(30_000); h.requests[2].resolve(summary()); await h.settle();
  assert.equal(h.value.summary.temperature_c, 0); assert.equal(h.value.error, null);
  h.unmount();
});

test('render-time expiry guard hides data even if a background timer has not run', async () => {
  const h = harness(); h.requests[0].resolve(summary(10_000)); await h.settle();
  h.jump(10_001); missing({ value: h.render() });
  h.unmount();
});

test('a request survives several polling intervals and foreground events without overlap or cancellation', async () => {
  const h = harness();
  await h.advance(95_000);
  await h.event('focus'); await h.event('visibilitychange');
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].options.signal.aborted, false);
  h.requests[0].resolve(summary(180_000)); await h.settle();
  assert.equal(h.value.summary.temperature_c, 0);
  await h.advance(29_999); assert.equal(h.requests.length, 1);
  await h.advance(1); assert.equal(h.requests.length, 2);
  await h.advance(90_000); assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].options.signal.aborted, false);
  h.unmount(); assert.equal(h.requests[1].options.signal.aborted, true);
  h.requests[1].resolve(summary()); await h.settle();
});

test('transport expiry promptly re-evaluates the remaining probe instead of calculating a partial mean', async () => {
  const h = harness();
  await h.advance(5_000);
  h.requests[0].resolve(summary(4_000, { temperature_c: 20 })); await h.settle();
  missing(h); assert.equal(h.value.error, null);
  await h.advance(999); assert.equal(h.requests.length, 1);
  await h.advance(1); assert.equal(h.requests.length, 2);
  const remaining = summary(60_000, { temperature_c: 0, included_probe_count: 1, stale_probe_count: 2 });
  h.requests[1].resolve(remaining); await h.settle();
  assert.deepEqual(h.value.summary, remaining);
  h.unmount();
});

test('repeated expired responses back off with a 15-second cap and a valid read resets recovery', async () => {
  const h = harness();
  for (const delay of [1_000, 2_000, 4_000, 8_000, 15_000, 15_000]) {
    const index = h.requests.length - 1;
    h.jump(1); h.requests[index].resolve(summary(0)); await h.settle(); missing(h);
    await h.advance(delay - 1); assert.equal(h.requests.length, index + 1);
    await h.advance(1); assert.equal(h.requests.length, index + 2);
    assert.equal(h.requests[index].options.signal.aborted, false);
  }
  h.requests.at(-1).resolve(summary()); await h.settle();
  assert.equal(h.value.summary.temperature_c, 0);
  await h.event('focus');
  h.jump(1); h.requests.at(-1).resolve(summary(0)); await h.settle(); missing(h);
  const count = h.requests.length;
  await h.advance(999); assert.equal(h.requests.length, count);
  await h.advance(1); assert.equal(h.requests.length, count + 1);
  h.requests.at(-1).resolve(summary(null, {
    temperature_c: null, included_probe_count: 0, stale_probe_count: 3,
    oldest_measured_at: null, newest_measured_at: null,
  })); await h.settle();
  assert.equal(h.value.summary.temperature_c, null);
  const emptyCount = h.requests.length;
  await h.advance(29_999); assert.equal(h.requests.length, emptyCount);
  await h.advance(1); assert.equal(h.requests.length, emptyCount + 1);
  h.unmount();
});

test('context switches cancel planned expired recovery and reject late recovery results', async () => {
  const h = harness();
  h.jump(1); h.requests[0].resolve(summary(0)); await h.settle();
  h.replaceContext(); await h.settle();
  assert.equal(h.requests.length, 2);
  await h.advance(1_000); assert.equal(h.requests.length, 2);
  h.requests[1].resolve(summary()); await h.settle();
  await h.event('focus');
  h.jump(1); h.requests[2].resolve(summary(0)); await h.settle();
  await h.advance(1_000); assert.equal(h.requests.length, 4);
  h.render(zone(18, 2));
  assert.equal(h.requests[3].options.signal.aborted, true);
  h.requests[3].resolve(summary(null, { temperature_c: 99 })); await h.settle(); missing(h);
  h.requests[4].resolve(summary()); await h.settle();
  assert.equal(h.value.summary.temperature_c, 0);
  h.unmount(); assert.equal(h.timers.size, 0);
});
