import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { ast, createHistoryFixture, deferred, functionNode, installAppRouting, tick } from './app-operation-test-harness.mjs';

// The complete Box list is no longer part of the bootstrap. These tests run the
// real App functions and effects with controlled request lifetimes, without a DOM.

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
function effectCall(marker) {
  return findNode((node) => ts.isCallExpression(node)
    && node.expression.getText(ast) === 'useEffect'
    && node.arguments[0]?.getText(ast).includes(marker));
}
function box(id, extra = {}) {
  return { id, global_code: `AUR-${String(id).padStart(3, '0')}`, local_code: `L${id}`, ...extra };
}

function harness({ path = '/', organizationId = 1 } = {}) {
  const browser = createHistoryFixture(path);
  const organizations = [1, 2, 3].map((id) => ({ id, name: `Organization ${id}` }));
  const state = {
    data: {
      boxes: [], boxDetails: {}, zones: [], dashboard: null, overview: null, exportOptions: null,
      profile: {
        id: 42, email: 'lab@example.test', interface_language: 'fr',
        organizations, active_organization: organizations[0],
        memberships: organizations.map((organization) => ({ organization, role: 'admin' })),
      },
    },
    organizationId, recent: [], loading: false, error: null, contextId: null,
    collection: { status: 'idle', loadedAt: null }, resolved: undefined,
  };
  const requests = [];
  const errors = [];
  const context = {
    URL,
    __API_PROXY_ORIGIN__: null,
    ApiResourceCancelledError: class ApiResourceCancelledError extends Error {},
    ApiError: class ApiError extends Error {},
    data: state.data,
    activeOrganizationId: organizationId,
    needsOrganizationChoice: false,
    isBoxRoute: false, isZoneRoute: false, isLoginRoute: false, isLoading: false,
    activeTab: 'pilotage',
    route: { tab: 'pilotage', boxCode: null, boxId: null },
    BOX_LIST_LIMIT: 100,
    organizationRequestGenerationRef: { current: 0 },
    openBoxRequestGenerationRef: { current: 0 },
    navigationGenerationRef: { current: 0 },
    boxCollectionRef: { current: { status: 'idle', loadedAt: null } },
    window: { location: browser.location, history: browser.history },
    setIsOrganizationMenuOpen() {}, setNeedsOrganizationChoice() {}, setIsCreateBoxOpen() {},
    setStoredInterfaceLanguage() {}, setIsLoginRoute() {}, setIsTabletScannerOpen() {}, setPasswordReset() {},
    setSearch() {}, setQrLabelSelection() {}, setMeasurementPrefill() {}, setExportOptionsRequested() {},
    setRoute() {}, setIsBoxLoading() {},
    setActiveOrganizationId(value) { state.organizationId = value; },
    setActiveOrganizationContext(value) { state.contextId = value; },
    setIsLoading(value) { state.loading = value; context.isLoading = value; },
    setError(value) { state.error = value; },
    setRecentBoxIds(value) { state.recent = value; },
    setBoxCollection(value) { state.collection = value; },
    setResolvedBoxCode(value) { state.resolved = value; },
    setData(value) { state.data = typeof value === 'function' ? value(state.data) : value; },
    getSelectableOrganizations(profile) { return profile.organizations; },
    getStoredActiveOrganizationId() { return 1; },
    resolveActiveOrganizationId(_profile, id) { return id; },
    shouldRedirectToLogin() { return false; },
    updateNavigationOrganization() {},
    mergeBoxDetail(current, detail) {
      return { ...current, boxes: [...current.boxes.filter((item) => item.id !== detail.id), detail], boxDetails: { ...current.boxDetails, [detail.id]: detail } };
    },
    apiGet(url) {
      const request = { ...deferred(), url, organizationId: state.contextId ?? context.activeOrganizationId };
      requests.push(request);
      return request.promise;
    },
    getApplicationError(error) {
      const request = { ...deferred(), error };
      errors.push(request);
      return request.promise;
    },
  };
  vm.createContext(context);
  function evaluate(code) {
    const { outputText } = ts.transpileModule(code, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None },
    });
    return vm.runInContext(outputText, context);
  }
  installAppRouting(context, evaluate, organizationId);
  context.boxCollectionRef = { current: state.collection };
  for (const name of ['getOperationRequests', 'fetchAllPages', 'getOrganizationById', 'setProfileActiveOrganization',
    'fetchScopedData', 'chooseOrganization', 'openBox']) {
    evaluate(functionNode(name).getText(ast));
  }
  // Same bookkeeping as the component: the ref mirrors the rendered state.
  context.setBoxCollection = (value) => { state.collection = value; };
  function render() {
    context.data = state.data;
    context.activeOrganizationId = state.organizationId;
    context.isLoading = state.loading;
  }
  function run(call) {
    return evaluate(`(${call.arguments[0].getText(ast)})()`);
  }
  return { state, requests, errors, context, browser, evaluate, render, run, organizations };
}

const pages = (items, next = null) => ({ count: items.length, next, previous: null, results: items });

// --- 1. Bootstrap does not wait for the Box list -------------------------------------------

function bootstrapEffect() {
  const node = functionNode('loadData');
  const statements = node.parent.statements;
  const prelude = statements.slice(0, statements.indexOf(node)).filter(ts.isVariableStatement)
    .map((item) => item.getText(ast)).join('\n');
  return `(() => { ${prelude}\n${node.getText(ast)}\nreturn loadData; })()`;
}

test('bootstrap loads profile, zones and dashboard only, and finishes without any Box page', async () => {
  const h = harness();
  const finished = h.evaluate(bootstrapEffect())();
  assert.equal(h.requests[0].url, '/api/profile/');
  h.requests[0].resolve(h.state.data.profile);
  await tick();
  const urls = h.requests.map((request) => request.url);
  assert.deepEqual(urls.slice(1).sort(), ['/api/dashboard/', '/api/thermal-zones/?limit=80']);
  assert.ok(urls.every((url) => !url.startsWith('/api/boxes/')), `no Box request at bootstrap: ${urls}`);

  for (const request of h.requests.slice(1)) {
    request.resolve(request.url === '/api/dashboard/'
      ? { recent_accesses: [{ object_id: 'AUR-004', metadata: { box_id: 4 } }] }
      : pages([{ id: 9 }]));
  }
  await finished;
  assert.equal(h.state.loading, false);
  assert.equal(h.state.data.boxes.length, 0);
  assert.equal(h.state.data.zones[0].id, 9);
  assert.deepEqual(Array.from(h.state.recent), [4]);
  assert.equal(h.state.collection.status, 'idle');
  assert.equal(h.requests.length, 3);
});

test('a Box page stuck in flight can no longer keep the application loading', async () => {
  const h = harness();
  const finished = h.evaluate(bootstrapEffect())();
  h.requests[0].resolve(h.state.data.profile);
  await tick();
  // Even if /api/boxes/ would never answer, nothing at bootstrap is waiting on it.
  for (const request of h.requests.slice(1)) {
    request.resolve(request.url === '/api/dashboard/' ? { recent_accesses: [] } : pages([]));
  }
  await finished;
  assert.equal(h.state.loading, false);
});

// --- 2. Routes that do not need the list ---------------------------------------------------

test('only list-dependent routes and interactions ask for the complete Box list', () => {
  const h = harness();
  const need = (override = {}) => h.context.needsFullBoxCollection({
    activeTab: 'pilotage', isBoxRoute: false, hasSearch: false, isAdminAvailable: true, ...override,
  });
  for (const tab of ['overview', 'exports', 'profile']) assert.equal(need({ activeTab: tab }), false, tab);
  assert.equal(need(), false, 'Pilotage home and recent boxes');
  assert.equal(need({ isBoxRoute: true, hasSearch: true }), false, 'a direct Box sheet');
  assert.equal(need({ hasSearch: true }), true, 'a Pilotage search');
  assert.equal(need({ activeTab: 'zones' }), true, 'zones overview');
  assert.equal(need({ activeTab: 'zones', zoneId: 3 }), true, 'zone detail');
  assert.equal(need({ activeTab: 'zones', zoneId: 3 }), true, 'zone boxes');
  assert.equal(need({ activeTab: 'zones', zoneId: 3, zoneHistory: true }), false, 'zone movement history');
  assert.equal(need({ activeTab: 'labels' }), true);
  for (const section of ['accounts', 'inventory', 'references', 'environment', 'history', 'organizations']) {
    assert.equal(need({ activeTab: 'admin', adminSection: section }), false, section);
  }
  assert.equal(need({ activeTab: 'admin', adminSection: 'transfers' }), true);
  assert.equal(need({ activeTab: 'admin', adminSection: 'transfers', isAdminAvailable: false }), false,
    'tablet administration never loads anything');
});

// --- 3. Direct Box detail without a preloaded list ------------------------------------------

function resolutionEffect() {
  return effectCall('resolveBoxCode');
}

test('a direct /boxes/CODE route resolves its id through the scoped search, not the full list', async () => {
  const h = harness({ path: '/boxes/AUR-042' });
  Object.assign(h.context, {
    route: { tab: 'pilotage', boxCode: 'AUR-042', boxId: null }, isBoxCodePending: true,
  });
  h.run(resolutionEffect());
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url, '/api/boxes/?limit=100&q=AUR-042');
  h.requests[0].resolve(pages([box(420, { global_code: 'XAUR-042' }), box(42, { global_code: 'AUR-042' })]));
  await tick();
  assert.deepEqual({ ...h.state.resolved }, { organizationId: 1, code: 'AUR-042', boxId: 42 });
  assert.equal(h.requests.length, 1, 'found on the first page: no further page, no list');
  assert.equal(h.state.data.boxes.length, 0);

  // The id is then derived without any known box, and the detail endpoint loads the sheet.
  h.context.useMemo = (callback) => callback();
  Object.assign(h.context, { isBoxCodeResolved: true, resolvedBoxCode: h.state.resolved });
  const selected = findNode((node) => ts.isVariableDeclaration(node) && node.name.getText(ast) === 'selectedBoxId');
  h.context.selectedBoxId = h.evaluate(selected.initializer.getText(ast));
  assert.equal(h.context.selectedBoxId, 42);
  h.context.selectedBoxDetail = null;
  const detailEffect = functionNode('loadBoxDetail').parent.parent.parent;
  h.evaluate(`(${detailEffect.arguments[0].getText(ast)})()`);
  assert.equal(h.requests[1].url, '/api/boxes/42/');
  const detail = { id: 42, global_code: 'AUR-042' };
  h.requests[1].resolve(detail);
  await tick();
  assert.equal(h.state.data.boxDetails[42], detail);
});

test('code lookup follows pages only until the exact code is found and reports an absent code', async () => {
  const h = harness();
  Object.assign(h.context, { route: { tab: 'pilotage', boxCode: 'AUR-1', boxId: null }, isBoxCodePending: true });
  h.run(resolutionEffect());
  h.requests[0].resolve(pages([box(1, { global_code: 'AUR-10' })], 'https://polypbase.test/api/boxes/?limit=100&q=AUR-1&offset=100'));
  await tick();
  assert.equal(h.requests.length, 2, 'the exact code is not on page one: next page is read');
  h.requests[1].resolve(pages([box(2, { global_code: 'AUR-11' })]));
  await tick();
  assert.deepEqual({ ...h.state.resolved }, { organizationId: 1, code: 'AUR-1', boxId: null });
});

test('an unavailable Box is reported as absent, not as a loading list', async () => {
  const h = harness();
  Object.assign(h.context, { route: { tab: 'pilotage', boxCode: 'OTHER-ORG', boxId: null }, isBoxCodePending: true });
  h.run(resolutionEffect());
  h.requests[0].resolve(pages([]));
  await tick();
  assert.equal(h.state.resolved.boxId, null);
  assert.equal(h.state.error, null);
});

test('code resolution waits for bootstrap and ignores an organization change', async () => {
  const h = harness();
  Object.assign(h.context, { route: { tab: 'pilotage', boxCode: 'AUR-042', boxId: null }, isBoxCodePending: true, isLoading: true });
  h.run(resolutionEffect());
  assert.equal(h.requests.length, 0, 'not before the organization context is ready');

  h.context.isLoading = false;
  h.run(resolutionEffect());
  const selection = h.context.chooseOrganization(2);
  h.requests[0].resolve(pages([box(42, { global_code: 'AUR-042' })]));
  await tick();
  assert.equal(h.state.resolved, null, 'chooseOrganization clears resolutions and the stale answer is dropped');
  for (const request of h.requests.filter((item) => item.organizationId === 2)) {
    request.resolve(request.url === '/api/dashboard/' ? { recent_accesses: [] } : pages([]));
  }
  await selection;
});

// --- 4. Organization switching and the on-demand list ---------------------------------------

test('a full list requested for one organization cannot reach the next one', async () => {
  const h = harness();
  h.render();
  const loading = h.context.requestBoxCollection();
  assert.equal(h.state.collection.status, 'loading');
  assert.equal(h.requests.length, 1);

  const selection = h.context.chooseOrganization(2);
  assert.equal(h.state.collection.status, 'idle', 'the switch forgets organization Box data');
  assert.equal(h.state.data.boxes.length, 0);
  h.render();
  h.requests[0].resolve(pages([box(1), box(2)]));
  await loading;
  assert.equal(h.state.data.boxes.length, 0, 'organization A boxes are not written into organization B');
  assert.equal(h.state.collection.status, 'idle');

  for (const request of h.requests.filter((item) => item.organizationId === 2)) {
    request.resolve(request.url === '/api/dashboard/' ? { recent_accesses: [] } : pages([]));
  }
  await selection;
  assert.equal(h.state.data.boxes.length, 0);
});

test('a failure of the previous organization list is ignored after a switch', async () => {
  const h = harness();
  h.render();
  const loading = h.context.requestBoxCollection();
  const selection = h.context.chooseOrganization(2);
  h.render();
  h.requests[0].reject(new Error('old list failed'));
  await loading;
  assert.equal(h.state.collection.status, 'idle', 'not an error state for the new organization');
  assert.equal(h.state.error, null);
  for (const request of h.requests.filter((item) => item.organizationId === 2)) {
    request.resolve(request.url === '/api/dashboard/' ? { recent_accesses: [] } : pages([]));
  }
  await selection;
});

test('the next organization can load its own list after the switch', async () => {
  const h = harness();
  h.render();
  const selection = h.context.chooseOrganization(2);
  for (const request of h.requests.filter((item) => item.organizationId === 2)) {
    request.resolve(request.url === '/api/dashboard/' ? { recent_accesses: [] } : pages([]));
  }
  await selection;
  h.render();
  const loading = h.context.requestBoxCollection();
  const request = h.requests.at(-1);
  assert.equal(request.organizationId, 2);
  request.resolve(pages([box(7)]));
  await loading;
  assert.deepEqual(Array.from(h.state.data.boxes.map((item) => item.id)), [7]);
  assert.equal(h.state.collection.status, 'ready');
});

// --- 5. A route that needs the list can still load it ----------------------------------------

test('the full list is read page by page, once, and merged into the known boxes', async () => {
  const h = harness();
  h.state.data.boxes = [box(5, { note: 'known from a detail' })];
  h.render();
  const first = h.context.requestBoxCollection();
  const second = h.context.requestBoxCollection();
  assert.equal(h.requests.length, 1, 'concurrent callers share one request chain');
  assert.equal(h.requests[0].url, '/api/boxes/?limit=100');
  assert.equal(h.state.collection.status, 'loading');

  h.requests[0].resolve(pages([box(1), box(2)], 'https://polypbase.test/api/boxes/?limit=100&offset=100'));
  await tick();
  assert.equal(h.requests.length, 2);
  h.requests[1].resolve(pages([box(3), box(5)]));
  await Promise.all([first, second]);
  assert.deepEqual(Array.from(h.state.data.boxes.map((item) => item.id)), [1, 2, 3, 5]);
  assert.equal(h.state.collection.status, 'ready');
  assert.equal(h.requests.length, 2);
  await h.context.requestBoxCollection();
  assert.equal(h.requests.length, 2, 'a fresh list is not reloaded');
});

test('a box changed while the list loads is not overwritten by the older snapshot', async () => {
  const h = harness();
  const original = box(1, { latest_measurement: null });
  h.state.data.boxes = [original];
  h.render();
  const loading = h.context.requestBoxCollection();
  const fresher = box(1, { latest_measurement: { polyp_count: 0 } });
  h.state.data = { ...h.state.data, boxes: [fresher] };
  h.requests[0].resolve(pages([box(1, { latest_measurement: null }), box(2)]));
  await loading;
  const merged = h.state.data.boxes;
  assert.equal(merged.find((item) => item.id === 1), fresher);
  assert.equal(merged.find((item) => item.id === 1).latest_measurement.polyp_count, 0, 'a scientific 0 survives the merge');
  assert.ok(merged.some((item) => item.id === 2));
});

test('an old loaded list is refreshed in the background without hiding its content', async () => {
  const h = harness();
  h.render();
  const loading = h.context.requestBoxCollection();
  h.requests[0].resolve(pages([box(1)]));
  await loading;
  h.state.collection = { status: 'ready', loadedAt: Date.now() - 6 * 60 * 1000 };
  h.context.boxCollectionRef.current = h.state.collection;
  h.render();
  const refresh = h.context.requestBoxCollection();
  assert.equal(h.requests.length, 2);
  assert.equal(h.state.collection.status, 'ready', 'stays usable while it refreshes');
  h.requests[1].resolve(pages([box(1), box(2)]));
  await refresh;
  assert.deepEqual(Array.from(h.state.data.boxes.map((item) => item.id)), [1, 2]);
  assert.ok(Date.now() - h.state.collection.loadedAt < 60_000);
});

// --- 8. A failed list stays local ------------------------------------------------------------

test('a failed full list leaves an error state for its routes only, and can be retried', async () => {
  const h = harness();
  h.render();
  const loading = h.context.requestBoxCollection();
  h.requests[0].reject(new Error('boxes unavailable'));
  await loading;
  assert.equal(h.state.collection.status, 'error');
  assert.equal(h.state.error, null, 'no application-wide error is raised');
  assert.equal(h.state.loading, false);
  assert.equal(h.errors.length, 0);

  await h.context.requestBoxCollection();
  assert.equal(h.requests.length, 1, 'no automatic retry loop');
  const retry = h.context.requestBoxCollection({ retry: true });
  assert.equal(h.requests.length, 2);
  assert.equal(h.state.collection.status, 'loading');
  h.requests[1].resolve(pages([box(1)]));
  await retry;
  assert.equal(h.state.collection.status, 'ready');
});

test('a failed background refresh keeps the list that was already loaded', async () => {
  const h = harness();
  h.render();
  const first = h.context.requestBoxCollection();
  h.requests[0].resolve(pages([box(1)]));
  await first;
  h.state.collection = { status: 'ready', loadedAt: 1 };
  h.context.boxCollectionRef.current = h.state.collection;
  h.render();
  const refresh = h.context.requestBoxCollection();
  h.requests[1].reject(new Error('offline'));
  await refresh;
  assert.equal(h.state.collection.status, 'ready');
  assert.deepEqual(Array.from(h.state.data.boxes.map((item) => item.id)), [1]);
});

test('list-dependent views gate on the collection status and unrelated routes never do', () => {
  const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const element = (view) => {
    const match = source.match(new RegExp(`<${view}\\s[\\s\\S]*?/>`));
    assert.ok(match, view);
    return match[0];
  };
  for (const view of ['ZonesView', 'ZoneDetailPage', 'ZoneBoxesPage', 'LabelsView']) {
    assert.match(element(view), /isLoading=\{isLoading \|\| isBoxCollectionLoading\}/, view);
  }
  for (const view of ['OverviewView', 'ExportsView', 'ProfileView', 'ZoneMovementHistoryPage']) {
    assert.doesNotMatch(element(view), /isBoxCollectionLoading|boxCollection/, view);
  }
});

// --- 6. Lookup and search ----------------------------------------------------------------------

test('scanned or typed codes resolve through the scoped search, case-insensitively, global or local', async () => {
  const h = harness();
  const lookup = h.context.findBoxIdByCode('  aur-007 ');
  assert.equal(h.requests[0].url, '/api/boxes/?limit=100&q=aur-007');
  h.requests[0].resolve(pages([box(70, { global_code: 'AUR-0070' }), box(7, { global_code: 'AUR-007' })]));
  assert.equal(await lookup, 7);

  const byLocal = h.context.findBoxIdByCode('l9');
  h.requests[1].resolve(pages([box(9)]));
  assert.equal(await byLocal, 9);

  const missing = h.context.findBoxIdByCode('NOPE');
  h.requests[2].resolve(pages([box(1)]));
  assert.equal(await missing, null);
  assert.equal(await h.context.findBoxIdByCode('   '), null);
  assert.equal(h.requests.length, 3, 'a blank code never reaches the API');
});

test('the QR parser extracts a code to look up without touching /bac/ ids', () => {
  const h = harness();
  const { getBoxCodeFromQrValue, getBoxIdFromQrValue } = (() => {
    const source = readFileSync(new URL('../src/utils/qrScanner.ts', import.meta.url), 'utf8');
    const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } });
    return h.evaluate(`(() => { const exports = {}; ${outputText}\nreturn exports; })()`);
  })();
  assert.equal(getBoxCodeFromQrValue('https://x.test/bac/42/'), null);
  assert.equal(getBoxCodeFromQrValue('https://x.test/boxes/AUR%20%2F42'), 'AUR /42');
  assert.equal(getBoxCodeFromQrValue('AUR-042'), 'AUR-042');
  assert.equal(getBoxCodeFromQrValue('x'.repeat(200)), null);
  assert.equal(getBoxCodeFromQrValue('   '), null);
  assert.equal(getBoxIdFromQrValue('https://x.test/bac/42/', []), 42, 'printed QR codes still need no list');
  assert.equal(getBoxIdFromQrValue('AUR-042', []), null);
});

function pilotageContext(h, search, boxCollectionStatus) {
  const React = { createElement: (type, props, ...children) => ({ type, props: props ?? {}, children: children.flat() }) };
  let index = 0;
  const resolved = [];
  const selected = [];
  Object.assign(h.context, {
    React, useState: (init) => [index++ === 0 ? 'search' : init, () => {}], userCanCreateBoxes: () => false,
    PHONE_RESULT_LIMIT: 5, PILOTAGE_RESULT_LIMIT: 15, SearchField: 'SearchField', SuggestionList: 'SuggestionList',
    RecentAccessList: 'RecentAccessList', BoxSearchStatus: 'BoxSearchStatus', document: { getElementById() { return null; } },
  });
  const { outputText } = ts.transpileModule(functionNode('PilotageView').getText(ast), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  });
  vm.runInContext(outputText, h.context);
  const tree = h.context.PilotageView({
    isPhoneLayout: true, search, searchResults: [], recentBoxes: [], boxCollectionStatus, boxes: [],
    t: (key) => key, onSearch() {}, onSelectBox: (id) => selected.push(id),
    onResolveBoxCode: (code) => { resolved.push(code); return Promise.resolve(42); },
  });
  const nodes = (item) => (item && typeof item === 'object' ? [item, ...item.children.flatMap(nodes)] : []);
  return { tree, nodes: nodes(tree), resolved, selected };
}

test('search shows a loading or retry state, never partial results, until the list is complete', () => {
  for (const status of ['idle', 'loading', 'error']) {
    const h = harness();
    const view = pilotageContext(h, 'aur', status);
    assert.equal(view.nodes.some((node) => node.type === 'SuggestionList'), false, status);
    assert.equal(view.nodes.find((node) => node.type === 'BoxSearchStatus').props.status, status);
  }
  const ready = pilotageContext(harness(), 'aur', 'ready');
  assert.ok(ready.nodes.some((node) => node.type === 'SuggestionList'));
  assert.equal(ready.nodes.some((node) => node.type === 'BoxSearchStatus'), false);
  const idle = pilotageContext(harness(), '', 'idle');
  assert.equal(idle.nodes.some((node) => node.type === 'BoxSearchStatus' || node.type === 'SuggestionList'), false,
    'an empty search never asks for the list');
});

test('submitting an exact code while the list loads still opens that box', async () => {
  const h = harness();
  const view = pilotageContext(h, 'AUR-042', 'loading');
  const field = view.nodes.find((node) => node.type === 'SearchField');
  field.props.onSubmit();
  await tick();
  assert.deepEqual(view.resolved, ['AUR-042']);
  assert.deepEqual(view.selected, [42]);

  const ready = pilotageContext(harness(), 'AUR-042', 'ready');
  ready.nodes.find((node) => node.type === 'SearchField').props.onSubmit();
  await tick();
  assert.deepEqual(ready.resolved, [], 'a complete list answers locally');
});

test('opening a box needs neither the list nor a previous download', async () => {
  const h = harness();
  h.render();
  h.context.openBox(99);
  assert.equal(h.requests[0].url, '/api/boxes/99/');
  h.requests[0].resolve({ id: 99, global_code: 'AUR-099' });
  await tick();
  assert.equal(h.state.data.boxDetails[99].global_code, 'AUR-099');
  assert.equal(h.requests.length, 1);
});

// --- 7. Recent boxes ---------------------------------------------------------------------------

function recentEffect(h, overrides = {}) {
  Object.assign(h.context, {
    activeTab: 'pilotage', isBoxRoute: false, isLoading: false, needsOrganizationChoice: false,
    activeOrganizationId: 1, recentBoxIds: [4, 2, 9, 11], recentBoxLimit: 3, data: h.state.data,
    requestedRecentBoxIdsRef: { current: new Set() },
    ...overrides,
  });
  h.state.data.profile = h.state.data.profile ?? {};
  return () => h.run(effectCall('requestedRecentBoxIdsRef.current'));
}

test('recent boxes fetch only the displayed ones, one by one, never the full list', async () => {
  const h = harness();
  h.state.data.boxes = [box(2)];
  const start = recentEffect(h);
  start();
  assert.deepEqual(h.requests.map((request) => request.url).sort(), ['/api/boxes/4/', '/api/boxes/9/']);
  h.requests.find((request) => request.url === '/api/boxes/4/').resolve({ id: 4, global_code: 'AUR-004' });
  h.requests.find((request) => request.url === '/api/boxes/9/').reject(new Error('gone'));
  await tick();
  assert.deepEqual(Array.from(h.state.data.boxes.map((item) => item.id).sort((a, b) => a - b)), [2, 4]);
  assert.ok(h.requests.every((request) => !request.url.startsWith('/api/boxes/?')));
  assert.equal(h.context.requestedRecentBoxIdsRef.current.has(4), true);
  assert.equal(h.context.requestedRecentBoxIdsRef.current.has(9), false, 'a failed one is retried on a later visit');
});

test('recent boxes are fetched only on the Pilotage home, once bootstrap is done', () => {
  for (const override of [{ activeTab: 'zones' }, { isBoxRoute: true }, { isLoading: true }, { needsOrganizationChoice: true },
    { activeOrganizationId: null }, { recentBoxIds: [] }]) {
    const h = harness();
    recentEffect(h, override)();
    assert.equal(h.requests.length, 0, JSON.stringify(override));
  }
  const h = harness();
  h.state.data.boxes = [box(4), box(2), box(9)];
  recentEffect(h)();
  assert.equal(h.requests.length, 0, 'known boxes are not fetched again');
});

test('a recent box answered after an organization switch is dropped', async () => {
  const h = harness();
  recentEffect(h, { recentBoxIds: [4] })();
  h.context.organizationRequestGenerationRef.current += 1;
  h.requests[0].resolve({ id: 4, global_code: 'AUR-004' });
  await tick();
  assert.equal(h.state.data.boxes.length, 0);
});
