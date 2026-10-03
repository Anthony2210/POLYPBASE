import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

export const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
export const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const compiledFunctions = new Map();
export function functionNode(name) {
  let found;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node;
    ts.forEachChild(node, visit);
  }
  visit(ast);
  assert.ok(found, `Missing App function: ${name}`);
  return found;
}
export function deferred() {
  let resolve, reject;
  const promise = new Promise((accept, decline) => { resolve = accept; reject = decline; });
  return { promise, resolve, reject };
}
export async function tick() {
  for (let index = 0; index < 30; index += 1) await Promise.resolve();
}
export function createHistoryFixture(initialPath = '/') {
  const entries = [{ path: 'https://external.invalid', state: null }, { path: initialPath, state: null }];
  let index = 1, queued = null, backCalls = 0, pushCalls = 0, replaceCalls = 0;
  const location = { origin: 'https://polypbase.test' };
  function updateLocation() {
    const url = new URL(entries[index].path, location.origin);
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
      replaceCalls++;
      entries[index] = { path, state: structuredClone(state) };
      updateLocation();
    },
    back() { backCalls++; queued = index - 1; },
  };
  return {
    location, history, entries,
    get path() { return entries[index].path; },
    get backCalls() { return backCalls; },
    get pushCalls() { return pushCalls; },
    get replaceCalls() { return replaceCalls; },
    flush(sync) {
      assert.notEqual(queued, null);
      index = queued;
      queued = null;
      updateLocation();
      sync();
    },
    travel(delta, sync) { index += delta; updateLocation(); sync(); },
  };
}

// Install actual modules and extracted App routing into each isolated VM. Keep
// setters/request lifetimes owned by the caller; never stub the history ledger.
export function installAppRouting(context, evaluate, organization = 1) {
  Object.assign(context, {
    URL, URLSearchParams,
    inAppHistoryRef: { current: null },
    navigationOrganizationRef: { current: organization },
    navigationPolicyRef: { current: { isDesktopApp: true, canUseAdmin: true } },
    navigationGenerationRef: context.navigationGenerationRef ?? { current: 0 },
  });
  for (const name of ['inAppHistory', 'authRouting', 'routeSafety']) {
    const moduleSource = readFileSync(new URL(`../src/utils/${name}.ts`, import.meta.url), 'utf8');
    const { outputText } = ts.transpileModule(moduleSource, {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
    });
    const exports = evaluate(`(() => { const exports = {}; ${outputText}\nreturn exports; })()`);
    Object.assign(context, exports);
  }
  const app = functionNode('App');
  const adminPaths = ast.statements.find(node => ts.isVariableStatement(node)
    && node.declarationList.declarations.some(declaration => declaration.name.getText(ast) === 'ADMIN_SECTION_PATHS'));
  assert.ok(adminPaths);
  context.ADMIN_SECTION_PATHS = evaluate(`(${adminPaths.declarationList.declarations[0].initializer.getText(ast)})`);
  for (const name of ['getCurrentAppPath', 'isRecognizedAppPath', 'getCurrentRoute', 'getInAppHistory',
    'resetNavigation', 'updateNavigationOrganization', 'navigateTo', 'replaceRoute', 'goBack',
    'closeBoxPage', 'closeZonePage', 'closeZoneSubview', 'openZone', 'openZoneBoxes', 'openZoneHistory', 'syncRoute']) {
    // Restrict nested routing functions to App, not similarly named UI callbacks.
    const node = app.body.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name)
      ?? functionNode(name);
    evaluate(node.getText(ast));
  }
  context.getInAppHistory();
}

export function appHarness() {
  const browser = createHistoryFixture();
  const requests = [];
  const queuedUpdates = [];
  const state = { data: { boxes: [], boxDetails: {}, zones: [], overview: [], exportOptions: {} }, recovery: null };
  const context = {
    URL, Set, Map,
    __API_PROXY_ORIGIN__: null,
    window: { location: browser.location, history: browser.history },
    activeOrganizationId: 1,
    openBoxRequestGenerationRef: { current: 0 },
    setRoute(value) { state.route = value; },
    setActiveOrganizationId(value) { context.activeOrganizationId = value; },
    setSearch() {}, setIsTabletScannerOpen() {}, setIsLoginRoute() {}, setPasswordReset() {},
    operationGeneration: 0,
    organizationRequestGenerationRef: { current: 0 },
    ApiResourceCancelledError: class ApiResourceCancelledError extends Error {},
    ApiError: class ApiError extends Error {},
    setData(update) { state.data = update(state.data); },
    setRefreshRecovery(update) { state.recovery = typeof update === 'function' ? update(state.recovery) : update; },
    isMeasurementWeekConflict(error) { return error?.data?.code === 'measurement_week_conflict'; },
    isMeasurementEditWindowExpired(error) { return error?.data?.code === 'edit_window_expired'; },
    isBoxLocationChangedError(error) { return error?.data?.code === 'box_location_changed'; },
    upsertBoxes(items, updates) { return [...items.filter(item => !updates.some(update => update.id === item.id)), ...updates]; },
    upsertThermalZones(items, updates) { return [...items.filter(item => !updates.some(update => update.id === item.id)), ...updates]; },
  };
  for (const method of ['apiGet', 'apiPost', 'apiPatch', 'apiDelete']) {
    context[method] = (url, payload) => {
      const request = { ...deferred(), method, url, payload, generation: context.organizationRequestGenerationRef.current };
      requests.push(request);
      return request.promise;
    };
  }
  vm.createContext(context);
  function evaluate(code) {
    if (!compiledFunctions.has(code)) {
      const { outputText } = ts.transpileModule(code, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } });
      compiledFunctions.set(code, outputText);
    }
    return vm.runInContext(compiledFunctions.get(code), context);
  }
  installAppRouting(context, evaluate);
  for (const name of ['getOperationRequests', 'fetchAllPages', 'mergeBoxDetail', 'refreshAfterMutation', 'applyMeasurementResult',
    'refreshBoxAfterMeasurement', 'createMeasurement', 'updateMeasurement', 'createBox', 'createSubculture', 'moveBox',
    'deactivateBox', 'reactivateBox', 'qualifyBox', 'assignBoxInitialLocation', 'qualifyBoxesBatch', 'loadLineageGraph',
    'createThermalZone', 'updateThermalZone', 'recordManualTemperature', 'refreshZoneSalinityCapability',
    'recordManualSalinity', 'updateManualSalinity', 'createProbe', 'createOrganization', 'updateOrganization',
    'deleteOrganization', 'createBoxTransfer', 'parsePositiveInteger', 'incrementCountValue', 'decrementCountValue',
    'buildMeasurementPayload', 'saveMeasurement', 'handleLoadLineageGraph']) {
    evaluate(functionNode(name).getText(ast));
  }
  return { context, state, requests, queuedUpdates, evaluate, browser,
    switchOrganization() { context.organizationRequestGenerationRef.current += 1; },
    queueUpdates() { context.setData = update => queuedUpdates.push(update); },
  };
}
