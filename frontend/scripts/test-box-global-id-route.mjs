import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { createHistoryFixture, installAppRouting } from './app-operation-test-harness.mjs';

// Execute App's real route parser and identity effects with controlled requests.
const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function findNode(predicate) {
  let found;
  function visit(node) {
    if (predicate(node)) found = node;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(found, 'Missing App implementation node');
  return found;
}
const globalEffect = findNode((node) => ts.isFunctionDeclaration(node) && node.name?.text === 'resolveGlobalBoxId')
  .parent.parent.parent;
assert.equal(globalEffect.expression.getText(ast), 'useEffect');
const localEffect = findNode((node) => ts.isFunctionDeclaration(node) && node.name?.text === 'resolveBoxCode')
  .parent.parent.parent;
assert.equal(localEffect.expression.getText(ast), 'useEffect');
function variable(name) {
  return findNode((node) => ts.isVariableDeclaration(node) && node.name.getText(ast) === name);
}
const resolvedDeclaration = variable('isGlobalBoxResolved');
const codeResolvedDeclaration = variable('isBoxCodeResolved');
const pendingDeclaration = variable('isGlobalBoxPending');
const codePendingDeclaration = variable('isBoxCodePending');
const selectedDeclaration = variable('selectedBoxId');
const boxRouteDeclaration = variable('isBoxRoute');
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

const GLOBAL_ID = '0b9f2c1e-4d5a-4f7b-9c3d-2e1f0a9b8c7d';

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}
async function tick() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}

function harness(path) {
  const browser = createHistoryFixture(path);
  const requests = [];
  const recoveries = [];
  const navigations = [];
  const state = { error: null, boxLoading: false };
  const organizations = [{ id: 1, name: 'Organization 1' }];
  const context = {
    URLSearchParams,
    URL,
    ApiError,
    resolvedBoxCode: null,
    isBoxCodeResolved: false,
    useMemo: (callback) => callback(),
    setResolvedBoxCode(value) { context.resolvedBoxCode = value; },
    window: { location: browser.location, history: {
      get state() { return browser.history.state; },
      replaceState(value, title, url) {
        browser.history.replaceState(value, title, url);
        navigations.push({ mode: 'replace', path: url });
      },
      pushState(value, title, url) {
        browser.history.pushState(value, title, url);
        navigations.push({ mode: 'push', path: url });
      },
      back() { browser.history.back(); },
    } },
    data: { boxes: [], profile: { organizations, memberships: organizations.map((organization) => ({ organization })) } },
    activeOrganizationId: 1,
    isLoginRoute: false,
    passwordReset: null,
    isLoading: false,
    needsOrganizationChoice: false,
    error: null,
    organizationRequestGenerationRef: { current: 0 },
    navigationGenerationRef: { current: 0 },
    openBoxRequestGenerationRef: { current: 0 },
    setRoute(value) { context.route = value; },
    setError(value) { state.error = value; context.error = value; },
    setIsBoxLoading(value) { state.boxLoading = value; },
    apiGet(url) {
      const request = { ...deferred(), method: 'GET', url, organizationId: context.activeOrganizationId };
      requests.push(request);
      return request.promise;
    },
    apiPost(url) {
      const request = { ...deferred(), method: 'POST', url };
      requests.push(request);
      return request.promise;
    },
    getApplicationError(error) {
      const recovery = { ...deferred(), error };
      recoveries.push(recovery);
      return recovery.promise;
    },
  };
  vm.createContext(context);
  function evaluate(code) {
    const { outputText } = ts.transpileModule(code, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
    });
    return vm.runInContext(outputText, context);
  }
  installAppRouting(context, evaluate, context.activeOrganizationId);
  navigations.length = 0;
  context.route = context.getCurrentRoute();
  let cleanup;
  function derive() {
    context.isBoxRoute = evaluate(boxRouteDeclaration.initializer.getText(ast));
    context.isBoxCodeResolved = evaluate(codeResolvedDeclaration.initializer.getText(ast));
    context.isGlobalBoxResolved = evaluate(resolvedDeclaration.initializer.getText(ast));
    context.isGlobalBoxPending = evaluate(pendingDeclaration.initializer.getText(ast));
    context.selectedBoxId = evaluate(selectedDeclaration.initializer.getText(ast));
    context.isBoxCodePending = evaluate(codePendingDeclaration.initializer.getText(ast));
  }
  function render() {
    derive();
    cleanup?.();
    const cleanups = [localEffect, globalEffect].map((effect) =>
      evaluate(`(${effect.arguments[0].getText(ast)})()`));
    cleanup = () => cleanups.forEach((dispose) => dispose?.());
    derive();
  }
  return { context, state, requests, recoveries, navigations, render, derive, browser, unmount() { cleanup?.(); } };
}

test('Local ID routes use exact authorized lookup without searching or loading the collection', async () => {
  const code = 'AUR /42';
  const h = harness(`/boxes/${encodeURIComponent(code)}`);
  h.render();
  assert.equal(h.context.isBoxCodePending, true);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, '/api/boxes/by-code/AUR%20%2F42/');
  assert.equal(h.requests[0].organizationId, 1);
  h.requests[0].resolve({ id: 42, global_code: code });
  await tick();
  h.derive();
  assert.equal(h.context.selectedBoxId, 42);
  assert.equal(h.context.isBoxCodePending, false);
  assert.equal(h.navigations.length, 0);
  h.render();
  assert.equal(h.requests.length, 1);
});

test('a cached current code cannot bypass server rejection of conflicting claims', async () => {
  const h = harness('/boxes/CURRENT-42');
  h.context.data.boxes = [{ id: 42, global_code: 'CURRENT-42' }];
  h.render();
  assert.equal(h.context.selectedBoxId, null);
  assert.equal(h.requests[0].url, '/api/boxes/by-code/CURRENT-42/');
  h.requests[0].reject(new ApiError(404, 'Not found'));
  await tick();
  h.derive();
  assert.equal(h.context.selectedBoxId, null);
  assert.equal(h.context.isBoxCodePending, false);
});

test('registered aliases and previous Local IDs replace the URL with the canonical code', async () => {
  for (const code of ['REGISTERED-ALIAS', 'PCA-CHO-1.005']) {
    const h = harness(`/boxes/${code}`);
    h.render();
    assert.equal(h.requests[0].url, `/api/boxes/by-code/${code}/`);
    h.requests[0].resolve({ id: 42, global_code: 'AUR /42', previous_global_code: 'PCA-CHO-1.005' });
    await tick();
    h.derive();
    assert.equal(h.context.selectedBoxId, 42);
    assert.equal(h.context.route.boxCode, 'AUR /42');
    assert.equal(h.navigations.length, 1);
    assert.equal(h.navigations[0].mode, 'replace');
    assert.equal(h.browser.location.pathname, '/boxes/AUR%20%2F42');
    assert.equal(h.requests.length, 1);
  }
});

test('Local ID 404 is an institution-scoped not-found state, not fuzzy search fallback', async () => {
  const h = harness('/boxes/OTHER-ORG');
  h.render();
  h.requests[0].reject(new ApiError(404, 'Not found'));
  await tick();
  h.derive();
  assert.equal(h.context.isBoxCodePending, false);
  assert.equal(h.context.selectedBoxId, null);
  assert.equal(h.state.error, null);
  assert.equal(h.recoveries.length, 0);
  h.render();
  assert.equal(h.requests.length, 1);
  h.context.activeOrganizationId = 2;
  h.render();
  assert.equal(h.context.isBoxCodePending, true);
  assert.equal(h.requests[1].organizationId, 2);
});

test('Local ID resolution waits for the active institution and bootstrap', () => {
  for (const blocked of [
    { activeOrganizationId: null }, { isLoading: true },
    { isLoginRoute: true }, { needsOrganizationChoice: true },
  ]) {
    const h = harness('/boxes/AUR-42');
    Object.assign(h.context, blocked);
    h.render();
    assert.equal(h.requests.length, 0);
  }
});

test('Local ID failures use the application error without changing the route', async () => {
  for (const status of [403, 500]) {
    const h = harness('/boxes/AUR-42');
    h.render();
    h.requests[0].reject(new ApiError(status, 'Unavailable'));
    await tick();
    h.recoveries[0].resolve({ message: 'Unavailable', requiresAuthentication: false });
    await tick();
    assert.equal(h.state.error.message, 'Unavailable');
    assert.equal(h.context.resolvedBoxCode, null);
    assert.equal(h.navigations.length, 0);
  }
});

test('Local ID resolutions and delayed errors cannot publish after supersession', async () => {
  for (const supersede of [
    (h) => { h.context.organizationRequestGenerationRef.current += 1; },
    (h) => { h.context.navigationGenerationRef.current += 1; },
    (h) => h.unmount(),
  ]) {
    for (const outcome of ['success', 'not-found', 'delayed-error']) {
      const h = harness('/boxes/REGISTERED-ALIAS');
      h.render();
      if (outcome === 'delayed-error') {
        h.requests[0].reject(new ApiError(500, 'Unavailable'));
        await tick();
      }
      supersede(h);
      if (outcome === 'success') h.requests[0].resolve({ id: 42, global_code: 'CANONICAL-42' });
      if (outcome === 'not-found') h.requests[0].reject(new ApiError(404, 'Not found'));
      if (outcome === 'delayed-error') {
        h.recoveries[0].resolve({ message: 'Unavailable', requiresAuthentication: false });
      }
      await tick();
      assert.equal(h.context.resolvedBoxCode, null);
      assert.equal(h.state.error, null);
      assert.equal(h.navigations.length, 0);
    }
  }
});

test('manual legacy local_code lookup keeps its search interpretation', async () => {
  const h = harness('/');
  h.context.fetchAllPages = async (url) => {
    assert.equal(url, '/api/boxes/?limit=100&q=001');
    return [{ id: 42, global_code: 'AUR-JKA-1.001', local_code: '001' }];
  };
  h.context.BOX_LIST_LIMIT = 100;
  assert.equal(await h.context.findBoxIdByCode('001'), 42);
  assert.equal(h.requests.length, 0, 'manual lookup does not call the exact route endpoint');
});

test('Global ID paths parse as a pending handoff, distinct from Local ID routes', () => {
  for (const path of [`/boxes/global/${GLOBAL_ID}`, `/boxes/global/${GLOBAL_ID}/`,
    `/boxes/global/${GLOBAL_ID.toUpperCase()}`]) {
    const route = harness(path).context.route;
    assert.equal(route.globalBoxId, GLOBAL_ID, path);
    assert.equal(route.boxCode, null);
    assert.equal(route.boxId, null);
    assert.equal(route.scanBoxId, undefined);
  }
  // Existing Local ID URLs keep their exact meaning, including a code named "global".
  for (const [path, code] of [['/boxes/AUR-JKA-1.001', 'AUR-JKA-1.001'], ['/boxes/global', 'global'],
    ['/boxes/AUR%20%2F42', 'AUR /42']]) {
    const route = harness(path).context.route;
    assert.equal(route.boxCode, code, path);
    assert.equal(route.globalBoxId, undefined);
  }
  for (const path of ['/boxes/global/not-a-uuid', `/boxes/global/${GLOBAL_ID}/extra`, '/boxes/global/42']) {
    assert.equal(harness(path).context.route.globalBoxId, undefined, path);
  }
  // The numeric QR handoff is untouched.
  for (const path of ['/bac/42', '/bac/42/', '/?scan_box=42']) {
    const route = harness(path).context.route;
    assert.equal(route.scanBoxId, 42, path);
    assert.equal(route.globalBoxId, undefined);
  }
});

test('Global ID paths are recognized in-app paths', () => {
  const h = harness('/');
  assert.equal(h.context.isRecognizedAppPath(`/boxes/global/${GLOBAL_ID}`, true, false), true);
  assert.equal(h.context.isRecognizedAppPath('/boxes/global/not-a-uuid/x', true, false), false);
  assert.equal(h.context.isRecognizedAppPath('/boxes/AUR-JKA-1.001', true, false), true);
});

test('resolves in the active institution and keeps the UUID URL', async () => {
  const h = harness(`/boxes/global/${GLOBAL_ID}`);
  h.render();
  assert.equal(h.context.isBoxRoute, true, 'renders the Box page, not the home page');
  assert.equal(h.context.isGlobalBoxPending, true);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].method, 'GET');
  assert.equal(h.requests[0].url, `/api/boxes/global/${GLOBAL_ID}/`);
  assert.equal(h.requests[0].organizationId, 1);
  h.requests[0].resolve({ id: 42, global_id: GLOBAL_ID, global_code: 'AUR /42' });
  await tick();
  h.derive();
  assert.equal(h.context.selectedBoxId, 42);
  assert.equal(h.context.isGlobalBoxPending, false);
  assert.equal(h.context.route.globalBoxId, GLOBAL_ID);
  assert.equal(h.navigations.length, 0, 'the URL is never replaced by the mutable Local ID');
  assert.equal(h.browser.location.pathname, `/boxes/global/${GLOBAL_ID}`);
  assert.equal(h.requests.filter((request) => request.method === 'POST').length, 0, 'no QR scan audit');
  // Rerendering a resolved route requests nothing more.
  h.render();
  assert.equal(h.requests.length, 1);
});

test('a resolution belongs to one institution and one Global ID', async () => {
  const h = harness(`/boxes/global/${GLOBAL_ID}`);
  h.render();
  h.requests[0].resolve({ id: 42, global_id: GLOBAL_ID, global_code: 'AUR-42' });
  await tick();
  h.derive();
  assert.equal(h.context.selectedBoxId, 42);
  // A Global ID entry never satisfies a Local ID code route.
  const resolvedRoute = h.context.route;
  for (const boxCode of ['', 'AUR-42']) {
    h.context.route = { tab: 'pilotage', boxCode, boxId: null };
    h.derive();
    assert.equal(h.context.isBoxCodeResolved, false, boxCode);
  }
  h.context.route = resolvedRoute;
  h.context.activeOrganizationId = 2;
  h.render();
  assert.equal(h.context.selectedBoxId, null, 'another institution never reuses the mapping');
  assert.equal(h.requests.length, 2);
  assert.equal(h.requests[1].organizationId, 2);
  const other = '1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f';
  h.context.route = { tab: 'pilotage', boxCode: null, boxId: null, globalBoxId: other };
  h.derive();
  assert.equal(h.context.selectedBoxId, null);
});

test('back and forward reparse the UUID route from the browser path', () => {
  const h = harness('/boxes/AUR-JKA-1.001');
  assert.equal(h.context.route.boxCode, 'AUR-JKA-1.001');
  h.browser.history.pushState(null, '', `/boxes/global/${GLOBAL_ID}`);
  assert.equal(h.context.getCurrentRoute().globalBoxId, GLOBAL_ID);
  h.browser.travel(-1, () => {});
  assert.equal(h.context.getCurrentRoute().boxCode, 'AUR-JKA-1.001');
  h.browser.travel(1, () => {});
  assert.equal(h.context.getCurrentRoute().globalBoxId, GLOBAL_ID);
});

test('waits for readiness; an unknown Global ID shows the not-found Box state', async () => {
  const h = harness(`/boxes/global/${GLOBAL_ID}`);
  h.context.activeOrganizationId = null;
  h.render();
  assert.equal(h.requests.length, 0);
  h.context.isLoading = true;
  h.context.activeOrganizationId = 1;
  h.render();
  assert.equal(h.requests.length, 0);
  h.context.isLoading = false;
  h.render();
  h.requests[0].reject(new ApiError(404, 'Not found'));
  await tick();
  h.derive();
  assert.equal(h.state.error, null);
  assert.equal(h.recoveries.length, 0);
  assert.equal(h.context.isGlobalBoxPending, false);
  assert.equal(h.context.selectedBoxId, null);
  assert.equal(h.navigations.length, 0);
});

test('other failures are reported through the application error', async () => {
  const h = harness(`/boxes/global/${GLOBAL_ID}`);
  h.render();
  h.requests[0].reject(new ApiError(403, 'Forbidden'));
  await tick();
  h.recoveries[0].resolve({ message: 'Forbidden', requiresAuthentication: false });
  await tick();
  assert.deepEqual(h.state.error, { message: 'Forbidden', requiresAuthentication: false });
  assert.equal(h.context.resolvedBoxCode, null);
});

test('ignores a stale resolution after organization change, navigation or unmount', async () => {
  const supersede = [
    (h) => { h.context.organizationRequestGenerationRef.current += 1; },
    (h) => { h.context.navigationGenerationRef.current += 1; },
    (h) => h.unmount(),
  ];
  for (const apply of supersede) {
    const h = harness(`/boxes/global/${GLOBAL_ID}`);
    h.render();
    apply(h);
    h.requests[0].resolve({ id: 7, global_id: GLOBAL_ID, global_code: 'STALE-7' });
    await tick();
    assert.equal(h.context.resolvedBoxCode, null);
    assert.equal(h.navigations.length, 0);
    assert.equal(h.state.error, null);
  }
});
