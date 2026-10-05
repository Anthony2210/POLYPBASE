import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import { appHarness, ast, functionNode, deferred, tick } from './app-operation-test-harness.mjs';

// Use real API error classes so the extracted handlers exercise their instanceof branches.
const clientSource = readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8');
const clientAst = ts.createSourceFile('client.ts', clientSource, ts.ScriptTarget.Latest, true);
const errorClasses = ['ApiError', 'ApiResourceCancelledError'].map(name => {
  const node = clientAst.statements.find(node => ts.isClassDeclaration(node) && node.name?.text === name);
  assert.ok(node, `Missing API error class: ${name}`);
  return node.getText(clientAst);
}).join('\n');
const apiErrors = {};
vm.runInNewContext(ts.transpileModule(errorClasses, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText, { exports: apiErrors });

// Render the actual App banner rather than duplicating its presentation condition.
function findSuccessBanner(node) {
  if (ts.isJsxElement(node) && node.openingElement.attributes.properties.some(attribute =>
    ts.isJsxAttribute(attribute) && attribute.name.getText(ast) === 'className'
    && attribute.initializer && ts.isStringLiteral(attribute.initializer) && attribute.initializer.text === 'subculture-success')) return node;
  return ts.forEachChild(node, findSuccessBanner);
}
const successBanner = findSuccessBanner(ast);
assert.ok(successBanner, 'Missing App subculture success banner');
const presentation = {};
vm.runInNewContext(ts.transpileModule(`export const renderSuccess = (subcultureSuccess, t) => (${successBanner.getText(ast)});`, {
  fileName: 'subculture-success.tsx',
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText, {
  exports: presentation,
  require(name) { assert.equal(name, 'react/jsx-runtime'); return jsxRuntime; },
});

for (const language of ['fr', 'en']) {
  test(`${language}: actual success banner shows quantitative transitions only for complete results`, () => {
    const catalogue = {};
    vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`../src/i18n/${language}.ts`, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    }).outputText, { exports: catalogue });
    const t = key => catalogue[language][key];
    for (const scenario of [
      { aggregate: null, after: null, allocations: [30, null] },
      { aggregate: null, after: null, allocations: [null, null] },
      { aggregate: null, after: 0, allocations: [0, null] },
      { aggregate: 0, after: null, allocations: [0, 0] },
      { aggregate: 0, after: 0, allocations: [0, null] },
    ]) {
      const result = subcultureResult();
      result.allocated_polyp_count = scenario.aggregate;
      result.parent_polyp_count_after = scenario.after;
      result.allocations.forEach((allocation, index) => { allocation.allocated_polyps = scenario.allocations[index]; });
      const html = renderToStaticMarkup(presentation.renderSuccess(result, t));
      assert.equal(html, `<p class="subculture-success" role="status"><strong>${t('subcultureCompleted')}</strong> — SERVER-ASSIGNED-804, SERVER-ASSIGNED-917</p>`);
      assert.doesNotMatch(html, /→|null|undefined/);
    }
    for (const after of [0, 3]) {
      const result = subcultureResult(after);
      const html = renderToStaticMarkup(presentation.renderSuccess(result, t));
      assert.ok(html.includes(`12 → ${after} ${t('polyps').toLocaleLowerCase()}`));
      assert.ok(html.includes(t('subcultureCompleted')));
      assert.equal((html.match(/<p /g) ?? []).length, 1);
    }
    const zero = subcultureResult();
    zero.parent_polyp_count_before = zero.allocated_polyp_count = 0;
    zero.allocations.forEach(allocation => { allocation.allocated_polyps = 0; });
    assert.ok(renderToStaticMarkup(presentation.renderSuccess(zero, t)).includes(`0 → 0 ${t('polyps').toLocaleLowerCase()}`));
  });
}

function parentBox(polypCount = 12, revision = 'parent-revision-before') {
  return {
    id: 17, global_code: 'LEGACY-PARENT', status: 'active',
    species: { id: 1, scientific_name: 'Aurelia aurita' },
    organization: { id: 1, name: 'Isolated laboratory' },
    thermal_zone: { id: 2, name: 'Parent zone' },
    current_polyp_state: { polyp_count: polypCount, revision, source: { kind: 'measurement', id: 8 } },
    latest_measurement: { id: 8, polyp_count: 12 },
  };
}

function subcultureResult(parentCountAfter = 0) {
  const counts = [5, 7 - parentCountAfter];
  const children = counts.map((count, index) => ({
    ...parentBox(count, `child-revision-${index}`),
    id: 91 + index, global_code: ['SERVER-ASSIGNED-804', 'SERVER-ASSIGNED-917'][index],
  }));
  return {
    id: 41, parent_box: 'LEGACY-PARENT', event_date: '2026-10-04', occurred_at: '2026-10-04T10:00:00Z',
    reason: 'Split stock', notes: 'Keep this draft', user: 'Lab operator',
    parent_polyp_count_before: 12, allocated_polyp_count: 12 - parentCountAfter,
    parent_polyp_count_after: parentCountAfter,
    parent_state_snapshot: { polyp_count: 12, revision: 'parent-revision-before' },
    allocations: children.map((child, index) => ({
      id: 61 + index, position: index + 1, child_box_id: child.id,
      child_global_code: child.global_code, allocated_polyps: counts[index],
    })),
    children,
  };
}

function workflow({ answers = [true, false], canChangeBoxStatus = true } = {}) {
  const h = appHarness();
  const parent = parentBox();
  const draft = {
    expected_current_state_revision: parent.current_polyp_state.revision,
    reason: 'Split stock', notes: 'Keep this draft',
    children: [5, 7].map(allocated_polyps => ({
      thermal_zone_id: 2, allocated_polyps, copy_origin: true, notes: `Allocation ${allocated_polyps}`,
    })),
  };
  h.state.data = {
    ...h.state.data, boxes: [parent], boxDetails: { [parent.id]: parent },
    overview: { cached: true }, exportOptions: { cached: true },
  };
  const ui = {
    isSavingSubculture: false, isSubcultureOpen: true, subcultureError: null, subcultureSuccess: null,
    isChangingBoxStatus: false, lifecycleAction: null, statusError: null,
  };
  const writes = [], confirmations = [], remainingAnswers = [...answers];
  Object.assign(h.context, apiErrors, {
    box: parent, canChangeBoxStatus, ...ui,
    operationLifetimeRef: { current: true },
    isOperationCurrent: () => h.context.operationGeneration === h.context.organizationRequestGenerationRef.current,
    t: key => key,
    getErrorMessage: error => error.message,
    confirmAction(options) {
      confirmations.push({ options, ui: { ...ui } });
      assert.ok(remainingAnswers.length, 'Unexpected confirmation');
      return Promise.resolve(remainingAnswers.shift());
    },
    onCreateSubculture: (boxId, payload) => h.context.createSubculture(boxId, payload),
    onDeactivateBox: (boxId, payload) => h.context.deactivateBox(boxId, payload),
    onReactivateBox: (boxId, payload) => h.context.reactivateBox(boxId, payload),
  });
  for (const key of Object.keys(ui)) {
    h.context[`set${key[0].toUpperCase()}${key.slice(1)}`] = value => {
      writes.push({ key, value });
      ui[key] = value;
      h.context[key] = value;
    };
  }
  for (const name of ['getSubcultureSaveError', 'handleSubculture', 'handleLifecycleSubmit']) {
    h.evaluate(functionNode(name).getText(ast));
  }
  return { ...h, parent, draft, ui, writes, confirmations };
}

function assertRequest(h, index, method, url) {
  const request = h.requests[index];
  assert.ok(request, `Missing request ${index}: ${method} ${url}`);
  assert.equal(request.method, method);
  assert.equal(request.url, url);
  return request;
}

async function startSubculture(h) {
  const pending = h.context.handleSubculture(h.draft);
  await tick();
  const mutation = assertRequest(h, 0, 'apiPost', '/api/boxes/17/subcultures/');
  assert.equal(mutation.payload, h.draft);
  assert.equal(h.ui.isSavingSubculture, true);
  assert.equal(h.ui.subcultureSuccess, null);
  assert.equal(h.ui.isSubcultureOpen, true);
  assert.equal(h.confirmations.length, 1);
  return { pending, mutation };
}

function authoritativeParentCount(h, result) {
  return typeof result.allocated_polyp_count === 'number' && typeof result.parent_polyp_count_after === 'number'
    && result.allocations.length > 0 && result.allocations.every(allocation => allocation.allocated_polyps !== null)
    ? result.parent_polyp_count_after : h.parent.current_polyp_state.polyp_count;
}

async function commitSubculture(h, result = subcultureResult()) {
  const operation = await startSubculture(h);
  operation.mutation.resolve(result);
  await tick();
  const refreshed = parentBox(authoritativeParentCount(h, result), 'parent-revision-after');
  if (result.allocations.some(allocation => allocation.allocated_polyps === null)) {
    refreshed.current_polyp_state.source = h.parent.current_polyp_state.source;
  }
  assertRequest(h, 1, 'apiGet', '/api/boxes/17/').resolve(refreshed);
  await operation.pending;
  return result;
}

function assertCommitted(h, result) {
  assert.equal(h.ui.subcultureSuccess, result);
  assert.equal(h.ui.subcultureError, null);
  assert.equal(h.ui.isSubcultureOpen, false);
  assert.equal(h.ui.isSavingSubculture, false);
  assert.equal(h.state.data.boxDetails[17].current_polyp_state.polyp_count, authoritativeParentCount(h, result));
  for (const child of result.children) {
    assert.equal(h.state.data.boxes.find(box => box.id === child.id), child);
  }
  assert.equal(h.requests.filter(request => request.method === 'apiPost').length, 1);
}

function assertDraftRetained(h, snapshot) {
  // The modal owns its draft; these App handlers must not close it or mutate its submitted intent.
  assert.equal(h.ui.isSubcultureOpen, true);
  assert.equal(h.ui.subcultureSuccess, null);
  assert.equal(h.ui.isSavingSubculture, false);
  assert.equal(h.ui.lifecycleAction, null);
  assert.deepEqual(h.draft, snapshot);
  assert.equal(h.confirmations.length, 1);
  assert.equal(h.requests.filter(request => request.method === 'apiPost').length, 1);
}

function stateConflict(h, state = { polyp_count: 8, revision: 'conflict-revision', source: { kind: 'subculture', id: 40 } }) {
  return new h.context.ApiError(409, 'Current state changed', {
    code: 'subculture_current_state_changed', current_polyp_state: state,
  });
}

test('actual App workflow retains server child identities and never creates provisional children', async () => {
  const h = workflow();
  const result = subcultureResult();
  const initialData = h.state.data;
  const { pending, mutation } = await startSubculture(h);
  assert.equal(h.state.data, initialData);
  assert.deepEqual(h.state.data.boxes.map(box => box.global_code), ['LEGACY-PARENT']);
  assert.ok(h.draft.children.every(child => !('global_code' in child) && !('box_number' in child) && !('id' in child)));
  assert.equal(h.confirmations[0].options.title, 'confirmSubcultureTitle');
  assert.equal(h.confirmations[0].options.details[2].value, 2);

  mutation.resolve(result);
  await tick();
  assert.deepEqual(h.state.data.boxes.filter(box => box.id !== 17).map(box => box.global_code),
    result.children.map(child => child.global_code));
  assert.equal(h.ui.subcultureSuccess, null, 'The handler is still awaiting the parent read');
  assertRequest(h, 1, 'apiGet', '/api/boxes/17/').resolve(parentBox(0, 'parent-revision-after'));
  await pending;
  assertCommitted(h, result);
  assert.deepEqual(h.ui.subcultureSuccess.allocations.map(allocation => allocation.child_global_code),
    result.children.map(child => child.global_code));
  assert.equal(h.state.data.boxes.length, 3);
});

for (const deactivate of [true, false]) {
  test(`zero remainder: optional answer ${deactivate ? 'yes opens existing lifecycle action' : 'no keeps parent active'} without a deactivation API call`, async () => {
    const h = workflow({ answers: [true, deactivate] });
    h.ui.statusError = h.context.statusError = 'Previous lifecycle error';
    const result = await commitSubculture(h);
    assertCommitted(h, result);
    assert.equal(h.confirmations.length, 2);
    const question = h.confirmations[1];
    assert.equal(question.options.title, 'subcultureDeactivateParentTitle');
    assert.equal(question.options.confirmLabel, 'boxArchiveAction');
    assert.equal(question.options.cancelLabel, 'subcultureKeepParentActive');
    assert.equal(question.options.details[0].value, h.parent.global_code);
    assert.equal(question.ui.subcultureSuccess, result);
    assert.equal(question.ui.isSubcultureOpen, false);
    assert.equal(question.ui.isSavingSubculture, false);
    assert.equal(h.ui.lifecycleAction, deactivate ? 'deactivate' : null);
    assert.equal(h.ui.statusError, deactivate ? null : 'Previous lifecycle error');
    assert.equal(h.state.data.boxDetails[17].status, 'active');
    assert.equal(h.requests.length, 2);
  });
}

test('known zero parent and explicit zero allocations retain the deactivation followup', async () => {
  const h = workflow({ answers: [true, false] });
  h.parent.current_polyp_state.polyp_count = 0;
  h.draft.children.forEach(child => { child.allocated_polyps = 0; });
  const result = subcultureResult();
  result.parent_polyp_count_before = 0;
  result.allocated_polyp_count = 0;
  result.allocations.forEach(allocation => { allocation.allocated_polyps = 0; });
  result.children.forEach(child => { child.current_polyp_state.polyp_count = 0; });
  await commitSubculture(h, result);
  assertCommitted(h, result);
  assert.equal(h.confirmations.length, 2);
  assert.equal(h.confirmations[1].options.title, 'subcultureDeactivateParentTitle');
  assert.equal(h.ui.lifecycleAction, null);
});

for (const scenario of [
  { name: 'without status permission', canChangeBoxStatus: false, count: 0 },
  { name: 'positive remainder', canChangeBoxStatus: true, count: 3 },

]) {
  test(`${scenario.name}: successful subculture asks no deactivation question`, async () => {
    const h = workflow({ answers: [true], canChangeBoxStatus: scenario.canChangeBoxStatus });
    const result = subcultureResult(scenario.count);
    h.draft.children[1].allocated_polyps = result.children[1].current_polyp_state.polyp_count;
    await commitSubculture(h, result);
    assertCommitted(h, result);
    assert.equal(h.confirmations.length, 1);
    assert.equal(h.ui.lifecycleAction, null);
    assert.equal(h.state.data.boxDetails[17].status, 'active');
    assert.equal(h.requests.length, 2);
  });
}

for (const scenario of [
  { name: 'mixed allocation', counts: [30, null], aggregate: null, after: null },
  { name: 'all unknown allocations', counts: [null, null], aggregate: null, after: null },
  { name: 'zero parent with unknown aggregate', counts: [0, null], aggregate: null, after: 0 },
  { name: 'zero parent with partial response allocations', counts: [0, null], aggregate: 0, after: 0 },
]) {
  test(`${scenario.name}: committed unknown allocation never opens zero deactivation`, async () => {
    const h = workflow({ answers: [true] });
    h.parent.current_polyp_state.polyp_count = scenario.counts[0] === 30 ? 50 : 0;
    h.draft.children.forEach((child, index) => { child.allocated_polyps = scenario.counts[index]; });
    const source = h.parent.current_polyp_state.source;
    const result = subcultureResult();
    result.parent_polyp_count_before = h.parent.current_polyp_state.polyp_count;
    result.allocated_polyp_count = scenario.aggregate;
    result.parent_polyp_count_after = scenario.after;
    result.allocations.forEach((allocation, index) => { allocation.allocated_polyps = scenario.counts[index]; });
    result.children.forEach((child, index) => { child.current_polyp_state.polyp_count = scenario.counts[index]; });
    await commitSubculture(h, result);
    assertCommitted(h, result);
    assert.equal(h.state.data.boxDetails[17].current_polyp_state.source, source);
    assert.equal(h.state.data.boxDetails[17].current_polyp_state.revision, 'parent-revision-after');
    assert.equal(result.parent_polyp_count_after, scenario.after, 'Event after remains distinct from the authoritative parent state');
    assert.equal(h.confirmations.length, 1);
    assert.equal(h.confirmations[0].options.message, 'confirmSubcultureMessage');
    assert.equal(h.ui.lifecycleAction, null);
    assert.equal(h.state.data.boxDetails[17].status, 'active');
    assert.equal(h.requests.length, 2);
  });
}

for (const language of ['fr', 'en']) {
  test(`${language}: confirmation with unknown allocations makes no remainder claim`, async () => {
    const catalogue = {};
    vm.runInNewContext(ts.transpileModule(readFileSync(new URL(`../src/i18n/${language}.ts`, import.meta.url), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS },
    }).outputText, { exports: catalogue });
    const h = workflow({ answers: [false] });
    h.context.t = key => catalogue[language][key];
    h.draft.children[1].allocated_polyps = null;
    await h.context.handleSubculture(h.draft);
    assert.equal(h.confirmations[0].options.message, language === 'fr'
      ? 'Les nouvelles boîtes seront reliées à la boîte parent.'
      : 'The new boxes will be linked to the parent box.');
    assert.equal(h.confirmations[0].options.details.length, 3);
    assert.equal(h.requests.length, 0);
  });
}

for (const forbidden of [false, true]) {
  test(`separate deactivation ${forbidden ? 'permission' : 'mutation'} failure retains committed subculture success`, async () => {
    const h = workflow({ answers: [true, true] });
    const result = await commitSubculture(h);
    assert.equal(h.ui.lifecycleAction, 'deactivate');
    assert.equal(h.requests.length, 2, 'Choosing yes must only open the lifecycle modal');
    const payload = { reason: 'Separate lifecycle operation' };
    const pending = h.context.handleLifecycleSubmit({ action: 'deactivate', payload });
    assert.equal(h.ui.isChangingBoxStatus, true);
    const request = assertRequest(h, 2, 'apiPost', '/api/boxes/17/deactivate/');
    assert.equal(request.payload, payload);
    request.reject(forbidden ? new h.context.ApiError(403, 'Forbidden') : new Error('Deactivation offline'));
    await pending;
    assert.equal(h.ui.subcultureSuccess, result);
    assert.equal(h.ui.subcultureError, null);
    assert.equal(h.ui.isSubcultureOpen, false);
    assert.equal(h.ui.statusError, forbidden ? 'boxArchiveForbidden' : 'Deactivation offline');
    assert.equal(h.ui.lifecycleAction, 'deactivate');
    assert.equal(h.ui.isChangingBoxStatus, false);
    assert.equal(h.state.data.boxDetails[17].status, 'active');
    assert.equal(h.state.data.boxDetails[17].current_polyp_state.polyp_count, 0);
    assert.ok(result.children.every(child => h.state.data.boxes.includes(child)));
    assert.equal(h.requests.filter(request => request.url.endsWith('/subcultures/')).length, 1);
  });
}

for (const failure of ['network', 'forbidden', 'unrelated conflict']) {
  test(`normal subculture ${failure} failure shows no success, refresh, or lifecycle question`, async () => {
    const h = workflow({ answers: [true] });
    const snapshot = structuredClone(h.draft);
    const initialData = h.state.data;
    const { pending, mutation } = await startSubculture(h);
    const error = failure === 'forbidden' ? new h.context.ApiError(403, 'Forbidden')
      : failure === 'unrelated conflict' ? new h.context.ApiError(409, 'Other conflict', { code: 'other_conflict' })
        : new Error('Mutation offline');
    mutation.reject(error);
    await pending;
    assertDraftRetained(h, snapshot);
    assert.equal(h.ui.subcultureError, failure === 'forbidden' ? 'subcultureForbidden' : error.message);
    assert.equal(h.state.data, initialData);
    assert.equal(h.requests.length, 1);
  });
}

for (const refreshFails of [false, true]) {
  test(`stale-state 409 with ${refreshFails ? 'failed' : 'successful'} refresh retains draft and modal without silently retrying intent`, async () => {
    const h = workflow({ answers: [true] });
    const snapshot = structuredClone(h.draft);
    const { pending, mutation } = await startSubculture(h);
    const error = stateConflict(h);
    mutation.reject(error);
    await tick();
    assert.equal(h.state.data.boxDetails[17].current_polyp_state, error.data.current_polyp_state,
      'Conflict response is accepted immediately for display');
    assert.equal(h.ui.isSubcultureOpen, true);
    assert.deepEqual(h.draft, snapshot);
    const refresh = assertRequest(h, 1, 'apiGet', '/api/boxes/17/');
    const freshParent = parentBox(6, 'newer-refresh-revision');
    if (refreshFails) refresh.reject(new Error('Conflict refresh offline'));
    else refresh.resolve(freshParent);
    await pending;
    assertDraftRetained(h, snapshot);
    assert.equal(h.ui.subcultureError, 'subcultureStateChanged');
    assert.equal(h.state.data.boxDetails[17].current_polyp_state,
      refreshFails ? error.data.current_polyp_state : freshParent.current_polyp_state);
    assert.equal(h.draft.expected_current_state_revision, 'parent-revision-before');
    assert.notEqual(h.draft.expected_current_state_revision, h.state.data.boxDetails[17].current_polyp_state.revision);
    assert.equal(h.state.data.boxes.length, 1);
    assert.equal(h.state.data.boxDetails[17].status, 'active');
    assert.equal(h.state.recovery, null);
    await tick();
    assert.equal(h.requests.length, 2, 'Refreshing display state must not retry the POST');
  });
}

test('committed subculture survives parent refresh failure and recovery retries only the read', async () => {
  const h = workflow({ answers: [true, false] });
  const result = subcultureResult();
  const { pending, mutation } = await startSubculture(h);
  mutation.resolve(result);
  await tick();
  assertRequest(h, 1, 'apiGet', '/api/boxes/17/').reject(new Error('Parent refresh offline'));
  await pending;
  assertCommitted(h, result);
  const fallback = h.state.data.boxDetails[17].current_polyp_state;
  assert.equal(fallback.polyp_count, 0);
  assert.equal(fallback.revision, '', 'A committed count fallback must not fabricate an intent revision');
  assert.equal(fallback.source, null);
  assert.equal(h.state.data.boxDetails[17].latest_measurement.polyp_count, 12);
  assert.equal(typeof h.state.recovery, 'function');
  const recovery = h.state.recovery();
  assertRequest(h, 2, 'apiGet', '/api/boxes/17/').resolve(parentBox(0, 'recovered-revision'));
  await recovery;
  assertCommitted(h, result);
  assert.equal(h.state.recovery, null);
  assert.equal(h.state.data.boxDetails[17].current_polyp_state.revision, 'recovered-revision');
  assert.equal(h.confirmations.length, 2);
});

for (const counts of [[30, null], [null, null], [0, null]]) {
  test(`partial ${JSON.stringify(counts)} commit preserves authoritative parent through failed GET and read-only recovery`, async () => {
    const h = workflow({ answers: [true] });
    h.parent.current_polyp_state.polyp_count = counts[0] === 0 ? 0 : 50;
    const originalState = structuredClone(h.parent.current_polyp_state);
    const source = h.parent.current_polyp_state.source;
    h.draft.children.forEach((child, index) => { child.allocated_polyps = counts[index]; });
    const result = subcultureResult();
    result.parent_polyp_count_before = originalState.polyp_count;
    result.allocated_polyp_count = null;
    result.parent_polyp_count_after = null;
    result.allocations.forEach((allocation, index) => { allocation.allocated_polyps = counts[index]; });
    result.children.forEach((child, index) => { child.current_polyp_state.polyp_count = counts[index]; });
    const { pending, mutation } = await startSubculture(h);
    mutation.resolve(result);
    await tick();
    const assertPreserved = () => {
      const detailState = h.state.data.boxDetails[17].current_polyp_state;
      assert.equal(detailState.polyp_count, originalState.polyp_count);
      assert.equal(detailState.source, source);
      assert.equal(detailState.revision, '', 'The old intent revision cannot be reused');
      assert.equal(h.state.data.boxes.find(box => box.id === 17).current_polyp_state, detailState);
    };
    assertPreserved();
    assertRequest(h, 1, 'apiGet', '/api/boxes/17/').reject(new Error('Partial parent refresh offline'));
    await pending;
    assertCommitted(h, result);
    assertPreserved();
    assert.equal(h.confirmations.length, 1);
    assert.equal(h.ui.lifecycleAction, null);
    assert.equal(typeof h.state.recovery, 'function');
    await tick();
    assert.equal(h.requests.length, 2, 'A committed POST is never automatically retried');
    const recovery = h.state.recovery();
    const refreshed = parentBox(originalState.polyp_count, 'recovered-partial-revision');
    refreshed.current_polyp_state.source = structuredClone(source);
    assertRequest(h, 2, 'apiGet', '/api/boxes/17/').resolve(refreshed);
    await recovery;
    assertCommitted(h, result);
    assert.deepEqual(h.state.data.boxDetails[17].current_polyp_state, {
      ...originalState, revision: 'recovered-partial-revision',
    });
    assert.equal(h.state.recovery, null);
    assert.equal(h.requests.filter(request => request.method === 'apiPost').length, 1);
    assert.equal(h.confirmations.length, 1);
    assert.equal(result.parent_polyp_count_after, null);
  });
}

test('initial confirmation cancelled makes no request or state change', async () => {
  const h = workflow({ answers: [false] });
  const initialData = h.state.data;
  const snapshot = structuredClone(h.draft);
  await h.context.handleSubculture(h.draft);
  assert.equal(h.requests.length, 0);
  assert.equal(h.writes.length, 0);
  assert.equal(h.confirmations.length, 1);
  assert.equal(h.state.data, initialData);
  assert.equal(h.ui.isSubcultureOpen, true);
  assert.equal(h.ui.subcultureSuccess, null);
  assert.deepEqual(h.draft, snapshot);
});

for (const outcome of ['success', 'failure', 'conflict']) {
  test(`organization generation changes during mutation: late ${outcome} cannot update replacement data or UI`, async () => {
    const h = workflow({ answers: [true] });
    const { pending, mutation } = await startSubculture(h);
    h.switchOrganization();
    const replacement = { boxes: [{ id: 200, global_code: 'OTHER-ORGANIZATION' }], boxDetails: {}, zones: [] };
    h.state.data = replacement;
    const writesBefore = h.writes.length;
    if (outcome === 'success') mutation.resolve(subcultureResult());
    else mutation.reject(outcome === 'conflict' ? stateConflict(h) : new Error('Old organization failure'));
    await pending;
    assert.equal(h.state.data, replacement);
    assert.equal(h.writes.length, writesBefore);
    assert.equal(h.ui.subcultureSuccess, null);
    assert.equal(h.ui.subcultureError, null);
    assert.equal(h.ui.lifecycleAction, null);
    assert.equal(h.confirmations.length, 1);
    assert.equal(h.requests.length, 1);
    assert.equal(h.state.recovery, null);
  });
}

for (const conflict of [false, true]) {
  for (const refreshFails of [false, true]) {
    test(`organization generation changes during ${conflict ? 'conflict' : 'committed'} refresh: late ${refreshFails ? 'failure' : 'success'} stays isolated`, async () => {
      const h = workflow({ answers: [true] });
      const { pending, mutation } = await startSubculture(h);
      if (conflict) mutation.reject(stateConflict(h));
      else mutation.resolve(subcultureResult());
      await tick();
      const refresh = assertRequest(h, 1, 'apiGet', '/api/boxes/17/');
      h.switchOrganization();
      const replacement = { boxes: [], boxDetails: {}, zones: [] };
      h.state.data = replacement;
      const writesBefore = h.writes.length;
      if (refreshFails) refresh.reject(new Error('Old refresh offline'));
      else refresh.resolve(parentBox(0, 'old-organization-refresh'));
      await pending;
      assert.equal(h.state.data, replacement);
      assert.equal(h.writes.length, writesBefore);
      assert.equal(h.ui.subcultureSuccess, null);
      assert.equal(h.ui.subcultureError, null);
      assert.equal(h.ui.lifecycleAction, null);
      assert.equal(h.confirmations.length, 1);
      assert.equal(h.requests.length, 2);
      assert.equal(h.state.recovery, null);
    });
  }
}

for (const phase of ['initial confirmation', 'optional deactivation confirmation']) {
  test(`organization generation changes during ${phase}: accepting the old question has no effect`, async () => {
    const answer = deferred();
    const h = workflow({ answers: phase === 'initial confirmation' ? [answer.promise] : [true, answer.promise] });
    let pending;
    if (phase === 'initial confirmation') pending = h.context.handleSubculture(h.draft);
    else {
      const operation = await startSubculture(h);
      pending = operation.pending;
      operation.mutation.resolve(subcultureResult());
      await tick();
      assertRequest(h, 1, 'apiGet', '/api/boxes/17/').resolve(parentBox(0, 'parent-revision-after'));
      await tick();
      assert.equal(h.confirmations.length, 2);
      assert.equal(h.ui.subcultureSuccess.parent_polyp_count_after, 0);
    }
    h.switchOrganization();
    const writesBefore = h.writes.length;
    answer.resolve(true);
    await pending;
    assert.equal(h.writes.length, writesBefore);
    assert.equal(h.ui.lifecycleAction, null);
    assert.equal(h.requests.length, phase === 'initial confirmation' ? 0 : 2);
  });
}

test('React applies queued subculture writes only while their organization generation is current', async () => {
  const h = workflow({ answers: [true] });
  h.queueUpdates();
  const { pending, mutation } = await startSubculture(h);
  mutation.resolve(subcultureResult());
  await tick();
  const refresh = assertRequest(h, 1, 'apiGet', '/api/boxes/17/');
  assert.equal(h.queuedUpdates.length, 1);
  h.switchOrganization();
  const replacement = { boxes: [], boxDetails: {}, zones: [] };
  assert.equal(h.queuedUpdates[0](replacement), replacement);
  h.state.data = replacement;
  refresh.resolve(parentBox(0, 'old-revision'));
  await pending;
  assert.equal(h.state.data, replacement);
  assert.equal(h.queuedUpdates.length, 1);
  assert.equal(h.ui.subcultureSuccess, null);
});

test('an extracted createSubculture callback from an old organization cannot start a request', async () => {
  const h = workflow();
  h.switchOrganization();
  await assert.rejects(h.context.createSubculture(17, h.draft), h.context.ApiResourceCancelledError);
  assert.equal(h.requests.length, 0);
});

test('unmounted BoxPage ignores completion even when the App request generation is current', async () => {
  const h = workflow({ answers: [true] });
  const { pending, mutation } = await startSubculture(h);
  h.context.operationLifetimeRef.current = false;
  const writesBefore = h.writes.length;
  mutation.resolve(subcultureResult());
  await tick();
  assertRequest(h, 1, 'apiGet', '/api/boxes/17/').resolve(parentBox(0, 'parent-revision-after'));
  await pending;
  assert.equal(h.writes.length, writesBefore);
  assert.equal(h.ui.subcultureSuccess, null);
  assert.equal(h.ui.lifecycleAction, null);
  assert.equal(h.confirmations.length, 1);
});
