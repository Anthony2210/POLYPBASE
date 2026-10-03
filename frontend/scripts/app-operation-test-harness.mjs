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
export function appHarness() {
  const requests = [];
  const queuedUpdates = [];
  const state = { data: { boxes: [], boxDetails: {}, zones: [], overview: [], exportOptions: {} }, recovery: null };
  const context = {
    URL, Set, Map,
    __API_PROXY_ORIGIN__: null,
    window: { location: { origin: 'https://polypbase.test' } },
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
  for (const name of ['getOperationRequests', 'fetchAllPages', 'mergeBoxDetail', 'refreshAfterMutation', 'applyMeasurementResult',
    'refreshBoxAfterMeasurement', 'createMeasurement', 'updateMeasurement', 'createBox', 'createSubculture', 'moveBox',
    'deactivateBox', 'reactivateBox', 'qualifyBox', 'assignBoxInitialLocation', 'qualifyBoxesBatch', 'loadLineageGraph',
    'createThermalZone', 'updateThermalZone', 'recordManualTemperature', 'refreshZoneSalinityCapability',
    'recordManualSalinity', 'updateManualSalinity', 'createProbe', 'createOrganization', 'updateOrganization',
    'deleteOrganization', 'createBoxTransfer', 'parsePositiveInteger', 'incrementCountValue', 'decrementCountValue',
    'buildMeasurementPayload', 'saveMeasurement', 'handleLoadLineageGraph']) {
    evaluate(functionNode(name).getText(ast));
  }
  return { context, state, requests, queuedUpdates, evaluate,
    switchOrganization() { context.organizationRequestGenerationRef.current += 1; },
    queueUpdates() { context.setData = update => queuedUpdates.push(update); },
  };
}
