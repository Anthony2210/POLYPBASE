import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { appHarness, ast as appAst, functionNode, deferred as operationDeferred, tick as operationTick,
  createHistoryFixture, installAppRouting } from './app-operation-test-harness.mjs';

for (const gap of ['before confirmation', 'after mutation success']) {
  test(`create-box caller cannot navigate after organization replacement ${gap}`, async () => {
    const h = appHarness();
    let submit;
    function visit(node) {
      if (ts.isFunctionDeclaration(node) && node.name?.text === 'handleSubmit') submit = node;
      ts.forEachChild(node, visit);
    }
    visit(functionNode('CreateBoxPanel'));
    assert.ok(submit);
    h.evaluate(submit.getText(appAst));
    const confirmation = operationDeferred();
    const mutation = operationDeferred();
    const navigations = [], errors = [], mutations = [];
    Object.assign(h.context, {
      isSaving: false, canSubmit: true, organizationId: 1, strainId: 2, zoneId: 3,
      globalCode: 'BOX-1', boxNumber: '1', enteredOn: '2026-09-16', notes: '',
      selectedStrain: null, selectedZone: null, activeOrganization: { name: 'Organization A' },
      presentation: 'inline', isConfirmationOpenRef: { current: false }, operationLifetimeRef: { current: true },
      isOperationCurrent: () => h.context.organizationRequestGenerationRef.current === 0,
      boxCodeMatchesBoxNumber: () => true, t: key => key, confirmAction: () => confirmation.promise,
      setIsSaving() {}, setError(value) { errors.push(value); }, setGlobalCode() {}, setBoxNumber() {}, setNotes() {},
      onCreateBox(payload) { mutations.push(payload); return mutation.promise; },
      onSelectBox(id) { navigations.push(id); },
    });
    const pending = h.context.handleSubmit({ preventDefault() {} });
    if (gap === 'before confirmation') {
      h.switchOrganization();
      confirmation.resolve(true);
    } else {
      confirmation.resolve(true);
      await operationTick();
      mutation.resolve({ id: 7 });
      h.switchOrganization();
    }
    await pending;
    assert.deepEqual(navigations, []);
    assert.deepEqual(errors.filter(Boolean), []);
    assert.equal(mutations.length, gap === 'before confirmation' ? 0 : 1);
  });
}

const operationCases = [
  ['createMeasurement', [7, {}]], ['updateMeasurement', [7, 8, {}]], ['createBox', [{}]],
  ['createSubculture', [7, {}]], ['moveBox', [7, {}]], ['deactivateBox', [7, {}]],
  ['reactivateBox', [7, {}]], ['qualifyBox', [7, {}]], ['assignBoxInitialLocation', [7, {}]],
  ['qualifyBoxesBatch', [{}]], ['createThermalZone', [{}]], ['updateThermalZone', [7, {}]],
  ['recordManualTemperature', [7, {}]], ['recordManualSalinity', [7, {}]],
  ['updateManualSalinity', [7, 8, {}]], ['createProbe', [{}]], ['createOrganization', [{}]],
  ['updateOrganization', [7, {}]], ['deleteOrganization', [7]], ['createBoxTransfer', [{}]],
  ['refreshBoxAfterMeasurement', [7]], ['refreshZoneSalinityCapability', [7]], ['loadLineageGraph', [7]],
];
for (const [name, args] of operationCases) {
  test(`${name}: generation is checked again at the outer await continuation`, async () => {
    const h = appHarness();
    const previous = h.state.data;
    const pending = h.context[name](...args);
    const rejected = assert.rejects(pending, h.context.ApiResourceCancelledError);
    h.requests[0].resolve({ id: 7 });
    await Promise.resolve();
    h.switchOrganization();
    await rejected;
    assert.equal(h.state.data, previous);
    assert.equal(h.requests.length, 1);
  });
  for (const outcome of ['success', 'failure']) {
    test(`${name}: stale ${outcome} cancels without state writes, refresh, or caller navigation`, async () => {
      const h = appHarness();
      const previous = h.state.data;
      let navigated = false;
      const pending = h.context[name](...args).then(() => { navigated = true; });
      const rejected = assert.rejects(pending, error => error instanceof h.context.ApiResourceCancelledError && error.status == null);
      h.switchOrganization();
      if (outcome === 'success') h.requests[0].resolve({ id: 7 });
      else h.requests[0].reject({ status: 403 });
      await rejected;
      assert.equal(h.requests.length, 1);
      assert.equal(h.state.data, previous);
      assert.equal(navigated, false);
      assert.equal(h.state.recovery, null);
    });
  }
}
for (const [name, args] of [['createThermalZone', [{}]], ['updateThermalZone', [7, {}]], ['createProbe', [{}]],
  ['assignBoxInitialLocation', [7, {}]], ['createOrganization', [{}]], ['updateOrganization', [7, {}]], ['deleteOrganization', [7]]]) {
  test(`${name}: replacement during follow-up refresh prevents writes and any later request`, async () => {
    const h = appHarness();
    const pending = h.context[name](...args);
    const rejected = assert.rejects(pending, h.context.ApiResourceCancelledError);
    h.requests[0].resolve({ id: 7 });
    for (let index = 0; index < 12; index += 1) await Promise.resolve();
    assert.equal(h.requests.length, 2);
    h.switchOrganization();
    const replacement = { boxes: [], boxDetails: {}, zones: [] };
    h.state.data = replacement;
    h.requests[1].resolve({ results: [{ id: 7 }] });
    await rejected;
    assert.equal(h.state.data, replacement);
    assert.equal(h.requests.length, 2);
  });
}

test('queued mutation state update is rechecked when React applies it', async () => {
  const h = appHarness();
  h.queueUpdates();
  const pending = h.context.createBox({});
  h.requests[0].resolve({ id: 7 });
  await pending;
  h.switchOrganization();
  const replacement = { boxes: [{ id: 9 }], boxDetails: {} };
  assert.equal(h.queuedUpdates[0](replacement), replacement);
});
test('callbacks belonging to a replaced organization cannot start mutations', async () => {
  const h = appHarness();
  h.switchOrganization();
  await assert.rejects(h.context.createBox({}), h.context.ApiResourceCancelledError);
  assert.equal(h.requests.length, 0);
});

// Run the actual App request functions with controlled promises, without a DOM.
const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function findFunction(name) {
  let found;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(found, `Missing App function: ${name}`);
  return found;
}
function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}
function harness() {
  const browser = createHistoryFixture();
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
    organizationId: 1, recent: [], qrLabels: [], measurementPrefill: null,
    exportOptionsRequested: false, search: '', loading: false, error: null,
  };
  const requests = [];
  const errors = [];
  const routes = [];
  const context = {
    URL,
    __API_PROXY_ORIGIN__: null,
    ApiResourceCancelledError: class ApiResourceCancelledError extends Error {},
    data: state.data,
    activeOrganizationId: 1,
    needsOrganizationChoice: false,
    isBoxRoute: false,
    isZoneRoute: false,
    activeTab: 'pilotage',
    exportOptionsRequested: false,
    BOX_LIST_LIMIT: 80,
    organizationRequestGenerationRef: { current: 0 },
    openBoxRequestGenerationRef: { current: 0 },
    navigationGenerationRef: { current: 0 },
    window: { location: browser.location, history: browser.history },
    setIsOrganizationMenuOpen() {}, setNeedsOrganizationChoice() {}, setIsCreateBoxOpen() {},
    setStoredInterfaceLanguage() {}, setIsLoginRoute() {},
    setSearch(value) { state.search = value; },
    setQrLabelSelection(value) { state.qrLabels = value; },
    setMeasurementPrefill(value) { state.measurementPrefill = value; },
    setRoute(route) { routes.push(route); },
    setIsTabletScannerOpen() {}, setPasswordReset() {},
    setIsBoxLoading(value) { state.boxLoading = value; },
    setActiveOrganizationId(value) { state.organizationId = value; },
    setActiveOrganizationContext(value) { state.contextId = value; },
    setIsLoading(value) { state.loading = value; },
    setError(value) { state.error = value; },
    setRecentBoxIds(value) { state.recent = value; },
    setData(value) { state.data = typeof value === 'function' ? value(state.data) : value; },
    setExportOptionsRequested(value) { state.exportOptionsRequested = value; },
    getSelectableOrganizations(profile) { return profile.organizations; },
    getStoredActiveOrganizationId() { return 1; },
    resolveActiveOrganizationId(_profile, id) { return id; },
    shouldRedirectToLogin() { return false; },
    ApiError: class ApiError extends Error {},
    mergeBoxDetail(current, detail) { return { ...current, boxDetails: { [detail.id]: detail } }; },
    apiGet(url) {
      const request = { ...deferred(), url, organizationId: state.contextId };
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
  installAppRouting(context, evaluate);
  for (const name of ['getOperationRequests', 'fetchAllPages', 'getOrganizationById', 'setProfileActiveOrganization', 'fetchScopedData', 'chooseOrganization', 'openBox']) {
    evaluate(findFunction(name).getText(ast));
  }
  function beginEffect(name) {
    const node = findFunction(name);
    const statements = node.parent.statements;
    const index = statements.indexOf(node);
    // Keep the effect's real lifetime/generation declarations, not its early route return.
    const prelude = statements.slice(0, index).filter(ts.isVariableStatement).map((item) => item.getText(ast)).join('\n');
    return evaluate(`(() => { ${prelude}\n${node.getText(ast)}\nreturn ${name}; })()`);
  }
  function render() {
    context.data = state.data;
    context.activeOrganizationId = state.organizationId;
    context.exportOptionsRequested = state.exportOptionsRequested;
  }
  return { state, requests, errors, routes, context, browser, beginEffect, render, choose: context.chooseOrganization, openBox: context.openBox };
}
async function tick() {
  for (let index = 0; index < 12; index += 1) await Promise.resolve();
}
function completeSelection(h, organizationId) {
  const requests = h.requests.filter((request) => request.organizationId === organizationId);
  // Zones and dashboard only: the complete Box list is not part of the selection.
  assert.equal(requests.length, 2);
  assert.ok(requests.every((request) => !request.url.startsWith('/api/boxes/')));
  for (const request of requests) {
    request.resolve(request.url === '/api/dashboard/'
      ? { organizationId, recent_accesses: [{ metadata: { box_id: organizationId } }] }
      : { results: [{ id: organizationId }] });
  }
}

test('older organization success cannot overwrite newer data/profile/recent boxes', async () => {
  const h = harness();
  const older = h.choose(2);
  const newer = h.choose(3);
  completeSelection(h, 3);
  await newer;
  const current = h.state.data;
  completeSelection(h, 2);
  await older;
  assert.equal(h.state.data, current);
  assert.equal(current.profile.active_organization.id, 3);
  assert.deepEqual(Array.from(h.state.recent), [3]);
  assert.equal(h.state.contextId, 3);
  assert.equal(h.state.loading, false);
});

test('older completion cannot clear the newer selection loading state', async () => {
  const h = harness();
  const older = h.choose(2);
  const newer = h.choose(3);
  const pendingData = h.state.data;
  completeSelection(h, 2);
  await older;
  assert.equal(h.state.loading, true);
  assert.equal(h.state.data, pendingData);
  completeSelection(h, 3);
  await newer;
  assert.equal(h.state.loading, false);
});

test('older failure is ignored before resolving its application error', async () => {
  const h = harness();
  const older = h.choose(2);
  const newer = h.choose(3);
  h.requests[0].reject(new Error('old failure'));
  await older;
  assert.equal(h.errors.length, 0);
  assert.equal(h.state.error, null);
  assert.equal(h.state.loading, true);
  completeSelection(h, 3);
  await newer;
});

test('an error superseded while authentication recovery is pending is ignored', async () => {
  const h = harness();
  const older = h.choose(2);
  h.requests[0].reject(new Error('old failure'));
  await tick();
  assert.equal(h.errors.length, 1);
  const newer = h.choose(3);
  h.errors[0].resolve({ message: 'old error', requiresAuthentication: true });
  await older;
  assert.equal(h.state.error, null);
  assert.equal(h.state.loading, true);
  completeSelection(h, 3);
  await newer;
});

test('current failure still shows its error and ends loading', async () => {
  const h = harness();
  const current = h.choose(2);
  h.requests[0].reject(new Error('current failure'));
  await tick();
  const error = { message: 'current error' };
  h.errors[0].resolve(error);
  await current;
  assert.equal(h.state.error, error);
  assert.equal(h.state.loading, false);
});

test('bootstrap cannot overwrite an explicit newer organization selection', async () => {
  const h = harness();
  const bootstrap = h.beginEffect('loadData')();
  h.requests[0].resolve(h.state.data.profile);
  await tick();
  const newer = h.choose(3);
  completeSelection(h, 3);
  await newer;
  const current = h.state.data;
  completeSelection(h, 1);
  await bootstrap;
  assert.equal(h.state.data, current);
  assert.equal(h.state.data.profile.active_organization.id, 3);
});

for (const name of ['loadOverview', 'loadBoxDetail', 'loadExportOptions']) {
  test(`${name} ignores success immediately after selection, before effect cleanup`, async () => {
    const h = harness();
    const older = h.beginEffect(name)(12);
    const newer = h.choose(3);
    const pendingData = h.state.data;
    h.requests[0].resolve({ id: 12, results: [{ id: 12 }] });
    await older;
    assert.equal(h.state.data, pendingData);
    assert.equal(h.state.exportOptionsRequested, false);
    completeSelection(h, 3);
    await newer;
  });
  test(`${name} ignores an error superseded during application-error resolution`, async () => {
    const h = harness();
    const older = h.beginEffect(name)(12);
    h.requests[0].reject(new Error('old read'));
    await tick();
    const newer = h.choose(3);
    h.errors[0].resolve({ message: 'old read error' });
    await older;
    assert.equal(h.state.error, null);
    assert.equal(h.state.exportOptionsRequested, false);
    completeSelection(h, 3);
    await newer;
  });
}

for (const outcome of ['success', 'failure']) {
  test(`organization selection invalidates openBox fallback ${outcome} and loading completion`, async () => {
    const h = harness();
    h.openBox(12);
    const newer = h.choose(3);
    const pendingData = h.state.data;
    h.context.setIsBoxLoading(true);
    if (outcome === 'success') h.requests[0].resolve({ id: 12, global_code: 'OLD-12' });
    else h.requests[0].reject(new Error('old fallback'));
    await tick();
    assert.equal(h.state.data, pendingData);
    assert.equal(h.routes.length, 0);
    assert.equal(h.errors.length, 0);
    assert.equal(h.state.boxLoading, true);
    completeSelection(h, 3);
    await newer;
  });
}

function startSelection(h, organizationId, marker) {
  const start = h.requests.length;
  const promise = h.choose(organizationId);
  const requests = h.requests.slice(start);
  assert.equal(requests.length, 2);
  assert.ok(requests.every((request) => request.organizationId === organizationId));
  h.render();
  return {
    promise,
    complete() {
      for (const request of requests) {
        request.resolve(request.url === '/api/dashboard/'
          ? { organizationId, marker, recent_accesses: [{ metadata: { box_id: marker } }] }
          : { results: [{ id: marker }] });
      }
    },
    fail() { requests[0].reject(new Error(`Organization ${organizationId} failed`)); },
  };
}
function assertEmptyOrganizationData(h, organizationId) {
  assert.equal(h.state.organizationId, organizationId);
  assert.equal(h.state.contextId, organizationId);
  assert.equal(h.state.data.profile.active_organization.id, organizationId);
  assert.equal(h.state.data.boxes.length, 0);
  assert.equal(Object.keys(h.state.data.boxDetails).length, 0);
  assert.equal(h.state.data.zones.length, 0);
  assert.equal(h.state.data.dashboard, null);
  assert.equal(h.state.data.overview, null);
  assert.equal(h.state.data.exportOptions, null);
  assert.equal(h.state.recent.length, 0);
  assert.equal(h.state.qrLabels.length, 0);
  assert.equal(h.state.measurementPrefill, null);
  assert.equal(h.state.exportOptionsRequested, false);
  assert.equal(h.state.search, '');
}

for (const language of ['fr', 'en']) {
  test(`loaded A -> failed B clears organization data and preserves session/profile (${language})`, async () => {
    const h = harness();
    h.state.data.profile.interface_language = language;
    const loadedA = startSelection(h, 2, 201);
    loadedA.complete();
    await loadedA.promise;
    h.state.data = {
      ...h.state.data,
      boxDetails: { 201: { id: 201 } },
      overview: [{ id: 201 }],
      exportOptions: { boxes: [{ id: 201 }] },
    };
    h.state.qrLabels = [{ id: 201 }];
    h.state.measurementPrefill = { id: 801, box_id: 201 };
    h.state.exportOptionsRequested = true;
    h.state.search = 'A box';
    h.render();
    const previousProfile = h.state.data.profile;
    assert.equal(h.state.data.boxes.length, 0, 'bootstrap loads no Box');
    assert.equal(h.state.data.zones[0].id, 201);
    assert.equal(h.state.recent[0], 201);

    const failedB = startSelection(h, 3, 301);
    assertEmptyOrganizationData(h, 3);
    assert.equal(h.state.loading, true);
    failedB.fail();
    await tick();
    assert.equal(h.errors.length, 1);
    const error = { message: 'B could not load', requiresAuthentication: false };
    h.errors[0].resolve(error);
    await failedB.promise;
    assertEmptyOrganizationData(h, 3);
    assert.equal(h.state.error, error);
    assert.equal(h.state.loading, false);
    assert.equal(h.state.data.profile.id, previousProfile.id);
    assert.equal(h.state.data.profile.email, previousProfile.email);
    assert.equal(h.state.data.profile.interface_language, language);
    assert.equal(h.state.data.profile.memberships, previousProfile.memberships);
    assert.equal(h.state.data.profile.organizations, previousProfile.organizations);
  });
}

for (const outcome of ['success', 'failure']) {
  for (const finalFirst of [false, true]) {
    test(`rapid A -> B -> A ignores old A and B ${outcome} (${finalFirst ? 'final completes first' : 'final completes last'})`, async () => {
      const h = harness();
      const firstA = startSelection(h, 2, 201);
      const middleB = startSelection(h, 3, 301);
      const finalA = startSelection(h, 2, 202);
      assert.equal(h.context.organizationRequestGenerationRef.current, 3);
      assertEmptyOrganizationData(h, 2);
      assert.equal(h.state.loading, true);
      if (finalFirst) {
        finalA.complete();
        await finalA.promise;
      }
      const currentData = h.state.data;
      firstA.complete();
      await firstA.promise;
      assert.equal(h.state.data, currentData);
      assert.equal(h.state.loading, !finalFirst);
      if (outcome === 'success') middleB.complete();
      else middleB.fail();
      await middleB.promise;
      assert.equal(h.state.data, currentData);
      assert.equal(h.state.error, null);
      assert.equal(h.errors.length, 0);
      assert.equal(h.state.loading, !finalFirst);
      if (!finalFirst) {
        finalA.complete();
        await finalA.promise;
      }
      assert.equal(h.state.data.profile.active_organization.id, 2);
      assert.equal(h.state.data.zones[0].id, 202);
      assert.equal(h.state.data.dashboard.marker, 202);
      assert.deepEqual(Array.from(h.state.recent), [202]);
      assert.equal(h.state.contextId, 2);
      assert.equal(h.state.loading, false);
    });
  }
}

test('rapid A -> B -> A ignores B error recovery already pending before final A', async () => {
  const h = harness();
  const firstA = startSelection(h, 2, 201);
  const middleB = startSelection(h, 3, 301);
  middleB.fail();
  await tick();
  assert.equal(h.errors.length, 1);
  const finalA = startSelection(h, 2, 202);
  const pendingData = h.state.data;
  h.errors[0].resolve({ message: 'Late B error', requiresAuthentication: true });
  await middleB.promise;
  firstA.complete();
  await firstA.promise;
  assert.equal(h.state.data, pendingData);
  assertEmptyOrganizationData(h, 2);
  assert.equal(h.state.error, null);
  assert.equal(h.state.loading, true);
  finalA.complete();
  await finalA.promise;
  assert.equal(h.state.data.zones[0].id, 202);
  assert.equal(h.state.loading, false);
});

test('reselecting the pending active organization does not restart or invalidate its request', async () => {
  const h = harness();
  const selection = startSelection(h, 2, 201);
  const pendingData = h.state.data;
  const generation = h.context.organizationRequestGenerationRef.current;
  await h.choose(2);
  h.render();
  assert.equal(h.requests.length, 2);
  assert.equal(h.context.organizationRequestGenerationRef.current, generation);
  assert.equal(h.state.data, pendingData);
  assert.equal(h.state.loading, true);
  selection.complete();
  await selection.promise;
  h.render();
  const loadedData = h.state.data;
  await h.choose(2);
  assert.equal(h.requests.length, 2);
  assert.equal(h.context.organizationRequestGenerationRef.current, generation);
  assert.equal(h.state.data, loadedData);
  assert.equal(h.state.data.zones[0].id, 201);
  assert.equal(h.state.loading, false);
});

test('same organization no-op does not invalidate current requests', async () => {
  const h = harness();
  await h.choose(1);
  assert.equal(h.context.organizationRequestGenerationRef.current, 0);
  assert.equal(h.context.openBoxRequestGenerationRef.current, 0);
  assert.equal(h.requests.length, 0);
});
