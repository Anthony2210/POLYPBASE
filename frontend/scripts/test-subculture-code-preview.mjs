import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { getEventListeners } from 'node:events';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/hooks/useSubcultureCodePreview.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const plain = value => JSON.parse(JSON.stringify(value));
const tick = () => new Promise(resolve => setImmediate(resolve));
const preview = (...codes) => ({ reserved: false, children: codes.map((global_code, index) => ({ position: index + 1, global_code, box_number: String(index + 1) })) });

// Run the real hook with controllable reads, including servers that finish after abort.
function harness() {
  const slots = [], effects = [], requests = [];
  let cursor = 0, dirty = false, mounted = true, output, params = [17, 1, 1];
  let context = new AbortController();
  const hooks = {
    useState(initial) {
      const i = cursor++;
      if (!(i in slots)) slots[i] = { value: initial };
      return [slots[i].value, value => {
        assert.ok(mounted, 'No state write after unmount');
        slots[i].value = value; dirty = true;
      }];
    },
    useEffect(fn, deps) {
      const i = cursor++;
      if (!slots[i] || deps.some((value, j) => !Object.is(value, slots[i].deps[j]))) {
        effects.push(() => {
          slots[i]?.cleanup?.();
          slots[i] = { deps, cleanup: fn() };
        });
      }
    },
  };
  const client = {
    getOrganizationResourceSignal: () => context.signal,
    apiGet(url, options) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      requests.push({ url, options, resolve, reject });
      return promise;
    },
  };
  const exports = {};
  vm.runInNewContext(compiled, { exports, AbortController, require(name) {
    if (name === 'react') return hooks;
    assert.equal(name, '../api/client'); return client;
  } });
  function render(next = params, commit = true) {
    params = next; cursor = 0; dirty = false;
    output = exports.default(...params);
    if (commit) while (effects.length) effects.shift()();
        else effects.length = 0;
    return plain(output);
  }
  render();
  return {
    requests, render, get codes() { return plain(output); }, get context() { return context; },
    async settle() { await tick(); if (dirty) render(); },
    replaceContext() { context.abort(); context = new AbortController(); },
    unmount() { slots.forEach(slot => slot.cleanup?.()); mounted = false; },
  };
}

test('preview uses the organization-aware GET client, no-store and a nonreserving response', async () => {
  const h = harness(), request = h.requests[0];
  assert.deepEqual(h.codes, []);
  assert.equal(request.url, '/api/boxes/17/subcultures/code-preview/?count=1');
  assert.deepEqual(Object.keys(request.options).sort(), ['cache', 'signal']);
  assert.equal(request.options.cache, 'no-store');
  assert.equal(request.options.signal.aborted, false);
    assert.equal(getEventListeners(h.context.signal, 'abort').length, 1);
  request.resolve(preview('SERVER-CODE')); await h.settle();
  assert.deepEqual(h.codes, ['SERVER-CODE']);
  h.render(); assert.equal(h.requests.length, 1, 'Stable dependencies do not refetch or reserve a code');
  h.unmount(); assert.equal(request.options.signal.aborted, true);
    assert.equal(getEventListeners(h.context.signal, 'abort').length, 0);
});

for (const [name, params, codes] of [
  ['child count', [17, 1, 2], ['CHILD-A', 'CHILD-B']],
  ['parent box', [18, 1, 1], ['OTHER-PARENT']],
  ['organization', [17, 2, 1], ['OTHER-ORG']],
]) {
  test(`${name} changes hide old identities before effects and abort stale reads`, async () => {
    const h = harness();
    h.requests[0].resolve(preview('OLD')); await h.settle();
    assert.deepEqual(h.codes, ['OLD']);
    assert.deepEqual(h.render(params, false), [], 'Never render codes for the previous request key');
    h.render(params);
    assert.equal(h.requests[0].options.signal.aborted, true);
    const request = h.requests[1];
    assert.equal(request.url, `/api/boxes/${params[0]}/subcultures/code-preview/?count=${params[2]}`);
    request.resolve(preview(...codes)); await h.settle();
    assert.deepEqual(h.codes, codes);
    h.unmount();
  });
}

test('out-of-order success and failure cannot replace the latest preview', async () => {
  const h = harness();
  h.render([17, 1, 2]); h.render([18, 1, 1]);
  h.requests[2].resolve(preview('LATEST')); await h.settle();
  h.requests[0].resolve(preview('STALE')); h.requests[1].reject(new Error('Stale failure'));
  await h.settle(); assert.deepEqual(h.codes, ['LATEST']);
  assert.ok(h.requests.slice(0, 2).every(request => request.options.signal.aborted));
  h.unmount();
});

test('organization context cancellation aborts even when hook arguments have not changed', async () => {
  const h = harness();
  h.replaceContext(); assert.equal(h.requests[0].options.signal.aborted, true);
  h.requests[0].resolve(preview('OLD-ORG')); await h.settle();
  assert.deepEqual(h.codes, []);
  h.render([17, 2, 1]);
  assert.equal(h.requests[1].options.signal.aborted, false);
  h.requests[1].resolve(preview('NEW-ORG')); await h.settle();
  assert.deepEqual(h.codes, ['NEW-ORG']); h.unmount();
});

test('already-aborted organization context and unmount ignore late completions', async () => {
  const h = harness(); h.context.abort();
  h.render([18, 1, 1]);
  assert.ok(h.requests.every(request => request.options.signal.aborted));
  h.unmount();
  h.requests[0].resolve(preview('LATE')); h.requests[1].reject(new Error('Late failure'));
  await h.settle(); assert.deepEqual(h.codes, []);
});

for (const [name, result] of [
  ['reserved response', { ...preview('RESERVED'), reserved: true }],
  ['missing reserved flag', { children: preview('UNTRUSTED').children }],
  ['wrong child count', preview('ONE', 'TWO')],
  ['malformed response', null],
]) {
  test(`${name} fails gracefully without inventing child identities`, async () => {
    const h = harness(); h.requests[0].resolve(result); await h.settle();
    assert.deepEqual(h.codes, []); h.unmount();
  });
}

test('dependency cleanup removes the old context listener without leaking new listeners', () => {
  const h = harness(), oldSignal = h.context.signal;
  h.replaceContext(); h.render([17, 2, 1]);
  assert.equal(getEventListeners(oldSignal, 'abort').length, 0);
  assert.equal(getEventListeners(h.context.signal, 'abort').length, 1);
  h.render([18, 2, 1]);
  assert.equal(getEventListeners(h.context.signal, 'abort').length, 1);
  h.unmount(); assert.equal(getEventListeners(h.context.signal, 'abort').length, 0);
});

test('network failure is advisory and a later request can recover', async () => {
  const h = harness(); h.requests[0].reject(new Error('Offline')); await h.settle();
  assert.deepEqual(h.codes, []);
  h.render([17, 1, 2]); h.requests[1].resolve(preview('A', 'B')); await h.settle();
  assert.deepEqual(h.codes, ['A', 'B']); h.unmount();
});
