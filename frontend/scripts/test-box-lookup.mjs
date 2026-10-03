import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';
import { appHarness, tick } from './app-operation-test-harness.mjs';

test('box bootstrap exhausts server pages beyond the old 1000 cap', async () => {
  const h = appHarness();
  const pending = h.context.fetchAllPages('/api/boxes/?limit=100');
  for (let page = 0; page < 12; page += 1) {
    h.requests[page].resolve({ results: Array.from({ length: 100 }, (_, index) => ({ id: page * 100 + index })),
      next: page === 11 ? null : `https://polypbase.test/api/boxes/?limit=100&offset=${(page + 1) * 100}` });
    await tick();
  }
  const items = await pending;
  assert.equal(items.length, 1200);
  assert.equal(items.at(-1).id, 1199);
});
test('pagination stops before following an obsolete organization page', async () => {
  const h = appHarness();
  const pending = h.context.fetchAllPages('/api/boxes/?limit=100');
  const rejected = assert.rejects(pending, h.context.ApiResourceCancelledError);
  h.switchOrganization();
  h.requests[0].resolve({ results: [], next: '/api/boxes/?offset=100' });
  await rejected;
  assert.equal(h.requests.length, 1);
});
for (const next of ['https://foreign.test/api/boxes/', '/login', '/api/boxes/?limit=100']) {
  test(`pagination rejects unsafe or cyclic next links: ${next}`, async () => {
    const h = appHarness();
    const pending = h.context.fetchAllPages('/api/boxes/?limit=100');
    const rejected = assert.rejects(pending, /Invalid pagination link/);
    h.requests[0].resolve({ results: [], next });
    await rejected;
    assert.equal(h.requests.length, 1);
  });
}

function proxyHarness(command = 'serve', browserOrigin = 'http://localhost:5173') {
  const configSource = readFileSync(new URL('../vite.config.ts', import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(configSource, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports, URL,
    require: name => name === 'vite' ? { defineConfig: config => config } : { default: () => ({}) },
  });
  const config = exports.default({ command });
  const h = appHarness();
  h.context.window.location.origin = browserOrigin;
  h.context.__API_PROXY_ORIGIN__ = JSON.parse(config.define.__API_PROXY_ORIGIN__);
  assert.equal(h.context.__API_PROXY_ORIGIN__, command === 'serve' ? new URL(config.server.proxy['/api']).origin : null);
  return h;
}

test('live proxy bootstrap normalizes absolute DRF links through multiple pages with overlapping IDs', async () => {
  const h = proxyHarness();
  const pending = h.context.fetchAllPages('/api/boxes/?limit=100');
  const pages = [
    { results: [{ id: 1 }, { id: 2 }], next: 'http://127.0.0.1:8000/api/boxes/?limit=100&offset=100' },
    { results: [{ id: 2 }, { id: 3 }], next: 'http://127.0.0.1:8000/api/boxes/?limit=100&offset=200' },
    { results: [{ id: 4 }], next: null },
  ];
  for (let index = 0; index < pages.length; index += 1) {
    assert.equal(h.requests[index].url, index === 0 ? '/api/boxes/?limit=100' : `/api/boxes/?limit=100&offset=${index * 100}`);
    h.requests[index].resolve(pages[index]);
    await tick();
  }
  assert.deepEqual(Array.from(await pending, item => item.id), [1, 2, 3, 4]);
  assert.ok(h.requests.every(request => request.url.startsWith('/api/boxes/')));
});

for (const next of [
  'https://foreign.test/api/boxes/?offset=100',
  'http://127.0.0.1:8001/api/boxes/?offset=100',
  'https://127.0.0.1:8000/api/boxes/?offset=100',
  'http://127.0.0.1:8000/login',
  'http://127.0.0.1:8000/api/thermal-zones/?offset=100',
  'http://127.0.0.1:8000/api/boxes/7/',
  'http://user:password@127.0.0.1:8000/api/boxes/?offset=100',
  'http://127.0.0.1:8000/api/boxes/?offset=100#fragment',
  'http://127.0.0.1:8000/api/boxes/?limit=100',
]) {
  test(`proxy pagination rejects unexpected origins, paths, credentials, fragments or normalized cycles: ${next}`, async () => {
    const h = proxyHarness();
    const pending = h.context.fetchAllPages('/api/boxes/?limit=100');
    const rejected = assert.rejects(pending, /Invalid pagination link/);
    h.requests[0].resolve({ results: [], next });
    await rejected;
    assert.equal(h.requests.length, 1);
  });
}

test('production does not trust development proxy URLs', async () => {
  const h = proxyHarness('build', 'https://polypbase.test');
  const pending = h.context.fetchAllPages('/api/boxes/?limit=100');
  const rejected = assert.rejects(pending, /Invalid pagination link/);
  h.requests[0].resolve({ results: [], next: 'http://127.0.0.1:8000/api/boxes/?offset=100' });
  await rejected;
  assert.equal(h.requests.length, 1);
});

test('production and LAN origins continue through their same-origin collection', async () => {
  for (const [command, origin] of [['build', 'https://polypbase.test'], ['serve', 'http://tablet-qa.test:5173']]) {
    const h = proxyHarness(command, origin);
    const pending = h.context.fetchAllPages('/api/boxes/?limit=100');
    h.requests[0].resolve({ results: [{ id: 1 }], next: `${origin}/api/boxes/?offset=100` });
    await tick();
    assert.equal(h.requests[1].url, '/api/boxes/?offset=100');
    h.requests[1].resolve({ results: [{ id: 2 }], next: null });
    assert.deepEqual(Array.from(await pending, item => item.id), [1, 2]);
  }
});

for (const outcome of ['success', 'failure']) {
  test(`Organization switch during a later proxy page ignores old ${outcome} and requests no further page`, async () => {
    const h = proxyHarness();
    const pending = h.context.fetchAllPages('/api/boxes/?limit=100');
    const rejected = assert.rejects(pending, h.context.ApiResourceCancelledError);
    h.requests[0].resolve({ results: [{ id: 1 }], next: 'http://127.0.0.1:8000/api/boxes/?offset=100' });
    await tick();
    h.switchOrganization();
    if (outcome === 'success') h.requests[1].resolve({ results: [{ id: 2 }], next: 'http://127.0.0.1:8000/api/boxes/?offset=200' });
    else h.requests[1].reject(new Error('old page failed'));
    await rejected;
    assert.equal(h.requests.length, 2);
  });
}

test('live proxy pagination propagates later-page failures without returning partial results', async () => {
  const h = proxyHarness();
  const pending = h.context.fetchAllPages('/api/boxes/?limit=100');
  const failure = new Error('later page unavailable');
  const rejected = assert.rejects(pending, error => error === failure);
  h.requests[0].resolve({ results: [{ id: 1 }], next: 'http://127.0.0.1:8000/api/boxes/?offset=100' });
  await tick();
  h.requests[1].reject(failure);
  await rejected;
});

function loadTypeScript(relativePath) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports });
  return exports;
}

const { filterBoxes } = loadTypeScript('../src/utils/boxLookup.ts');

function box(id, {
  globalCode,
  localCode = '',
  boxNumber,
  species = 'Aurelia aurita',
  strain = 'AA',
  zone = 'Nurserie',
}) {
  return {
    id,
    global_code: globalCode,
    local_code: localCode,
    box_number: boxNumber,
    species: { scientific_name: species },
    strain: { code: strain },
    thermal_zone: zone ? { name: zone } : null,
  };
}

const boxes = [
  box(1, { globalCode: 'AQP-AA-012', localCode: 'LOCAL-12', boxNumber: '12' }),
  box(2, { globalCode: 'AQP-AA-012-BIS', boxNumber: '112', species: 'Chrysaora quinquecirrha', strain: 'CQ' }),
  box(3, { globalCode: 'LAB-777', boxNumber: '77', species: 'Cassiopea andromeda', strain: 'CA', zone: 'Zone froide' }),
  box(4, { globalCode: 'PREFIX-42', boxNumber: '42', species: 'Aurelia coerulea', strain: 'BLUE', zone: 'Réserve' }),
];

test('matching is case-insensitive and trims surrounding whitespace', () => {
  assert.deepEqual(filterBoxes(boxes, '  aqp-AA-012  ').map(({ id }) => id), [1, 2]);
});

test('an exact global code ranks before partial matches', () => {
  const candidates = [boxes[1], boxes[0]];
  assert.deepEqual(filterBoxes(candidates, 'AQP-AA-012').map(({ id }) => id), [1, 2]);
});

test('an exact local box number ranks before partial matches', () => {
  assert.deepEqual(filterBoxes(boxes, '12').map(({ id }) => id), [1, 2]);
});

test('prefix matches rank before generic substring matches', () => {
  const candidates = [
    box(10, { globalCode: 'LAB-PREFIX-42', boxNumber: '8' }),
    box(11, { globalCode: 'PREFIX-99', boxNumber: '9' }),
  ];
  assert.deepEqual(filterBoxes(candidates, 'prefix').map(({ id }) => id), [11, 10]);
});

test('species, strain, and zone fields remain searchable', () => {
  assert.deepEqual(filterBoxes(boxes, 'cassiopea').map(({ id }) => id), [3]);
  assert.deepEqual(filterBoxes(boxes, 'blue').map(({ id }) => id), [4]);
  assert.deepEqual(filterBoxes(boxes, 'froide').map(({ id }) => id), [3]);
});
