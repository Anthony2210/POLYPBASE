import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { createHistoryFixture, installAppRouting } from './app-operation-test-harness.mjs';

// Execute App's real route parser and effect with controlled request lifetimes.
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
function functionNode(name) {
  return findNode((node) => ts.isFunctionDeclaration(node) && node.name?.text === name);
}
const scanEffect = functionNode('handoffScan').parent.parent.parent;
assert.equal(scanEffect.expression.getText(ast), 'useEffect');
const readiness = findNode((node) => ts.isVariableDeclaration(node) && node.name.getText(ast) === 'isScanReady');
const scannerEffect = findNode((node) => ts.isCallExpression(node)
  && node.expression.getText(ast) === 'useEffect'
  && node.arguments[1]?.getText(ast) === '[activeOrganizationId]'
  && node.arguments[0].getText(ast).includes('setIsPhoneQrOpen(false)'));
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}
async function tick() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}
function harness(path = '/?scan_box=42') {
  const browser = createHistoryFixture(path);
  const organizations = [1, 2].map((id) => ({ id, name: `Organization ${id}` }));
  const requests = [];
  const recoveries = [];
  const navigations = [];
  const state = { error: null, boxLoading: false, labels: [{ id: 17 }], phoneScanner: true, tabletScanner: true };

  const context = {
    URLSearchParams,
    URL,
    ApiResourceCancelledError: class ApiResourceCancelledError extends Error {},
    window: { location: browser.location, history: {
      get state() { return browser.history.state; },
      replaceState(state, title, value) {
        browser.history.replaceState(state, title, value);
        navigations.push({ mode: 'replace', path: value });
      },
      pushState(state, title, value) {
        browser.history.pushState(state, title, value);
        navigations.push({ mode: 'push', path: value });
      },
      back() { browser.history.back(); },
    } },
    data: { boxes: [], profile: { organizations, memberships: organizations.map((organization) => ({ organization })) } },
    activeOrganizationId: 1,
    activeTab: 'pilotage',
    isBoxRoute: false,
    isZoneRoute: false,
    isLoginRoute: false,
    passwordReset: null,
    isLoading: false,
    needsOrganizationChoice: false,
    error: null,
    BOX_LIST_LIMIT: 120,
    organizationRequestGenerationRef: { current: 0 },
    navigationGenerationRef: { current: 0 },
    openBoxRequestGenerationRef: { current: 0 },
    setRoute(value) { context.route = value; },
    setError(value) { state.error = value; context.error = value; },
    setIsBoxLoading(value) { state.boxLoading = value; },
    setIsLoginRoute(value) { context.isLoginRoute = value; },
    setPasswordReset(value) { context.passwordReset = value; },
    setIsLoading(value) { context.isLoading = value; },
    setActiveOrganizationId(value) { context.activeOrganizationId = value; },
    setNeedsOrganizationChoice(value) { context.needsOrganizationChoice = value; },
    setQrLabelSelection(value) { state.labels = value; },
    setIsPhoneQrOpen(value) { state.phoneScanner = value; },
    setIsTabletScannerOpen(value) { state.tabletScanner = value; },
    setIsOrganizationMenuOpen() {}, setIsCreateBoxOpen() {}, setSearch() {},
    setRecentBoxIds() {}, setMeasurementPrefill() {}, setExportOptionsRequested() {},
    setActiveOrganizationContext(value) { state.organizationContext = value; },
    setData(value) { context.data = typeof value === 'function' ? value(context.data) : value; },
    mergeBoxDetail(current, detail) { return { ...current, boxDetails: { [detail.id]: detail } }; },
    apiPost(url, payload) {
      const request = { ...deferred(), method: 'POST', url, payload, organizationId: context.activeOrganizationId };
      requests.push(request);
      return request.promise;
    },
    apiGet(url) {
      const request = { ...deferred(), method: 'GET', url, organizationId: context.activeOrganizationId };
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
  // Initial history ownership is metadata setup, not a scan navigation.
  navigations.length = 0;
  for (const name of ['handleAuthenticated', 'openBox', 'openScannedBox',
    'getSelectableOrganizations', 'getOrganizationById', 'resolveActiveOrganizationId', 'setProfileActiveOrganization',
    'getOperationRequests', 'fetchAllPages', 'fetchScopedData', 'chooseOrganization']) {
    evaluate(functionNode(name).getText(ast));
  }
  context.route = context.getCurrentRoute();
  let lastDependencies;
  let cleanup;
  function render() {
    context.isScanReady = evaluate(readiness.initializer.getText(ast));
    const dependencies = evaluate(scanEffect.arguments[1].getText(ast));
    if (lastDependencies && dependencies.every((value, index) => value === lastDependencies[index])) return;
    cleanup?.();
    lastDependencies = dependencies;
    cleanup = evaluate(`(${scanEffect.arguments[0].getText(ast)})()`) ?? undefined;
  }
  return { context, state, requests, recoveries, navigations, render, browser, evaluate,
    unmount() { cleanup?.(); },
    closeScanners() { evaluate(`(${scannerEffect.arguments[0].getText(ast)})()`); },
  };
}

test('permanent QR opens the authorized detail directly without a box-list match', async () => {
  const h = harness('/?scan_box=42');
  h.render();
  h.requests[0].resolve({ global_code: 'LATE-PAGE-42' });
  await tick();
  assert.equal(h.context.data.boxes.length, 0);
  h.context.useMemo = callback => callback();
  Object.assign(h.context, { resolvedBoxCode: null, isBoxCodeResolved: false });
  const selectedId = findNode(node => ts.isVariableDeclaration(node) && node.name.getText(ast) === 'selectedBoxId');
  h.context.selectedBoxId = h.evaluate(selectedId.initializer.getText(ast));
  assert.equal(h.context.selectedBoxId, 42);
  h.context.selectedBoxDetail = null;
  const detailEffect = functionNode('loadBoxDetail').parent.parent.parent;
  h.evaluate(`(${detailEffect.arguments[0].getText(ast)})()`);
  assert.equal(h.requests[1].url, '/api/boxes/42/');
  assert.equal(h.requests[1].method, 'GET');
  const detail = { id: 42, global_code: 'LATE-PAGE-42' };
  h.requests[1].resolve(detail);
  await tick();
  assert.equal(h.context.data.boxDetails[42], detail);
});

test('parses a pending handoff without exposing a box route or looking up data', () => {
  for (const path of ['/?scan_box=42', '/bac/42', '/bac/42/']) {
    const h = harness(path);
    assert.equal(h.context.route.scanBoxId, 42);
    assert.equal(h.context.route.boxId, null);
    assert.equal(h.context.route.boxCode, null);
    assert.equal(h.requests.length, 0);
  }
  for (const path of ['/?scan_box=0', '/?scan_box=-1', '/?scan_box=abc', '/?scan_box=1.5',
    '/?scan_box=9007199254740992', '/?scan_box=', '/zones?scan_box=42', '/login?scan_box=42']) {
    assert.equal(harness(path).context.route.scanBoxId, undefined, path);
  }
});

test('login next preserves the scan, which waits for profile and organization readiness', async () => {
  const h = harness('/login?next=%2F%3Fscan_box%3D42');
  h.context.isLoginRoute = true;
  h.context.data.profile = null;
  h.render();
  assert.equal(h.requests.length, 0);
  h.context.handleAuthenticated();
  assert.equal(h.context.route.scanBoxId, 42);
  h.context.isLoading = true;
  h.render();
  assert.equal(h.requests.length, 0);
  h.context.data.profile = { organizations: [{ id: 1 }], memberships: [] };
  h.context.isLoading = false;
  h.context.activeOrganizationId = null;
  h.context.needsOrganizationChoice = true;
  h.render();
  assert.equal(h.requests.length, 0);
  h.context.activeOrganizationId = 1;
  h.context.needsOrganizationChoice = false;
  h.render();
  assert.equal(h.requests.length, 1);
  const request = h.requests[0];
  assert.equal(request.method, 'POST');
  assert.equal(request.url, '/api/boxes/42/scan/');
  assert.deepEqual(Object.keys(request.payload), []);
  assert.equal(request.organizationId, 1);
  request.resolve({ global_code: 'AUR /42' });
  await tick();
  assert.equal(h.context.route.boxCode, 'AUR /42');
  assert.equal(h.context.route.boxId, 42, 'permanent QR retains the authorized id even without a cached list item');
  assert.equal(h.context.route.scanBoxId, undefined);
  assert.deepEqual(h.navigations.at(-1), { mode: 'replace', path: '/boxes/AUR%20%2F42' });
  assert.equal(h.state.boxLoading, false);
  h.render();
  assert.equal(h.requests.length, 1);
});

test('ordinary rerenders and profile updates do not submit the scan twice', async () => {
  const h = harness();
  h.render();
  h.render();
  h.context.data.profile = { ...h.context.data.profile, interface_language: 'en' };
  h.render();
  assert.equal(h.requests.length, 1);
  h.requests[0].resolve({ global_code: 'AUR-42' });
  await tick();
});

test('keeps the existing organization preference and fallback policy', () => {
  const h = harness();
  const profile = h.context.data.profile;
  assert.equal(h.context.resolveActiveOrganizationId(profile, 2), 2);
  assert.equal(h.context.resolveActiveOrganizationId(profile, null), null);
  assert.equal(h.context.resolveActiveOrganizationId(profile, 99), null);
  assert.equal(h.context.resolveActiveOrganizationId({ organizations: [{ id: 1 }], memberships: [] }, 99), 1);
  assert.equal(h.context.resolveActiveOrganizationId({ organizations: [], memberships: [] }, null), null);
  h.render();
  assert.equal(h.context.activeOrganizationId, 1);
  assert.equal(h.requests[0].organizationId, 1);
});

const invalidate = {
  organization(h) { h.context.organizationRequestGenerationRef.current += 1; },
  navigation(h) { h.context.navigateTo({ tab: 'zones', boxCode: null, boxId: null }, '/zones'); },
  openBox(h) { h.context.openBox(17, 'OTHER-17'); },
  unmount(h) { h.unmount(); },
};
for (const [name, supersede] of Object.entries(invalidate)) {
  test(`ignores scan success after ${name}`, async () => {
    const h = harness();
    h.render();
    supersede(h);
    const navigationCount = h.navigations.length;
    h.requests[0].resolve({ global_code: 'STALE-42' });
    await tick();
    assert.equal(h.navigations.length, navigationCount);
    assert.equal(h.state.error, null);
  });
  test(`ignores scan failure before error recovery after ${name}`, async () => {
    const h = harness();
    h.render();
    supersede(h);
    h.requests[0].reject(new Error('Forbidden'));
    await tick();
    assert.equal(h.recoveries.length, 0);
    assert.equal(h.state.error, null);
  });
  test(`ignores asynchronous error recovery superseded by ${name}`, async () => {
    const h = harness();
    h.render();
    h.requests[0].reject(new Error('Forbidden'));
    await tick();
    assert.equal(h.recoveries.length, 1);
    supersede(h);
    h.recoveries[0].resolve({ message: 'Stale failure', requiresAuthentication: true });
    await tick();
    assert.equal(h.state.error, null);
  });
}

test('organization change retains the handoff but isolates old completion and loading', async () => {
  const h = harness();
  h.render();
  const selection = h.context.chooseOrganization(2);
  h.closeScanners();
  assert.equal(h.state.labels.length, 0);
  assert.equal(h.state.phoneScanner, false);
  assert.equal(h.state.tabletScanner, false);
  assert.equal(h.context.route.scanBoxId, 42);
  assert.deepEqual(h.navigations, [{ mode: 'replace', path: '/?scan_box=42' }],
    'Organization replacement resets history ownership without consuming the handoff');
  h.render();
  assert.equal(h.requests.filter((request) => request.method === 'POST').length, 1);
  for (const request of h.requests.filter((request) => request.method === 'GET')) {
    request.resolve(request.url === '/api/dashboard/' ? { recent_accesses: [] } : { results: [] });
  }
  await selection;
  h.render();
  assert.equal(h.requests.at(-1).url, '/api/boxes/42/scan/');
  assert.equal(h.requests.at(-1).organizationId, 2);
  h.requests[0].resolve({ global_code: 'OLD-42' });
  await tick();
  assert.equal(h.state.boxLoading, true);
  assert.deepEqual(h.navigations, [{ mode: 'replace', path: '/?scan_box=42' }],
    'Old scan completion must not navigate after the organization history reset');
  h.requests.at(-1).resolve({ global_code: 'CURRENT-42' });
  await tick();
  assert.equal(h.context.route.boxCode, 'CURRENT-42');
  assert.equal(h.state.boxLoading, false);
});

test('current scan errors remain visible and do not trigger another POST', async () => {
  for (const requiresAuthentication of [false, true]) {
    const h = harness();
    h.render();
    h.requests[0].reject(new Error('Scan failed'));
    await tick();
    const error = { message: 'Scan failed', requiresAuthentication };
    h.recoveries[0].resolve(error);
    await tick();
    assert.equal(h.state.error, error);
    assert.equal(h.state.boxLoading, false);
    assert.equal(h.navigations.length, 0);
    h.render();
    assert.equal(h.requests.length, 1);
  }
});

test('camera scans still open boxes without POSTing a SCAN', async () => {
  const h = harness('/');
  h.context.data.boxes = [{ id: 42, global_code: 'AUR-42' }];
  h.context.openScannedBox(42);
  assert.equal(h.state.tabletScanner, false);
  assert.equal(h.context.route.boxCode, 'AUR-42');
  assert.equal(h.requests.length, 0);
  h.context.openScannedBox(99);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].method, 'GET');
  assert.equal(h.requests[0].url, '/api/boxes/99/');
  h.requests[0].resolve({ id: 99, global_code: 'AUR-99' });
  await tick();
  assert.equal(h.context.route.boxCode, 'AUR-99');
});
