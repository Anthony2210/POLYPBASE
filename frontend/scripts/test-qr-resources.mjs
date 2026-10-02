import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { resourceClient } from './resource-test-harness.mjs';

const tick = async () => { for (let i = 0; i < 30; i++) await Promise.resolve(); };
const svgResponse = { ok: true, text: async () => '<svg/>' };

test('resource requests capture organization headers and same-context selection preserves lifetime', async () => {
  const requests = [];
  const client = resourceClient({ fetch: async (url, options) => { requests.push({ url, options }); return svgResponse; } });
  const signal = client.getOrganizationResourceSignal();
  client.setActiveOrganizationContext(1);
  assert.equal(client.getOrganizationResourceSignal(), signal);
  assert.equal(await client.apiGetResource('/boites/17/qr.svg'), '<svg/>');
  assert.equal(requests[0].options.headers.get('X-Organization-Id'), '1');
  assert.equal(requests[0].options.credentials, 'include');
  assert.equal(requests[0].options.cache, 'no-store');
  client.setActiveOrganizationContext(2);
  await client.apiGetResource('/boites/17/qr.svg');
  assert.equal(requests[1].options.headers.get('X-Organization-Id'), '2');
  assert.equal(signal.aborted, true);
});

test('missing organization and foreign origins fail without fetching or fallback', async () => {
  let calls = 0;
  const client = resourceClient({ fetch: async () => { calls++; return svgResponse; } });
  await assert.rejects(client.apiGetResource('https://other.test/boites/17/qr.svg'), (error) => error instanceof client.ApiError);
  client.setActiveOrganizationContext(null);
  await assert.rejects(client.apiGetResource('/boites/17/qr.svg'), (error) => error instanceof client.ApiError);
  assert.equal(calls, 0);
});

test('A-B-A invalidates resource body reads even when body ignores abort', async () => {
  let resolveBody;
  const client = resourceClient({ fetch: async () => ({ ok: true, text: () => new Promise((resolve) => { resolveBody = resolve; }) }) });
  const oldSignal = client.getOrganizationResourceSignal();
  const pending = client.apiGetResource('/boites/17/qr.svg');
  await tick();
  client.setActiveOrganizationContext(2);
  client.setActiveOrganizationContext(1);
  await assert.rejects(pending, (error) => error instanceof client.ApiResourceCancelledError);
  assert.notEqual(client.getOrganizationResourceSignal(), oldSignal);
  await assert.rejects(client.apiGetResource('/boites/17/qr.svg', { contextSignal: oldSignal }), (error) => error instanceof client.ApiResourceCancelledError);
  resolveBody('<svg/>');
  await tick();
});

const previewCode = ts.transpileModule(readFileSync(new URL('../src/components/QrLabel.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

function previewHarness() {
  const requests = [], created = [], revoked = [], slots = [], effects = [], cleanups = [];
  let index = 0;
  const client = resourceClient({ fetch: (url, options) => new Promise((resolve) => requests.push({ url, options, resolve })) });
  const hooks = {
    useState(initial) { const i = index++; if (!(i in slots)) slots[i] = initial; return [slots[i], (value) => { slots[i] = value; }]; },
    useEffect(fn, deps) {
      const i = index++;
      if (!slots[i] || deps.some((dep, j) => dep !== slots[i][j])) {
        slots[i] = deps;
        effects.push(() => { cleanups[i]?.(); cleanups[i] = fn(); });
      }
    },
  };
  const jsx = (type, props) => ({ type, props });
  const exports = {};
  vm.runInNewContext(previewCode, {
    exports, AbortController, Blob,
    URL: { createObjectURL(blob) { const url = `blob:qr-${created.length}`; created.push({ url, blob }); return url; }, revokeObjectURL(url) { revoked.push(url); } },
    require(name) { if (name === 'react') return hooks; if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx }; return client; },
  });
  let source = '/boites/17/qr.svg';
  function render() {
    index = 0;
    const tree = exports.default({ item: { globalCode: 'BOX-17', speciesName: 'Aurelia', qrImageUrl: source } });
    while (effects.length) effects.shift()();
    return tree.props.children[0].props;
  }
  return { client, requests, created, revoked, render, changeSource(value) { source = value; }, unmount() { cleanups.forEach((cleanup) => cleanup?.()); } };
}

test('preview renders only fetched SVG and clears/revokes it on context invalidation', async () => {
  const h = previewHarness();
  assert.equal(h.render().src, undefined);
  assert.equal(h.requests[0].options.headers.get('X-Organization-Id'), '1');
  h.requests[0].resolve(svgResponse);
  await tick();
  assert.equal(h.render().src, 'blob:qr-0');
  assert.equal(h.created[0].blob.type, 'image/svg+xml');
  h.client.setActiveOrganizationContext(2);
  h.client.setActiveOrganizationContext(1);
  assert.deepEqual(h.revoked, ['blob:qr-0']);
  assert.equal(h.render().src, undefined);
  h.requests[1].resolve(svgResponse);
  await tick();
  assert.equal(h.render().src, 'blob:qr-1');
  h.unmount();
  assert.deepEqual(h.revoked, ['blob:qr-0', 'blob:qr-1']);
});

test('preview ignores late fetch after A-B-A, source replacement, and unmount', async () => {
  const h = previewHarness();
  h.render();
  h.client.setActiveOrganizationContext(2);
  h.client.setActiveOrganizationContext(1);
  h.render();
  h.requests[0].resolve(svgResponse);
  await tick();
  assert.equal(h.created.length, 0);
  h.changeSource('/boites/18/qr.svg');
  assert.equal(h.render().src, undefined);
  assert.equal(h.requests[1].options.signal.aborted, true);
  h.requests[1].resolve(svgResponse);
  h.unmount();
  h.requests[2].resolve(svgResponse);
  await tick();
  assert.equal(h.created.length, 0);
  assert.equal(h.requests[2].options.signal.aborted, true);
});
