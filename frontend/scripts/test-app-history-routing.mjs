import assert from 'node:assert/strict';

import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { ast, functionNode, source, deferred, tick, installAppRouting } from './app-operation-test-harness.mjs';

function harness(initialPath = '/zones', organization = 1) {
  const entries = [{ path: 'https://external.invalid', state: null }, { path: initialPath, state: null }];
  let index = 1, queued = null, backCalls = 0, pushCalls = 0;
  const location = {};
  function updateLocation() {
    const url = new URL(entries[index].path, 'https://polypbase.test');
    Object.assign(location, { pathname: url.pathname, search: url.search, hash: url.hash });
  }
  updateLocation();
  const history = {
    get state() { return structuredClone(entries[index].state); },
    pushState(state, unused, path) {
      pushCalls++;
      entries.splice(index + 1);
      entries.push({ path, state: structuredClone(state) });
      index++;
      updateLocation();
    },
    replaceState(state, unused, path) {
      entries[index] = { path, state: structuredClone(state) };
      updateLocation();
    },
    back() { backCalls++; queued = index - 1; },
  };
  const requests = [];
  const context = {
    URL, URLSearchParams, exports: {},
    window: { location, history },
    ADMIN_SECTION_PATHS: {
      accounts: '/administration/team', inventory: '/administration/box-inventory',
      references: '/administration/reference-data', environment: '/administration/laboratory',
      transfers: '/administration/transfers', history: '/administration/history',
      organizations: '/administration/institutions',
    },
    inAppHistoryRef: { current: null }, navigationOrganizationRef: { current: organization },
    navigationPolicyRef: { current: { isDesktopApp: true, canUseAdmin: true } },
    navigationGenerationRef: { current: 0 }, organizationRequestGenerationRef: { current: 0 },
    openBoxRequestGenerationRef: { current: 0 }, activeOrganizationId: organization,
    activeTab: 'zones', isBoxRoute: false, isZoneRoute: false, needsOrganizationChoice: false,
    data: { boxes: [], boxDetails: {}, profile: { id: 1 } },
    setRoute(value) { context.route = value; context.routeWrites++; }, routeWrites: 0,
    setSearch() {}, setIsBoxLoading() {}, setIsLoading() {}, setNeedsOrganizationChoice() {},
    setIsOrganizationMenuOpen() {}, setIsCreateBoxOpen() {}, setRecentBoxIds() {}, setQrLabelSelection() {},
    setMeasurementPrefill() {}, setExportOptionsRequested() {}, setRefreshRecovery() {},
    setIsTabletScannerOpen() {}, setPasswordReset(value) { context.passwordReset = value; },
    setIsLoginRoute(value) { context.isLoginRoute = value; }, setError(value) { context.error = value; },
    // Model React's async state commit: the navigation ref must update first.
    setActiveOrganizationId(value) { context.queuedOrganization = value; },
    setData(value) { context.data = typeof value === 'function' ? value(context.data) : value; },
    setProfileActiveOrganization(profile, id) { return { ...profile, organizationId: id }; },
    buildRecentBoxIds() { return []; },
    fetchScopedData() { return Promise.resolve(context.data); },
    apiPost() { return Promise.resolve(); },
    apiGet(url) { const request = { ...deferred(), url }; requests.push(request); return request.promise; },
    mergeBoxDetail(data, detail) { return { ...data, boxDetails: { ...data.boxDetails, [detail.id]: detail } }; },
    getApplicationError(error) { return Promise.resolve(error); },
  };
  vm.createContext(context);
  function evaluate(code) {
    const { outputText } = ts.transpileModule(code, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    });
    return vm.runInContext(outputText, context);
  }
  installAppRouting(context, evaluate, organization);
  for (const name of ['openBox', 'chooseOrganization', 'handleAuthenticated', 'logoutCurrentUser']) {
    evaluate(functionNode(name).getText(ast));
  }
  context.route = context.getCurrentRoute();
  context.getInAppHistory();
  return {
    context, requests, evaluate, entries,
    get path() { return entries[index].path; }, get backCalls() { return backCalls; }, get pushCalls() { return pushCalls; },
    flush() { assert.notEqual(queued, null); index = queued; queued = null; updateLocation(); context.syncRoute(); },
    travel(delta) { index += delta; updateLocation(); context.syncRoute(); },
    guard() {
      const app = functionNode('App');
      const effect = app.body.statements.find(node => ts.isExpressionStatement(node)
        && ts.isCallExpression(node.expression) && node.expression.expression.getText(ast) === 'useLayoutEffect'
        && node.expression.arguments[0].getText(ast).includes("activeTab === 'admin'"));
      assert.ok(effect);
      evaluate(`(${effect.expression.arguments[0].getText(ast)})()`);
    },
  };
}

const boxRoute = (code, boxId = null) => ({ tab: 'pilotage', boxCode: code, boxId });
const decodedDotSegmentPaths = [
  '/boxes/A%2F..%2FB', '/boxes/A%2F.%2FB', '/boxes/%2e%2e%2Flabels',
  '/boxes/%2E%2flabels', '/boxes/A%2f%2E%2e%2FB', '/boxes/A%2F%2e%2fB',
  '/boxes/A%2F.%2e%2fB', '/boxes/A%2F..', '/boxes/A%2F%2E',
  '/boxes/A%2f..%2fB/?source=qr#reading', '/boxes/.%2e%2Flabels',
];

test('App classifier positively recognizes parser routes and respects access policy', () => {
  const h = harness();
  const classify = h.context.isRecognizedAppPath;
  for (const path of ['/', '/zones', '/overview', '/labels', '/profile', '/exports', '/boxes/A%20B/',
    '/boxes/A%2FB', '/boxes/AUR%20%2F42', '/boxes/AUR%20%2f42/?source=qr#reading',
    '/zones/3/', '/zones/3/boxes', '/zones/3/history/?direction=departure#reading', '/bac/42/',
    '/?scan_box=42', '/administration/', ...Object.values(h.context.ADMIN_SECTION_PATHS)]) {
    assert.equal(classify(path, true, true), true, path);
  }
  for (const path of ['/unknown', '/zones/abc', '/zones/0', '/zones/9007199254740992', '/boxes/',
    '/boxes/A/extra', '/zones/3/extra', '/administration/unknown', '/login', '/reset-password/user/token',
    '//external.invalid', '/\\external', 'https://external.invalid', '/zones/../labels', '/boxes/%zz',
    '/boxes/%5Cexternal', '/%2fexternal', '/boxes%2FAUR', '/boxes/A%2FB/extra', '/boxes/A%2FB//',
    '/zones/3%2Fhistory', '/administration%2Fteam', '/unknown/A%2FB', '/boxes/A/../B',
    '/boxes/%2e%2e', '/boxes/%2e%2e/labels', '/boxes/A%2F42%zz', '/boxes/A%2F42%5C',
    '/boxes/A%2F42%00', '/boxes/A%2F42%0a', '/boxes/A%2F42%7f', ...decodedDotSegmentPaths]) {
    assert.equal(classify(path, true, true), false, path);
  }
  assert.equal(classify('/administration/team', false, true), false);
  assert.equal(classify('/administration/team', true, false), false);
  assert.equal(classify('/exports', false, true), false);
});

test('actual replaceRoute rejects decoded dot segments without changing the route or trusted history', () => {
  const h = harness('/labels');
  h.context.navigateTo(boxRoute('SAFE'), '/boxes/SAFE');
  const route = h.context.route;
  const writes = h.context.routeWrites;
  for (const path of decodedDotSegmentPaths) {
    assert.throws(() => h.context.replaceRoute(boxRoute('UNSAFE'), path), /Unsafe in-app path/, path);
    assert.equal(h.path, '/boxes/SAFE');
    assert.equal(h.context.route, route);
    assert.equal(h.context.routeWrites, writes);
    assert.equal(h.pushCalls, 1);
  }
  h.context.closeBoxPage();
  h.flush();
  assert.equal(h.path, '/labels');
});

for (const code of ['AUR-42', 'AUR 42', 'AUR /42', 'AUR/ZONE/42', 'AUR%2F42',
  'AUR/..B/42', 'AUR/.../42', 'AUR%2F..%2FB']) {
  const path = `/boxes/${encodeURIComponent(code)}`;
  test(`actual Box navigation keeps the opaque identifier and contextual Back: ${code}`, async () => {
    const previous = '/zones/3/history?direction=departure';
    const h = harness(previous);
    h.context.openBox(42, code);
    assert.equal(h.path, path);
    assert.equal(h.context.route.boxCode, code);
    assert.equal(h.context.route.boxId, 42);
    h.requests[0].resolve({ id: 42, global_code: code });
    await tick();
    assert.equal(h.path, path);
    assert.equal(h.pushCalls, 1, 'Async enrichment must not add another history entry');
    assert.equal(h.context.routeWrites, 2);
    assert.equal(h.context.getCurrentRoute().boxCode, code, 'Parser decodes the identifier once, without splitting it');
    h.context.closeBoxPage();
    h.flush();
    assert.equal(h.path, previous);
    h.travel(1);
    assert.equal(h.path, path);
    assert.equal(h.context.route.boxCode, code);
    h.context.closeBoxPage();
    h.flush();
    assert.equal(h.path, previous);
  });
  test(`direct Box entry preserves the identifier without blind Back: ${code}`, () => {
    const h = harness(path);
    assert.equal(h.context.route.boxCode, code);
    h.context.closeBoxPage();
    assert.equal(h.path, '/');
    assert.equal(h.backCalls, 0);
    assert.equal(h.pushCalls, 0);
  });
}

test('encoded slash Box identifier survives login without inheriting auth provenance', () => {
  const path = '/boxes/AUR%20%2F42';
  const h = harness(`/login?next=${encodeURIComponent(path)}`);
  h.context.handleAuthenticated();
  assert.equal(h.path, path);
  assert.equal(h.context.route.boxCode, 'AUR /42');
  h.context.updateNavigationOrganization(2);
  h.context.closeBoxPage();
  assert.equal(h.path, '/');
  assert.equal(h.backCalls, 0);
});

test('invalid encoded route navigation resets rather than creating a trusted predecessor', () => {
  for (const path of ['/boxes%2FAUR', '/boxes/A%2FB/extra', '/zones/3%2Fhistory',
    '/boxes/A%2F42%5C', '/boxes/A%2F42%00', '/boxes/%2e%2e/labels', '//external.invalid',
    ...decodedDotSegmentPaths]) {
    const h = harness('/labels');
    h.context.navigateTo(boxRoute('SAFE'), '/boxes/SAFE');
    h.context.navigateTo(boxRoute('UNSAFE'), path);
    assert.equal(h.path, '/', path);
    assert.equal(h.context.route.boxCode, null);
    assert.equal(h.pushCalls, 1, 'Invalid destination must not push an entry');
    h.context.closeBoxPage();
    assert.equal(h.path, '/');
    assert.equal(h.backCalls, 0, 'Invalid destination must break previous provenance');
  }
});

test('actual async openBox enrichment updates route without a duplicate history entry', async () => {
  const h = harness('/zones/3/history?direction=departure');
  h.context.openBox(42, 'BOX-42');
  assert.equal(h.context.route.boxId, 42);
  h.requests[0].resolve({ id: 42, global_code: 'BOX-42' });
  await tick();
  assert.equal(h.context.routeWrites, 2);
  assert.equal(h.pushCalls, 1);
  h.context.closeBoxPage();
  h.flush();
  assert.equal(h.path, '/zones/3/history?direction=departure');
});

test('same-path noop explicitly updates enriched route, pending push/replace do not', () => {
  const h = harness('/labels');
  h.context.navigateTo(boxRoute('A'), '/boxes/A');
  h.context.navigateTo(boxRoute('A', 42), '/boxes/A');
  assert.equal(h.context.route.boxId, 42);
  const route = h.context.route, writes = h.context.routeWrites;
  h.context.closeBoxPage();
  h.context.navigateTo(boxRoute('B'), '/boxes/B');
  h.context.replaceRoute(boxRoute('C'), '/boxes/C');
  h.context.closeBoxPage();
  assert.equal(h.context.route, route);
  assert.equal(h.context.routeWrites, writes);
  assert.equal(h.backCalls, 1);
  h.flush();
  assert.equal(h.path, '/labels');
});

test('Back invalidates an in-flight box request before popstate arrives', async () => {
  const h = harness('/labels');
  h.context.openBox(42, 'BOX-42');
  h.context.closeBoxPage();
  h.requests[0].resolve({ id: 42, global_code: 'BOX-42' });
  await tick();
  assert.equal(h.context.routeWrites, 1);
  h.flush();
  assert.equal(h.path, '/labels');
});

for (const [path, close, fallback] of [
  ['/boxes/A', 'closeBoxPage', '/'], ['/zones/3', 'closeZonePage', '/zones'],
  ['/zones/3/history?direction=departure', 'closeZoneSubview', '/zones/3'],
  ['/zones/3/boxes', 'closeZoneSubview', '/zones/3'],
]) {
  test(`direct ${path} Back replaces with ${fallback}`, () => {
    const h = harness(path);
    h.context[close](3);
    assert.equal(h.path, fallback);
    assert.equal(h.backCalls, 0);
    assert.equal(h.pushCalls, 0);
  });
}

test('direction changes replace the same history subview and preserve meaningful previous route', () => {
  const h = harness('/labels');
  h.context.openZoneHistory(3, 'arrival');
  h.context.openZoneHistory(3, 'departure');
  h.context.openZoneHistory(3, 'arrival');
  assert.equal(h.pushCalls, 1);
  h.context.closeZoneSubview(3);
  h.flush();
  assert.equal(h.path, '/labels');
  h.context.openZoneHistory(4, 'departure');
  h.context.openZoneHistory(5, 'arrival');
  assert.equal(h.pushCalls, 3);
});

test('popstate reconciles back/forward and subsequent pushes prune the forward branch', () => {
  const h = harness('/zones');
  h.context.openZoneBoxes(3);
  h.context.navigateTo(boxRoute('A'), '/boxes/A');
  h.travel(-1);
  assert.equal(h.context.route.zoneBoxes, true);
  h.travel(1);
  assert.equal(h.context.route.boxCode, 'A');
  h.travel(-1);
  h.context.openZone(4);
  assert.equal(h.entries.length, 4);
  h.context.closeZonePage();
  h.flush();
  assert.equal(h.path, '/zones/3/boxes');
});

test('organization selection resets with the new org before React commits state', async () => {
  const h = harness('/zones');
  h.context.openZone(3);
  Object.assign(h.context, { isZoneRoute: true, activeTab: 'zones' });
  await h.context.chooseOrganization(2);
  assert.equal(h.context.activeOrganizationId, 1);
  assert.equal(h.context.navigationOrganizationRef.current, 2);
  assert.equal(h.path, '/zones');
  h.context.openZone(4);
  h.context.closeZonePage();
  h.flush();
  assert.equal(h.path, '/zones');
  h.context.closeZonePage();
  assert.equal(h.backCalls, 1);
  // Manually visiting the old browser entry cannot revive org 1's ledger.
  h.travel(-1);
  h.context.closeZonePage();
  assert.equal(h.backCalls, 1);
});

test('bootstrap org changes break chains, same-org resolution preserves them', () => {
  const h = harness('/labels');
  h.context.navigateTo(boxRoute('A'), '/boxes/A');
  h.context.updateNavigationOrganization(1);
  h.context.closeBoxPage();
  h.flush();
  assert.equal(h.path, '/labels');
  h.context.navigateTo(boxRoute('B'), '/boxes/B');
  h.context.updateNavigationOrganization(null);
  h.context.closeBoxPage();
  assert.equal(h.path, '/');
  assert.equal(h.backCalls, 1);
});

test('authentication return validates destinations and resets prior provenance', () => {
  for (const next of ['/boxes/A', '/bac/42/', '/?scan_box=42', '/administration/team', '/unknown', '//external.invalid', '/\\external']) {
    const h = harness(`/login?next=${encodeURIComponent(next)}`);
    h.context.handleAuthenticated();
    const expected = h.context.isRecognizedAppPath(next, true, true) ? next : '/';
    assert.equal(h.path, expected);
    assert.equal(h.context.navigationOrganizationRef.current, null);
    h.context.updateNavigationOrganization(2);
    h.context.closeBoxPage();
    assert.equal(h.backCalls, 0);
  }
});

test('logout resets pending traversal and never retains authenticated provenance', async () => {
  const h = harness('/labels');
  h.context.navigateTo(boxRoute('A'), '/boxes/A');
  h.context.closeBoxPage();
  await h.context.logoutCurrentUser();
  assert.equal(h.path, '/login');
  assert.equal(h.context.navigationOrganizationRef.current, null);
  h.flush();
  h.context.closeBoxPage();
  assert.equal(h.path, '/');
  assert.equal(h.backCalls, 1);
});

test('tablet and unauthorized Administration guards reset rather than push a loop', () => {
  for (const isDesktopApp of [false, true]) {
    const h = harness('/labels');
    h.context.navigateTo({ tab: 'admin', boxCode: null, boxId: null }, '/administration/team');
    Object.assign(h.context, { activeTab: 'admin', isDesktopApp, canUseAdmin: false,
      isLoading: false, availableTabs: ['pilotage', 'zones', 'labels'] });
    h.context.navigationPolicyRef.current = { isDesktopApp, canUseAdmin: false };
    h.guard();
    assert.equal(h.path, '/');
    h.context.closeBoxPage();
    assert.equal(h.backCalls, 0);
    assert.equal(h.pushCalls, 1);
  }
});

test('all browser history writes use the helper and zone subview callbacks use contextual Back', () => {
  assert.doesNotMatch(source, /window\.history\.(pushState|replaceState|back)\s*\(/);
  assert.equal((source.match(/onBack=\{\(\) => closeZoneSubview\(route\.zoneId as number\)\}/g) ?? []).length, 2);
  const loadData = functionNode('loadData').getText(ast);
  assert.match(loadData, /resetNavigation\(loginPath, null\)/);
  assert.match(loadData, /updateNavigationOrganization\(resolvedOrganizationId\)/);
  assert.match(source, /resetNavigation\('\/login', null\);\s*setPasswordReset\(null\)/);
});
