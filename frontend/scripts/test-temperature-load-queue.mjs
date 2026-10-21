import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function loadQueue() {
  const exports = {};
  const code = ts.transpileModule(readFileSync(new URL('../src/utils/temperatureLoadQueue.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  vm.runInNewContext(code, { exports, DOMException });
  return exports.createTemperatureLoadQueue;
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const tick = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
const rejectsAbort = promise => assert.rejects(promise, { name: 'AbortError' });

test('twelve loaders start FIFO with at most two real loads active by default', async () => {
  const queue = loadQueue()();
  const gates = Array.from({ length: 12 }, deferred);
  const started = [];
  let active = 0, peak = 0;
  const jobs = gates.map((gate, index) => queue.run(new AbortController().signal, async () => {
    started.push(index);
    active += 1;
    peak = Math.max(peak, active);
    try { return await gate.promise; }
    finally { active -= 1; }
  }));

  assert.deepEqual(started, [0, 1]);
  for (let index = 0; index < gates.length; index++) {
    assert.equal(active, Math.min(2, gates.length - index));
    gates[index].resolve(index);
    assert.equal(await jobs[index], index);
    await tick();
    assert.deepEqual(started, Array.from({ length: Math.min(index + 3, 12) }, (_, i) => i));
  }
  assert.deepEqual(await Promise.all(jobs), Array.from({ length: 12 }, (_, i) => i));
  assert.equal(peak, 2);
  assert.equal(active, 0);
});

test('queued cancellation rejects immediately without invocation and the next survivor starts', async () => {
  const queue = loadQueue()(1);
  const gate = deferred();
  const first = queue.run(new AbortController().signal, () => gate.promise);
  const cancelled = new AbortController();
  const started = [];
  const obsolete = queue.run(cancelled.signal, async () => { started.push('obsolete'); });
  const rejected = rejectsAbort(obsolete);
  const next = queue.run(new AbortController().signal, async () => { started.push('next'); return 0; });

  cancelled.abort();
  await rejected;
  assert.deepEqual(started, []);
  gate.resolve('first');
  assert.equal(await first, 'first');
  assert.equal(await next, 0);
  assert.deepEqual(started, ['next']);
});

for (const settlement of ['resolve', 'reject']) {
  test(`active cancellation keeps its slot until the underlying load ${settlement}s`, async () => {
    const queue = loadQueue()(1);
    const controller = new AbortController();
    const gate = deferred();
    let active = 0, peak = 0, nextStarted = false;
    const first = queue.run(controller.signal, async () => {
      active += 1;
      peak = Math.max(peak, active);
      try { return await gate.promise; }
      finally { active -= 1; }
    });
    const rejected = rejectsAbort(first);
    const next = queue.run(new AbortController().signal, async () => {
      nextStarted = true;
      active += 1;
      peak = Math.max(peak, active);
      active -= 1;
      return 'current';
    });

    controller.abort();
    await rejected;
    await tick();
    assert.equal(active, 1);
    assert.equal(nextStarted, false);
    if (settlement === 'resolve') gate.resolve('stale success');
    else gate.reject(new Error('obsolete failure'));
    assert.equal(await next, 'current');
    assert.equal(peak, 1);
    assert.equal(active, 0);
    await rejected;
  });
}

test('completion checks cancellation even when load resolves before its continuation runs', async () => {
  const queue = loadQueue()(1);
  const controller = new AbortController();
  const gate = deferred();
  let published = false;
  const job = queue.run(controller.signal, () => gate.promise).then(value => {
    published = true;
    return value;
  });
  const rejected = rejectsAbort(job);
  gate.resolve('stale');
  controller.abort();
  await rejected;
  assert.equal(published, false);
  assert.equal(await queue.run(new AbortController().signal, async () => 'fresh'), 'fresh');
});

test('failure releases capacity, preserves the error and lets FIFO work and a retry enter', async () => {
  const queue = loadQueue()(1);
  const gate = deferred();
  const failure = new Error('temperature unavailable');
  const started = [];
  const first = queue.run(new AbortController().signal, () => gate.promise);
  const rejected = assert.rejects(first, error => error === failure);
  const waiting = queue.run(new AbortController().signal, async () => { started.push('waiting'); return null; });
  gate.reject(failure);
  await rejected;
  const retry = queue.run(new AbortController().signal, async () => { started.push('retry'); return 0; });
  assert.equal(await waiting, null);
  assert.equal(await retry, 0);
  assert.deepEqual(started, ['waiting', 'retry']);
});

test('a synchronous loader throw also releases capacity', async () => {
  const queue = loadQueue()(1);
  const failure = new Error('synchronous failure');
  const first = queue.run(new AbortController().signal, () => { throw failure; });
  const rejected = assert.rejects(first, error => error === failure);
  const next = queue.run(new AbortController().signal, async () => 'next');
  await rejected;
  assert.equal(await next, 'next');
});

test('pre-aborted jobs never load or consume capacity, regardless of abort reason', async () => {
  const queue = loadQueue()(1);
  const controller = new AbortController();
  controller.abort(new Error('old context'));
  let invoked = false;
  await rejectsAbort(queue.run(controller.signal, async () => { invoked = true; }));
  assert.equal(invoked, false);
  assert.equal(await queue.run(new AbortController().signal, async () => 0), 0);
});

test('old institution and box/unmount queues cancel completely and new jobs progress', async () => {
  const queue = loadQueue()();
  const institution = new AbortController();
  const box = new AbortController();
  const activeGates = [deferred(), deferred()];
  const activeJobs = activeGates.map(gate => queue.run(institution.signal, () => gate.promise));
  const rejectedActive = activeJobs.map(rejectsAbort);
  let obsoleteCalls = 0;
  const oldJobs = Array.from({ length: 10 }, (_, index) => queue.run(
    index % 2 === 0 ? institution.signal : box.signal,
    async () => { obsoleteCalls += 1; },
  ));
  const rejectedOld = oldJobs.map(rejectsAbort);
  const started = [];
  const newGates = [deferred(), deferred()];
  const current = newGates.map((gate, index) => queue.run(new AbortController().signal, () => {
    started.push(index);
    return gate.promise;
  }));

  institution.abort();
  box.abort();
  await Promise.all([...rejectedActive, ...rejectedOld]);
  assert.equal(obsoleteCalls, 0);
  assert.deepEqual(started, []);
  activeGates[0].resolve('old institution');
  await tick();
  assert.deepEqual(started, [0]);
  activeGates[1].reject(new Error('old box'));
  await tick();
  assert.deepEqual(started, [0, 1]);
  newGates[0].resolve('current institution');
  newGates[1].resolve('current box');
  assert.deepEqual(await Promise.all(current), ['current institution', 'current box']);
  assert.equal(obsoleteCalls, 0);
});

test('queue instances have independent capacity and a custom limit is respected', async () => {
  const createTemperatureLoadQueue = loadQueue();
  const queue = createTemperatureLoadQueue(3);
  const separate = createTemperatureLoadQueue(1);
  const gates = Array.from({ length: 4 }, deferred);
  const started = [];
  const jobs = gates.map((gate, index) => queue.run(new AbortController().signal, () => {
    started.push(index);
    return gate.promise;
  }));
  assert.deepEqual(started, [0, 1, 2]);
  assert.equal(await separate.run(new AbortController().signal, async () => 'independent'), 'independent');
  gates[1].resolve(1);
  await jobs[1];
  await tick();
  assert.deepEqual(started, [0, 1, 2, 3]);
  gates.forEach((gate, index) => gate.resolve(index));
  assert.deepEqual(await Promise.all(jobs), [0, 1, 2, 3]);
});

test('invalid limits are rejected instead of creating a permanently blocked queue', () => {
  const createTemperatureLoadQueue = loadQueue();
  for (const limit of [0, -1, 1.5, NaN, Infinity]) {
    assert.throws(() => createTemperatureLoadQueue(limit), { name: 'RangeError' });
  }
});
