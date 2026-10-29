import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const MINUTE = 60_000;
const DAY = 24 * 60 * MINUTE;
const range = (start = 0, duration = DAY) => ({ start, end: start + duration });
const plain = (value) => JSON.parse(JSON.stringify(value));
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function clock() {
  let now = 0;
  let nextId = 0;
  const pending = new Map();
  return {
    now: () => now,
    setTimeout(callback, delay) {
      const id = ++nextId;
      pending.set(id, { callback, at: now + delay });
      return id;
    },
    clearTimeout(id) { pending.delete(id); },
    advance(milliseconds) {
      const end = now + milliseconds;
      for (;;) {
        const next = [...pending].filter(([, timer]) => timer.at <= end)
          .sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0];
        if (!next) break;
        pending.delete(next[0]);
        now = next[1].at;
        next[1].callback();
      }
      now = end;
    },
    get size() { return pending.size; },
  };
}

function load(path, timers, imports = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports, AbortController, URLSearchParams,
    Date: class extends Date { static now() { return timers.now(); } },
    setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), 'Unexpected runtime import: ' + name);
      return imports[name];
    },
  }, { filename: path });
  return exports;
}

// Complete API-shaped fixtures, including scientific zero and a missing interval.
function readings(window, { zoneId = 7, resolution = 1, value = 0, points } = {}) {
  const samples = points ?? [
    [window.start, value, null, null],
    [window.start + MINUTE, 2, null, null],
    [window.start + 10 * MINUTE, 3, null, null],
  ];
  const first = samples.length ? new Date(samples[0][0]).toISOString() : null;
  const last = samples.length ? new Date(samples.at(-1)[0]).toISOString() : null;
  return {
    zone_id: zoneId, start: new Date(window.start).toISOString(), end: new Date(window.end).toISOString(),
    resolution_minutes: resolution, target_temperature_c: '0', first_reading_at: first, last_reading_at: last,
    monitoring: {
      has_collected_probes: true, last_reading_at: last, manual_entry_available: false, fallback_after_hours: 6,
    },
    probes: [{ id: 11, code: 'QA-PROBE', location: 'QA tank', is_active: true, external_channel: 1, points: samples }],
  };
}

function harness({ zoneId = 7, context = new AbortController(), timers = clock() } = {}) {
  const api = load('../src/utils/probeReadings.ts', timers);
  const calls = [];
  const notifications = [];
  let observed = range();
  let loader;
  loader = new api.ProbeReadingsLoader(zoneId, (window, signal) => {
    let resolve, reject;
    const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
    // Deliberately ignore abort: the loader must guard both late outcomes itself.
    calls.push({ window: { ...window }, signal, resolve, reject });
    return promise;
  }, () => notifications.push(plain(loader.read(observed))), context.signal, timers.now);
  loader.activate();
  return {
    api, loader, calls, notifications, context, timers,
    select(window, force = false) { observed = window; loader.select(window, force); },
    async answer(index, options) { calls[index].resolve(readings(calls[index].window, { zoneId, ...options })); await flush(); },
  };
}

function assertView(view, window, { loading = false, value = 0, zoneId = 7 } = {}) {
  assert.deepEqual(plain(view.window), window);
  assert.ok(view.data, 'Existing readings must not disappear');
  assert.equal(view.data.zone_id, zoneId);
  assert.equal(view.data.start, new Date(window.start).toISOString());
  assert.equal(view.data.end, new Date(window.end).toISOString());
  assert.equal(view.data.probes[0].points[0][1], value);
  assert.equal(view.isLoading, loading);
}

test('cache is bounded to 24 windows, replaces exact entries and can be cleared', () => {
  const timers = clock();
  const { ProbeReadingsCache } = load('../src/utils/probeReadings.ts', timers);
  const cache = new ProbeReadingsCache(timers.now);
  const windows = Array.from({ length: 25 }, (_, index) => range(index * 2 * DAY));
  for (const window of windows.slice(0, 24)) cache.put(window, readings(window));
  for (const window of windows.slice(0, 24)) assert.equal(cache.ready(window), true);
  cache.put(windows[0], readings(windows[0], { value: 9 }));
  cache.put(windows[24], readings(windows[24]));
  assert.equal(cache.ready(windows[0]), true, 'Replacement refreshes insertion position');
  assert.equal(cache.preview(windows[0]).probes[0].points[0][1], 9);
  assert.equal(cache.ready(windows[1]), false);
  assert.equal(cache.preview(windows[1]), null);
  for (const window of windows.slice(2)) assert.equal(cache.ready(window), true);
  cache.clear();
  for (const window of windows) {
    assert.equal(cache.ready(window), false);
    assert.equal(cache.preview(window), null);
  }
});

test('TTL expires readiness at 60000 ms while stale data remains available as preview', () => {
  const timers = clock();
  const { ProbeReadingsCache } = load('../src/utils/probeReadings.ts', timers);
  const cache = new ProbeReadingsCache(timers.now);
  const window = range();
  cache.put(window, readings(window));
  timers.advance(59_999);
  assert.equal(cache.ready(window), true);
  timers.advance(1);
  assert.equal(cache.ready(window), false);
  assert.ok(cache.preview(window));
  cache.put(window, readings(window, { value: 4 }));
  timers.advance(59_999);
  assert.equal(cache.ready(window), true);
  assert.equal(cache.preview(window).probes[0].points[0][1], 4);
});

test('resolution thresholds are inclusive at 2 and 31 days', () => {
  const { requestedProbeResolution } = load('../src/utils/probeReadings.ts', clock());
  for (const [duration, expected] of [[DAY, 1], [2 * DAY, 1], [2 * DAY + 1, 15], [31 * DAY, 15], [31 * DAY + 1, 60]]) {
    assert.equal(requestedProbeResolution(range(0, duration)), expected);
  }
});

test('exact windows accept their resolution, covered windows require sufficient detail', () => {
  const { ProbeReadingsCache } = load('../src/utils/probeReadings.ts', clock());
  const outer = range(0, 40 * DAY);
  for (const [resolution, dayReady, weekReady, monthReady] of [[1, true, true, true], [15, false, true, true], [60, false, false, true]]) {
    const cache = new ProbeReadingsCache(() => 0);
    cache.put(outer, readings(outer, { resolution }));
    assert.equal(cache.ready(outer), true);
    assert.equal(cache.ready(range(DAY)), dayReady);
    assert.equal(cache.ready(range(DAY, 7 * DAY)), weekReady);
    assert.equal(cache.ready(range(DAY, 32 * DAY)), monthReady);
    assert.equal(cache.ready(range(-DAY, 2 * DAY)), false, 'Partial overlap is only a preview');
    assert.equal(cache.preview(range(outer.end)), null, 'Touching half-open windows do not overlap');
    assert.ok(cache.preview(range(-DAY, 2 * DAY)));
  }
});

test('preview prefers complete coverage, then finer resolution, then newer readings', () => {
  const timers = clock();
  const { ProbeReadingsCache } = load('../src/utils/probeReadings.ts', timers);
  const cache = new ProbeReadingsCache(timers.now);
  const target = range(DAY);
  const marker = () => cache.preview(target).probes[0].points[0][1];
  const put = (window, resolution, value) => {
    timers.advance(1);
    cache.put(window, readings(window, { resolution, points: [[DAY, value, null, null]] }));
  };
  put(range(0, 4 * DAY), 60, 1);
  put(range(DAY, DAY / 2), 1, 2);
  assert.equal(marker(), 1);
  put(range(0, 3 * DAY), 15, 3);
  assert.equal(marker(), 3);
  put(range(DAY / 2, 3 * DAY), 15, 4);
  assert.equal(marker(), 4);
});

test('zoom projects immediately, keeps zero and gaps, excludes end and does not mutate source', async () => {
  const h = harness();
  const outer = range();
  const zoom = range(0, 11 * MINUTE);
  const points = [[-1, 99, null, null], [0, 0, null, null], [MINUTE, 2, 0, 4], [10 * MINUTE, 3, null, null], [zoom.end, 99, null, null]];
  const data = readings(outer, { points });
  h.select(outer);
  h.calls[0].resolve(data);
  await flush();
  const before = plain(data);
  const view = h.loader.read(zoom);
  assertView(view, zoom);
  assert.deepEqual(plain(view.data.probes[0].points), points.slice(1, 4));
  assert.deepEqual(plain(data), before);
  assert.equal(view.data.target_temperature_c, '0');
  assert.deepEqual(plain(view.data.monitoring), before.monitoring);
  h.select(zoom);
  h.timers.advance(140);
  assert.equal(h.calls.length, 1, 'Fine covering cache avoids an API request');
});

test('coarse preview stays visible throughout the debounced fine request', async () => {
  const h = harness();
  const outer = range(0, 40 * DAY);
  const zoom = range();
  h.select(outer);
  await h.answer(0, { resolution: 60 });
  assertView(h.loader.read(zoom), zoom, { loading: true });
  h.select(zoom);
  assertView(h.loader.read(zoom), zoom, { loading: true });
  h.timers.advance(139);
  assert.equal(h.calls.length, 1);
  h.timers.advance(1);
  assert.equal(h.calls.length, 2);
  assertView(h.loader.read(zoom), zoom, { loading: true });
  await h.answer(1, { value: 5 });
  assertView(h.loader.read(zoom), zoom, { value: 5 });
  assert.equal(h.loader.read(zoom).data.resolution_minutes, 1);
  assert.ok(h.notifications.every((view) => view.data !== null || view.window.end === outer.end));
});

test('non-overlapping navigation retains previous data AND previous axis until success', async () => {
  const h = harness();
  const old = range();
  const next = range(3 * DAY);
  h.select(old);
  await h.answer(0);
  assertView(h.loader.read(next), old, { loading: true });
  h.select(next);
  assertView(h.loader.read(next), old, { loading: true });
  h.timers.advance(140);
  assertView(h.loader.read(next), old, { loading: true });
  await h.answer(1, { value: 8 });
  assertView(h.loader.read(next), next, { value: 8 });
});

test('140 ms trailing debounce groups actions and starts only the final window', () => {
  const h = harness();
  h.select(range());
  assert.equal(h.calls.length, 1, 'Initial load is immediate');
  const final = range(6 * DAY);
  h.select(range(2 * DAY));
  assert.equal(h.calls[0].signal.aborted, true);
  h.timers.advance(100);
  h.select(range(4 * DAY));
  h.timers.advance(100);
  h.select(final);
  h.select({ ...final });
  h.timers.advance(139);
  assert.equal(h.calls.length, 1);
  assert.equal(h.timers.size, 1);
  h.timers.advance(1);
  assert.equal(h.calls.length, 2);
  assert.deepEqual(h.calls[1].window, final);
  assert.equal(h.timers.size, 0);
});

for (const outcome of ['success', 'error']) {
  test('aborted old request late ' + outcome + ' cannot publish or poison a revisit', async () => {
    const h = harness();
    const old = range();
    const current = range(3 * DAY);
    h.select(old);
    h.select(current);
    h.timers.advance(140);
    assert.equal(h.calls[0].signal.aborted, true);
    await h.answer(1, { value: 8 });
    const count = h.notifications.length;
    if (outcome === 'success') await h.answer(0, { value: 99 });
    else { h.calls[0].reject(new Error('Late obsolete failure')); await flush(); }
    assert.equal(h.notifications.length, count);
    assertView(h.loader.read(current), current, { value: 8 });
    assert.equal(h.loader.read(current).error, null);
    h.select(old);
    assertView(h.loader.read(old), current, { loading: true, value: 8 });
    h.timers.advance(140);
    assert.equal(h.calls.length, 3, 'Obsolete success must not become an exact cache hit');
    await h.answer(2, { value: 4 });
    assertView(h.loader.read(old), old, { value: 4 });
  });
}

test('exact cache revisit is immediate with no API, but TTL expiry revalidates without blanking', async () => {
  const h = harness();
  const first = range();
  const second = range(3 * DAY);
  h.select(first);
  await h.answer(0, { resolution: 60 });
  h.select(second);
  h.timers.advance(140);
  await h.answer(1, { value: 8 });
  h.select(first);
  assertView(h.loader.read(first), first);
  h.timers.advance(140);
  assert.equal(h.calls.length, 2);
  h.timers.advance(60_000);
  h.select(second);
  assertView(h.loader.read(second), second, { loading: true, value: 8 });
  h.timers.advance(140);
  assert.equal(h.calls.length, 3);
});

test('failure and forced retry retain previous data/axis, then clear the error on success', async () => {
  const h = harness();
  const old = range();
  const next = range(3 * DAY);
  h.select(old);
  await h.answer(0);
  h.select(next);
  h.timers.advance(140);
  const error = new Error('Controlled failure');
  h.calls[1].reject(error);
  await flush();
  assertView(h.loader.read(next), old);
  assert.equal(h.loader.read(next).error, error);
  h.loader.retry();
  assertView(h.loader.read(next), old, { loading: true });
  assert.equal(h.loader.read(next).error, null);
  h.timers.advance(0);
  assert.equal(h.calls.length, 3, 'Retry skips the normal 140 ms debounce');
  await h.answer(2, { value: 6 });
  assertView(h.loader.read(next), next, { value: 6 });
  assert.equal(h.loader.read(next).error, null);
  h.loader.retry();
  assertView(h.loader.read(next), next, { loading: true, value: 6 });
  h.timers.advance(0);
  assert.equal(h.calls.length, 4, 'Forced retry bypasses a fresh exact cache entry');
});

test('wrong-zone response becomes an error, never displayed or cached', async () => {
  const h = harness();
  const window = range();
  h.select(window);
  await h.answer(0, { zoneId: 99 });
  const view = h.loader.read(window);
  assert.equal(view.data, null);
  assert.equal(view.isLoading, false);
  assert.match(view.error.message, /Unexpected probe response/);
  h.select(range(3 * DAY));
  h.select(window);
  h.timers.advance(140);
  assert.equal(h.calls.length, 2);
  await h.answer(1);
  assertView(h.loader.read(window), window);
});

for (const outcome of ['success', 'error']) {
  test('organization abort clears cache, cancels requests and ignores late ' + outcome, async () => {
    const h = harness();
    const old = range();
    const next = range(3 * DAY);
    h.select(old);
    await h.answer(0);
    h.select(next);
    h.timers.advance(140);
    h.context.abort();
    assert.equal(h.calls[1].signal.aborted, true);
    assert.equal(h.timers.size, 0);
    const count = h.notifications.length;
    if (outcome === 'success') await h.answer(1, { value: 99 });
    else { h.calls[1].reject(new Error('Old organization')); await flush(); }
    assert.equal(h.notifications.length, count);
    for (const window of [old, next]) {
      const view = h.loader.read(window);
      assert.equal(view.data, null);
      assert.equal(view.isLoading, false);
      assert.equal(view.error, null);
    }
    h.loader.activate();
    h.select(old);
    h.loader.retry();
    h.timers.advance(1000);
    assert.equal(h.calls.length, 2, 'An aborted organization context cannot be reactivated');
    const fresh = harness({ timers: h.timers });
    fresh.select(old);
    assert.equal(fresh.loader.read(old).data, null);
    assert.equal(fresh.calls.length, 1, 'Same zone in a new organization must fetch independently');
    await fresh.answer(0, { value: 5 });
    assertView(fresh.loader.read(old), old, { value: 5 });
  });
}

test('different zone loaders never share exact cache entries', async () => {
  const first = harness();
  first.select(range());
  await first.answer(0);
  const other = harness({ zoneId: 8, timers: first.timers, context: first.context });
  other.select(range());
  assert.equal(other.loader.read(range()).data, null);
  assert.equal(other.calls.length, 1);
  await other.answer(0, { value: 8 });
  assertView(other.loader.read(range()), range(), { value: 8, zoneId: 8 });
  assertView(first.loader.read(range()), range());
});

test('dispose cancels debounce; StrictMode activate/select replay restores loading', async () => {
  const h = harness();
  const window = range();
  h.select(window);
  h.loader.dispose();
  assert.equal(h.calls[0].signal.aborted, true);
  h.loader.activate();
  h.select(window);
  assert.equal(h.calls.length, 2, 'StrictMode effect replay must issue a new initial request');
  await h.answer(0, { value: 99 });
  assert.equal(h.loader.read(window).data, null);
  assert.equal(h.loader.read(window).isLoading, true);
  await h.answer(1);
  assertView(h.loader.read(window), window);
  h.select(range(3 * DAY));
  assert.equal(h.timers.size, 1);
  h.loader.dispose();
  assert.equal(h.timers.size, 0);
  h.timers.advance(1000);
  assert.equal(h.calls.length, 2);
  h.loader.activate();
  h.select(window);
  assert.equal(h.calls.length, 3, 'Dispose also removes cached data');
  assert.equal(h.loader.read(window).data, null);
  await h.answer(2, { value: 4 });
  assertView(h.loader.read(window), window, { value: 4 });
  h.context.abort();
  assert.equal(h.loader.read(window).data, null, 'Replayed activation restores context abort listener');
});

// Minimal hook lifecycle harness: execute the actual hook and loader, not source assertions.
function hookHarness() {
  const timers = clock();
  const api = load('../src/utils/probeReadings.ts', timers);
  const slots = [];
  const calls = [];
  let cursor = 0;
  let context = new AbortController();
  const same = (a, b) => a && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const memo = (factory, dependencies) => {
    const index = cursor++;
    if (!same(slots[index]?.dependencies, dependencies)) slots[index] = { dependencies, value: factory() };
    return slots[index].value;
  };
  const hooks = {
    useMemo: memo,
    useCallback: (callback, dependencies) => memo(() => callback, dependencies),
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: initial };
      return [slots[index].value, (update) => {
        slots[index].value = typeof update === 'function' ? update(slots[index].value) : update;
      }];
    },
    useEffect(setup, dependencies) {
      const index = cursor++;
      const previous = slots[index];
      if (!same(previous?.dependencies, dependencies)) {
        slots[index] = { dependencies, setup, cleanup: previous?.cleanup, pending: true, effect: true };
      }
    },
  };
  const useReadings = load('../src/hooks/useProbeReadings.ts', timers, {
    react: hooks,
    '../utils/probeReadings': api,
    '../api/client': {
      getOrganizationResourceSignal: () => context.signal,
      apiGet(path, options) {
        let resolve, reject;
        const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
        const url = new URL(path, 'https://qa.invalid');
        calls.push({ path: url.pathname, options, resolve, reject,
          window: { start: Date.parse(url.searchParams.get('start')), end: Date.parse(url.searchParams.get('end')) } });
        return promise;
      },
    },
  }).default;
  const effects = () => slots.filter((slot) => slot.effect);
  return {
    timers, calls,
    render(zoneId, window) { cursor = 0; return useReadings(zoneId, window); },
    commit() {
      const pending = effects().filter((effect) => effect.pending);
      for (const effect of pending) effect.cleanup?.();
      for (const effect of pending) { effect.cleanup = effect.setup(); effect.pending = false; }
    },
    replay() {
      for (const effect of effects()) effect.cleanup?.();
      for (const effect of effects()) effect.cleanup = effect.setup();
    },
    changeOrganization() { context.abort(); context = new AbortController(); },
    dispose() { for (const effect of effects()) effect.cleanup?.(); },
    async answer(index, options) {
      calls[index].resolve(readings(calls[index].window, options));
      await flush();
    },
  };
}

test('actual hook wires no-store, window query, retry and StrictMode effect replay', async () => {
  const h = hookHarness();
  const window = range();
  assert.equal(h.render(7, window).data, null);
  h.commit();
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].path, '/api/thermal-zones/7/probe-readings/');
  assert.equal(h.calls[0].options.cache, 'no-store');
  assert.deepEqual(h.calls[0].window, window);
  h.replay();
  assert.equal(h.calls[0].options.signal.aborted, true);
  assert.equal(h.calls.length, 2);
  await h.answer(0, { value: 99 });
  assert.equal(h.render(7, window).data, null);
  await h.answer(1);
  assertView(h.render(7, window), window);
  h.render(7, window).retry();
  assertView(h.render(7, window), window, { loading: true });
  h.timers.advance(0);
  assert.equal(h.calls.length, 3);
  await h.answer(2, { value: 4 });
  assertView(h.render(7, window), window, { value: 4 });
  h.dispose();
  assert.equal(h.timers.size, 0);
});

test('actual hook changes zone and organization without exposing old cached readings', async () => {
  const h = hookHarness();
  const window = range();
  h.render(7, window);
  h.commit();
  await h.answer(0);
  assertView(h.render(7, window), window);
  assert.equal(h.render(8, window).data, null);
  h.commit();
  assert.equal(h.calls.length, 2);
  assert.equal(h.calls[1].path, '/api/thermal-zones/8/probe-readings/');
  await h.answer(1, { zoneId: 8, value: 8 });
  assertView(h.render(8, window), window, { zoneId: 8, value: 8 });
  h.render(8, window).retry();
  h.timers.advance(0);
  h.changeOrganization();
  assert.equal(h.calls[2].options.signal.aborted, true);
  assert.equal(h.render(8, window).data, null);
  h.commit();
  assert.equal(h.calls.length, 4);
  await h.answer(2, { zoneId: 8, value: 99 });
  assert.equal(h.render(8, window).data, null);
  await h.answer(3, { zoneId: 8, value: 5 });
  assertView(h.render(8, window), window, { zoneId: 8, value: 5 });
  assert.equal(h.render(7, window).data, null, 'Returning to the original zone must not reuse disposed cache');
  h.commit();
  assert.equal(h.calls.length, 5);
  h.dispose();
  assert.equal(h.calls[4].options.signal.aborted, true);
});


test('reselecting the same expired window revalidates while preserving curves and deduplicating pending work', async () => {
  const h = harness(); const window = range();
  h.select(window); await h.answer(0);
  h.select(window); assert.equal(h.calls.length, 1);
  h.timers.advance(60000); h.select(window);
  assertView(h.loader.read(window), window, { loading: true });
  h.select(window); h.select(window);
  h.timers.advance(h.api.PROBE_REQUEST_DEBOUNCE_MS);
  assert.equal(h.calls.length, 2);
  h.select(window); assert.equal(h.calls.length, 2);
  await h.answer(1, { value: 9 });
  assertView(h.loader.read(window), window, { value: 9 });
  h.loader.dispose();
});
