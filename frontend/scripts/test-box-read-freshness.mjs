import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { appHarness, ast, deferred, functionNode, source, tick } from './app-operation-test-harness.mjs';

// Run sensitivity switches directly: node scripts/test-box-read-freshness.mjs --disable-invalidation
// (the Node --test runner does not forward them). They change only the VM, never the working tree.
// --baseline reads the two production sources at the reported starting commit.
const baseline = process.argv.includes('--baseline');
const disableInvalidation = process.argv.includes('--disable-invalidation');
const disablePublicationGuard = process.argv.includes('--disable-publication-guard');
const disableMutationPublicationGuard = process.argv.includes('--disable-mutation-publication-guard');
const disableRevisionCleanup = process.argv.includes('--disable-revision-cleanup');
const copyRevisionMapAtDispatch = process.argv.includes('--copy-revision-map-at-dispatch');
const readBaseline = path => execFileSync('git', ['--no-pager', 'show', `0703f19:${path}`], {
  cwd: new URL('../../', import.meta.url), encoding: 'utf8',
});
const appSource = baseline ? readBaseline('frontend/src/App.tsx') : source;
const appAst = baseline ? ts.createSourceFile('App.tsx', appSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX) : ast;
const clientSource = baseline ? readBaseline('frontend/src/api/client.ts')
  : readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8');
const clientCode = ts.transpileModule(clientSource, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;

function findNode(predicate) {
  let found;
  function visit(node) {
    if (predicate(node)) found = node;
    ts.forEachChild(node, visit);
  }
  visit(appAst);
  return found;
}
function appFunction(name) {
  return baseline ? findNode(node => ts.isFunctionDeclaration(node) && node.name?.text === name) : functionNode(name);
}
function box(id = 7, count = 10, extra = {}) {
  const measurement = { id: 81, measured_on: '2026-10-12', polyp_count: count, salinity_psu: 32 };
  return {
    id, global_code: `BOX-${id}`, status: 'active', thermal_zone: 1,
    biological_measurements: [measurement], latest_measurement: measurement,
    current_polyp_state: { polyp_count: count, revision: `revision-${count}`, source: 'measurement' },
    ...extra,
  };
}
const observed = promise => promise.then(value => ({ value }), error => ({ error }));

function harness({ seed = true } = {}) {
  const h = appHarness();
  const fetches = [];
  const storage = new Map();
  Object.assign(h.context, {
    Headers, AbortController, document: { cookie: 'csrftoken=local-test-token' },
    boxDetailRevisionsRef: { current: new Map() },
    data: h.state.data,
    setIsBoxLoading(value) { h.state.loading = value; },
    setError(value) { h.state.error = value; },
    getApplicationError: async error => error,
    fetch(path, options) {
      const pending = deferred();
      const request = {
        ...pending, path, method: options.method ?? 'GET', options,
        organization: options.headers.get('X-Organization-Id'),
        payload: options.body == null ? null : JSON.parse(options.body),
        respond(body, status = 200) {
          pending.resolve({ ok: status >= 200 && status < 300, status,
            headers: new Headers({ 'content-type': 'application/json' }),
            json: async () => structuredClone(body) });
        },
      };
      fetches.push(request);
      return pending.promise;
    },
  });
  h.context.window.localStorage = {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
    removeItem: key => storage.delete(key),
  };
  const client = vm.runInContext(`(() => { const exports = {}; ${clientCode}\nreturn exports; })()`, h.context);
  Object.assign(h.context, client);
  client.setActiveOrganizationContext(1);
  const errorClass = findNode(node => ts.isClassDeclaration(node) && node.name?.text === 'BoxDetailSupersededError');
  if (errorClass) {
    if (h.evaluate('typeof BoxDetailSupersededError') === 'undefined') h.evaluate(errorClass.getText(appAst));
    h.evaluate('globalThis.BoxDetailSupersededError = BoxDetailSupersededError;');
  }
  const invalidation = findNode(node => ts.isFunctionDeclaration(node) && node.name?.text === 'invalidateBoxDetail');
  if (invalidation) h.evaluate(invalidation.getText(appAst));
  if (disableInvalidation) h.context.invalidateBoxDetail = () => {};
  const mutationBoundary = findNode(node => ts.isFunctionDeclaration(node) && node.name?.text === 'beginBoxMutation');
  if (mutationBoundary) {
    let text = mutationBoundary.getText(appAst);
    if (copyRevisionMapAtDispatch) {
      // VM-only fault: emulate the old O(all tracked Boxes) dispatch snapshot.
      const body = mutationBoundary.body.getText(appAst);
      text = text.replace(body, `{ new Map(boxDetailRevisionsRef.current); ${body.slice(1)}`);
    }
    if (disableMutationPublicationGuard) {
      // Disable only mutation publication; retain invalidation, reconciliation and GET guards.
      const guard = 'safe && isCurrent() ? update(current) : current';
      assert.ok(text.includes(guard), 'Expected the actual beginBoxMutation updater guard');
      text = text.replace(guard, 'update(current)');
    }
    h.evaluate(text);
  }
  for (const name of ['getOperationRequests', 'refreshAfterMutation', 'applyMeasurementResult',
    'refreshBoxAfterMeasurement', 'createMeasurement', 'updateMeasurement', 'createBox', 'createSubculture',
    'moveBox', 'deactivateBox', 'reactivateBox', 'qualifyBox', 'assignBoxInitialLocation', 'qualifyBoxesBatch',
    'correctMovement', 'refreshMovementState', 'openBox']) {
    let text = appFunction(name).getText(appAst);
    if (name === 'getOperationRequests' && disablePublicationGuard) {
      const node = appFunction(name);
      let setter;
      function visit(child) {
        if (ts.isPropertyAssignment(child) && child.name.getText(appAst) === 'setData') setter = child.initializer;
        ts.forEachChild(child, visit);
      }
      visit(node);
      assert.ok(setter, 'Expected the actual operation setData wrapper');
      text = text.replace(setter.getText(appAst), '(update: any) => setData(update)');
    }
    h.evaluate(text);
  }
  if (seed) {
    h.state.data.boxDetails[7] = box();
    h.state.data.boxes = [box()];
  }
  return {
    ...h, client, fetches, storage,
    request(method, path, index = 0) {
      const matches = fetches.filter(item => item.method === method && item.path === path);
      assert.ok(matches[index], `Missing ${method} ${path} #${index + 1}; got ${fetches.map(item => `${item.method} ${item.path}`).join(', ')}`);
      return matches[index];
    },
    switchTo(organization) {
      h.context.organizationRequestGenerationRef.current++;
      h.context.operationGeneration = h.context.organizationRequestGenerationRef.current;
      h.context.activeOrganizationId = organization;
      client.setActiveOrganizationContext(organization);
    },
    flush() {
      for (const update of h.queuedUpdates.splice(0)) h.state.data = update(h.state.data);
      h.context.data = h.state.data;
    },
    effect(kind, recentIds = [7]) {
      const callback = findNode(node => ts.isCallExpression(node) && node.expression.getText(appAst) === 'useEffect'
        && ts.isArrowFunction(node.arguments[0]) && (kind === 'initial'
          ? node.arguments[0].getText(appAst).includes('async function loadBoxDetail(')
          : node.arguments[0].getText(appAst).includes('const requestedIds = requestedRecentBoxIdsRef.current')))?.arguments[0];
      assert.ok(callback, `Missing ${kind} detail effect`);
      Object.assign(h.context, { selectedBoxId: 7, selectedBoxDetail: null, activeTab: 'pilotage',
        isBoxRoute: false, isLoading: false, needsOrganizationChoice: false, recentBoxIds: recentIds, recentBoxLimit: 6 });
      h.state.data.profile = { id: 1 };
      h.context.data = h.state.data;
      return h.evaluate(`(${callback.getText(appAst)})()`);
    },
  };
}
function assertZero(h) {
  const detail = h.state.data.boxDetails[7];
  assert.equal(detail.latest_measurement.polyp_count, 0, 'committed zero remains the latest measurement');
  assert.equal(detail.biological_measurements[0].polyp_count, 0);
  assert.equal(h.state.data.boxes.find(item => item.id === 7).latest_measurement.polyp_count, 0);
}
function assertSuperseded(result) {
  assert.equal(result.error?.constructor.name, 'BoxDetailSupersededError', 'obsolete reads are locally superseded, not API failures');
}
// A server commit captures a response snapshot independently of delivery order.
function serverLedger() {
  const details = new Map();
  const commits = [];
  return {
    commits,
    commit(request, detail, body = detail) {
      details.set(detail.id, structuredClone(detail));
      commits.push(detail.id);
      const snapshot = structuredClone(body);
      return () => request.respond(snapshot);
    },
    detail(id = 7) { return structuredClone(details.get(id)); },
  };
}
function assertBox(h, expected) {
  assert.deepEqual(structuredClone(h.state.data.boxDetails[expected.id]), expected);
  assert.deepEqual(structuredClone(h.state.data.boxes.find(item => item.id === expected.id)), expected);
}
async function finishZones(h) {
  await tick();
  for (const request of h.fetches.filter(item => item.path.startsWith('/api/thermal-zones/'))) {
    request.respond({ results: [], next: null });
  }
  await tick();
}

async function startMeasurement(h, kind = 'create', count = 0) {
  const payload = { measured_on: '2026-10-12', polyp_count: count };
  const pending = observed(kind === 'create' ? h.context.createMeasurement(7, payload)
    : h.context.updateMeasurement(7, 81, payload));
  h.request(kind === 'create' ? 'POST' : 'PATCH', kind === 'create'
    ? '/api/boxes/7/measurements/' : '/api/boxes/7/measurements/81/').respond({ ...box(7, count).latest_measurement });
  await tick();
  return { pending };
}

const fullMutations = [
  ['deactivateBox', [7, {}], '/api/boxes/7/deactivate/', { status: 'inactive', thermal_zone: null }],
  ['reactivateBox', [7, {}], '/api/boxes/7/activate/', { status: 'active' }],
  ['qualifyBox', [7, {}], '/api/boxes/7/qualify/', { status: 'inactive' }],
  ['createBox', [{}], '/api/boxes/', { status: 'active' }],
  ['moveBox', [7, {}], '/api/boxes/7/move/', { thermal_zone: 2 }],
  ['assignBoxInitialLocation', [7, {}], '/api/admin/box-inventory/7/assign-location/', { thermal_zone: 2 }],
];
for (const [name, args, path, extra] of fullMutations) {
  for (const schedule of ['commit-forward/respond-forward', 'commit-forward/respond-reverse',
    'commit-reverse/respond-forward', 'commit-reverse/respond-reverse']) {
    test(`${name}: independent server ${schedule} reconciles overlapping full snapshots`, async () => {
      const h = harness();
      const server = serverLedger();
      let completed = false;
      const first = observed(h.context[name](...args)).then(result => { completed = true; return result; });
      const firstRequest = h.request('POST', path);
      const second = observed(h.context.qualifyBox(7, {}));
      const secondRequest = h.request('POST', '/api/boxes/7/qualify/', name === 'qualifyBox' ? 1 : 0);
      const firstDetail = box(7, 5, { ...extra, notes: 'first server commit snapshot' });
      const secondDetail = box(7, 0, { status: 'inactive', thermal_zone: null, notes: 'second snapshot' });
      let deliverFirst, deliverSecond;
      if (schedule.startsWith('commit-reverse')) {
        deliverSecond = server.commit(secondRequest, secondDetail);
        deliverFirst = server.commit(firstRequest, firstDetail);
      } else {
        deliverFirst = server.commit(firstRequest, firstDetail);
        deliverSecond = server.commit(secondRequest, secondDetail);
      }
      const reverseResponse = schedule.endsWith('respond-reverse');
      (reverseResponse ? deliverSecond : deliverFirst)();
      await finishZones(h);
      assert.equal((await (reverseResponse ? second : first)).error, undefined);
      const beforeUnsafe = h.state.data;
      (reverseResponse ? deliverFirst : deliverSecond)();
      await tick();
      assert.equal(h.state.data, beforeUnsafe, 'unsafe full response cannot publish even temporarily');
      if (reverseResponse) assert.equal(completed, false, 'full handler waits for reconciliation');
      assert.equal(h.fetches.filter(item => item.method === 'GET' && item.path.startsWith('/api/thermal-zones/')).length,
        !reverseResponse && ['moveBox', 'assignBoxInitialLocation'].includes(name) ? 1 : 0,
        'unsafe move/assign must reconcile detail before requesting zones');
      h.request('GET', '/api/boxes/7/').respond(server.detail());
      await finishZones(h);
      assert.equal((await (reverseResponse ? first : second)).error, undefined);
      assertBox(h, server.detail());
      assert.equal(h.fetches.filter(item => item.path === '/api/boxes/7/').length, 1);
      assert.equal(h.state.recovery, null);
    });
  }
}

for (const kind of ['create', 'update']) {
  for (const schedule of ['commit-forward/respond-forward', 'commit-forward/respond-reverse',
    'commit-reverse/respond-forward', 'commit-reverse/respond-reverse']) {
    test(`${kind} measurement: independent server ${schedule} preserves authoritative zero and provenance`, async () => {
      const h = harness();
      const server = serverLedger();
      const path = kind === 'create' ? '/api/boxes/7/measurements/' : '/api/boxes/7/measurements/81/';
      const method = kind === 'create' ? 'POST' : 'PATCH';
      const first = observed(kind === 'create' ? h.context.createMeasurement(7, { polyp_count: 5 })
        : h.context.updateMeasurement(7, 81, { polyp_count: 5 }));
      const second = observed(kind === 'create' ? h.context.createMeasurement(7, { polyp_count: 0 })
        : h.context.updateMeasurement(7, 81, { polyp_count: 0 }));
      const firstDetail = box(7, 5, { biological_timeline: [{ identity: 'first' }] });
      const secondDetail = box(7, 0, { biological_timeline: [{ identity: 'zero' }] });
      let deliverFirst, deliverSecond;
      if (schedule.startsWith('commit-reverse')) {
        deliverSecond = server.commit(h.request(method, path, 1), secondDetail, secondDetail.latest_measurement);
        deliverFirst = server.commit(h.request(method, path), firstDetail, firstDetail.latest_measurement);
      } else {
        deliverFirst = server.commit(h.request(method, path), firstDetail, firstDetail.latest_measurement);
        deliverSecond = server.commit(h.request(method, path, 1), secondDetail, secondDetail.latest_measurement);
      }
      const reverse = schedule.endsWith('respond-reverse');
      (reverse ? deliverSecond : deliverFirst)();
      await tick();
      h.request('GET', '/api/boxes/7/').respond(server.detail());
      assert.equal((await (reverse ? second : first)).error, undefined);
      const beforeUnsafe = h.state.data;
      (reverse ? deliverFirst : deliverSecond)();
      await tick();
      assert.equal(h.state.data, beforeUnsafe, 'unsafe measurement cannot clear revision or restore a historical count');
      h.request('GET', '/api/boxes/7/', 1).respond(server.detail());
      assert.equal((await (reverse ? first : second)).error, undefined);
      assertBox(h, server.detail());
      assert.equal(h.fetches.filter(item => item.method === 'GET').length, 2, 'both measurement successes always read detail');
      assert.equal(h.state.recovery, null);
    });
  }
}

for (const kind of ['full', 'measurement', 'partial']) {
  test(`${kind}: queued mutation updater is a reference-preserving no-op after a newer boundary`, async () => {
    const h = harness();
    h.queueUpdates();
    const first = observed(kind === 'full' ? h.context.deactivateBox(7, {})
      : kind === 'measurement' ? h.context.updateMeasurement(7, 81, { polyp_count: 5 })
        : h.context.qualifyBoxesBatch({ box_ids: [7] }));
    if (kind === 'full') h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 5, { status: 'inactive' }));
    else if (kind === 'measurement') h.request('PATCH', '/api/boxes/7/measurements/81/').respond(box(7, 5).latest_measurement);
    else h.request('POST', '/api/admin/box-inventory/batch-qualify/').respond({ successes: [{ box_id: 7, status: 'inactive' }], errors: [] });
    await tick();
    const stale = h.queuedUpdates.shift();
    assert.equal(typeof stale, 'function', 'actual mutation reached the React updater queue');
    const immediateSetData = (update) => { h.state.data = update(h.state.data); };
    h.context.setData = immediateSetData;
    const newer = observed(h.context.reactivateBox(7, {}));
    const authoritative = box(7, 0, { status: 'active', notes: 'newer' });
    h.request('POST', '/api/boxes/7/activate/').respond(authoritative);
    assert.equal((await newer).error, undefined);
    if (kind === 'measurement') h.request('GET', '/api/boxes/7/').respond(box(7, 5));
    assert.equal((await first).error, undefined);
    h.flush();
    const committed = h.state.data;
    h.state.data = stale(committed);
    assert.equal(h.state.data, committed, 'React must not apply an obsolete successful mutation');
    assertBox(h, authoritative);
    assert.equal(h.state.recovery, null);
  });
}

test('delayed committed move cannot restore active state after later deactivation', async () => {
  const h = harness();
  const move = observed(h.context.moveBox(7, {}));
  // Server commits move first; hold its response while deactivation commits second.
  const moved = box(7, 10, { thermal_zone: 2 });
  const deactivate = observed(h.context.deactivateBox(7, {}));
  const inactive = box(7, 10, { status: 'inactive', thermal_zone: null });
  h.request('POST', '/api/boxes/7/deactivate/').respond(inactive);
  await deactivate;
  h.request('POST', '/api/boxes/7/move/').respond(moved);
  await tick();
  assert.equal(h.state.data.boxDetails[7].status, 'inactive');
  assert.equal(h.state.data.boxes[0].thermal_zone, null);
  h.request('GET', '/api/boxes/7/').respond(inactive);
  await tick();
  h.request('GET', '/api/thermal-zones/?limit=80').respond({ results: [], next: null });
  assert.equal((await move).error, undefined);
});

test('delayed earlier correction cannot restore count after committed zero and failed refresh', async () => {
  const h = harness();
  const earlier = observed(h.context.updateMeasurement(7, 81, { polyp_count: 5 }));
  // Commit 5, then commit 0; deliver the zero response/read before the older PATCH response.
  const later = observed(h.context.updateMeasurement(7, 81, { polyp_count: 0 }));
  h.request('PATCH', '/api/boxes/7/measurements/81/', 1).respond(box(7, 0).latest_measurement);
  await tick();
  h.request('GET', '/api/boxes/7/').respond(box(7, 0));
  assert.equal((await later).error, undefined);
  h.request('PATCH', '/api/boxes/7/measurements/81/').respond(box(7, 5).latest_measurement);
  await tick();
  h.request('GET', '/api/boxes/7/', 1).respond({ detail: 'read unavailable' }, 503);
  assert.equal((await earlier).value.polyp_count, 5, 'write still succeeded');
  assertZero(h);
  assert.equal(h.state.data.boxDetails[7].current_polyp_state.revision, 'revision-0');
});

for (const kind of ['create', 'correction']) {
  for (const order of ['old-first', 'fresh-first']) {
    test(`${kind}: real shared pre-mutation GET cannot replace 10 -> 0 (${order})`, async () => {
      const h = harness();
      const old = observed(h.context.refreshBoxAfterMeasurement(7));
      const { pending } = await startMeasurement(h, kind);
      assert.equal(h.fetches.filter(item => item.path === '/api/boxes/7/').length, 2, 'post-commit read must use a fresh fetch');
      const finishOld = async () => { h.request('GET', '/api/boxes/7/').respond(box()); assertSuperseded(await old); assertZero(h); };
      const finishFresh = async () => { h.request('GET', '/api/boxes/7/', 1).respond(box(7, 0)); assert.equal((await pending).error, undefined); assertZero(h); };
      if (order === 'old-first') { await finishOld(); await finishFresh(); }
      else { await finishFresh(); await finishOld(); }
      assert.equal(h.state.data.boxDetails[7].current_polyp_state.polyp_count, 0);
      assert.equal(h.state.recovery, null);
    });
  }
}

test('an obsolete rejected GET is superseded without scheduling recovery', async () => {
  const h = harness();
  const requests = h.context.getOperationRequests();
  const old = observed(h.context.refreshAfterMutation(() => h.context.refreshBoxAfterMeasurement(7), requests.assertCurrent));
  const { pending } = await startMeasurement(h);
  h.request('GET', '/api/boxes/7/').reject(new Error('old transport failed'));
  await old;
  assert.equal(h.state.recovery, null, 'obsolete failure must not offer an obsolete recovery');
  h.request('GET', '/api/boxes/7/', 1).respond(box(7, 0));
  assert.equal((await pending).error, undefined);
  assertZero(h);
});

test('fresh refresh failure preserves committed zero and recovery sends GET only', async () => {
  const h = harness();
  const old = observed(h.context.refreshBoxAfterMeasurement(7));
  const { pending } = await startMeasurement(h);
  h.request('GET', '/api/boxes/7/', 1).respond({ detail: 'temporary read failure' }, 503);
  assert.equal((await pending).value.polyp_count, 0);
  assertZero(h);
  assert.equal(typeof h.state.recovery, 'function');
  const recovery = h.state.recovery;
  h.request('GET', '/api/boxes/7/').respond(box());
  assertSuperseded(await old);
  assert.equal(h.state.recovery, recovery);
  assertZero(h);
  const retry = observed(recovery());
  await tick();
  h.request('GET', '/api/boxes/7/', 2).respond(box(7, 0));
  assert.equal((await retry).error, undefined);
  assert.equal(h.state.recovery, null);
  assert.equal(h.fetches.filter(item => item.method !== 'GET').length, 1, 'never repeat the committed mutation');
});

test('two interleaved committed mutations supersede the first follow-up without recovery', async () => {
  const h = harness();
  const first = await startMeasurement(h);
  const secondPending = observed(h.context.updateMeasurement(7, 81, { polyp_count: 3 }));
  h.request('PATCH', '/api/boxes/7/measurements/81/').respond(box(7, 3).latest_measurement);
  await tick();
  h.request('GET', '/api/boxes/7/', 1).respond(box(7, 3));
  assert.equal((await secondPending).error, undefined);
  h.request('GET', '/api/boxes/7/').respond(box(7, 0));
  assert.equal((await first.pending).error, undefined, 'the first committed mutation remains a success');
  assert.equal(h.state.data.boxDetails[7].latest_measurement.polyp_count, 3);
  assert.equal(h.state.recovery, null);
});

for (const kind of ['wrapper', 'initial', 'recent', 'openBox']) {
  test(`${kind}: a queued pre-mutation publication is rechecked when React applies it`, async () => {
    const h = harness({ seed: kind === 'wrapper' });
    h.queueUpdates();
    let pending;
    if (kind === 'wrapper') pending = observed(h.context.refreshBoxAfterMeasurement(7));
    else if (kind === 'openBox') h.context.openBox(7, 'BOX-7');
    else h.effect(kind);
    h.request('GET', '/api/boxes/7/').respond(box());
    await tick();
    if (pending) assert.equal((await pending).error, undefined);
    assert.ok(h.queuedUpdates.length, 'the old read reached the real publication path');
    const mutation = observed(h.context.deactivateBox(7, {}));
    h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 0, { status: 'inactive' }));
    assert.equal((await mutation).error, undefined);
    // Apply the committed write first, then the previously queued stale read.
    const stale = h.queuedUpdates.shift();
    h.flush();
    const committed = h.state.data;
    h.state.data = stale(h.state.data);
    assert.equal(h.state.data, committed, 'queued stale updater must be a no-op, not merely restored later');
    assert.equal(h.state.data.boxDetails[7].status, 'inactive');
    assertZero(h);
    assert.equal(h.state.error, undefined);
  });
}

for (const kind of ['initial', 'openBox']) {
  test(`${kind}: delayed application error from a rejected read cannot publish after successful deactivation`, async () => {
    const h = harness({ seed: false });
    const applicationError = deferred();
    const transportError = new Error('Initial detail transport failed');
    const convertedErrors = [];
    const publishedErrors = [];
    h.context.getApplicationError = error => {
      convertedErrors.push(error);
      return applicationError.promise;
    };
    h.context.setError = error => {
      publishedErrors.push(error);
      h.state.error = error;
    };
    const cleanup = kind === 'initial' ? h.effect(kind) : h.context.openBox(7, 'BOX-7');
    h.request('GET', '/api/boxes/7/').reject(transportError);
    await tick();
    assert.deepEqual(convertedErrors, [transportError], 'read rejection reaches the real catch before the mutation');
    assert.equal(h.state.loading, true, 'error conversion remains pending across the mutation');
    const mutation = observed(h.context.deactivateBox(7, {}));
    const deactivated = box(7, 0, { status: 'inactive' });
    h.request('POST', '/api/boxes/7/deactivate/').respond(deactivated);
    assert.equal((await mutation).error, undefined);
    const committed = h.state.data;
    applicationError.resolve({ message: 'Obsolete detail error', code: 'old_detail_unavailable' });
    await tick();
    assert.deepEqual(publishedErrors, [], 'obsolete error must never reach setError after the conversion await');
    assert.equal(h.state.error, undefined);
    assert.equal(h.state.data, committed, 'successful deactivation remains unchanged');
    assert.deepEqual(structuredClone(h.state.data.boxDetails[7]), deactivated);
    assertZero(h);
    assert.equal(h.state.loading, false);
    assert.equal(h.state.recovery, null);
    if (cleanup) cleanup();
  });
}

for (const kind of ['initial', 'recent', 'openBox']) {
  for (const outcome of ['resolve', 'reject']) {
    test(`${kind}: obsolete initial ${outcome} after mutation cannot publish or show an error`, async () => {
      const h = harness({ seed: false });
      if (kind === 'openBox') h.context.openBox(7, 'BOX-7'); else h.effect(kind);
      const mutation = observed(h.context.deactivateBox(7, {}));
      h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 0, { status: 'inactive' }));
      assert.equal((await mutation).error, undefined);
      if (outcome === 'resolve') h.request('GET', '/api/boxes/7/').respond(box());
      else h.request('GET', '/api/boxes/7/').reject(new Error('obsolete initial failure'));
      await tick();
      assertZero(h);
      assert.equal(h.state.data.boxDetails[7].status, 'inactive');
      assert.equal(h.state.error, undefined);
      assert.equal(h.state.recovery, null);
      if (kind !== 'recent') assert.equal(h.state.loading, false);
    });
  }
}

for (const [name, args, path, extra] of [
  ['moveBox', [7, {}], '/api/boxes/7/move/', { thermal_zone: 2 }],
  ['deactivateBox', [7, {}], '/api/boxes/7/deactivate/', { status: 'inactive' }],
  ['reactivateBox', [7, {}], '/api/boxes/7/activate/', { status: 'active' }],
  ['qualifyBox', [7, {}], '/api/boxes/7/qualify/', { status: 'inactive' }],
  ['assignBoxInitialLocation', [7, {}], '/api/admin/box-inventory/7/assign-location/', { thermal_zone: 2 }],
  ['createBox', [{}], '/api/boxes/', { status: 'active' }],
]) {
  test(`${name}: returned detail invalidates the actual old detail fetch`, async () => {
    const h = harness();
    const old = observed(h.context.refreshBoxAfterMeasurement(7));
    const mutation = observed(h.context[name](...args));
    h.request('POST', path).respond(box(7, 0, extra));
    await tick();
    for (const request of h.fetches.filter(item => item.path.startsWith('/api/thermal-zones/'))) request.respond({ results: [], next: null });
    assert.equal((await mutation).error, undefined);
    h.request('GET', '/api/boxes/7/').respond(box());
    assertSuperseded(await old);
    assertZero(h);
    for (const [key, value] of Object.entries(extra)) assert.equal(h.state.data.boxDetails[7][key], value);
  });
}

test('movement correction invalidates detail before the separately requested movement refresh', async () => {
  const h = harness();
  const old = observed(h.context.refreshBoxAfterMeasurement(7));
  const payload = { expected_revision: 0, to_thermal_zone_id: 2 };
  const corrected = box(7, 0, { thermal_zone: { id: 2, name: 'Destination' },
    movements: [{ id: 91, revision: 1, to_thermal_zone: { id: 2, name: 'Destination' },
      moved_at: '2026-10-12T10:12:13.123456Z', notes: 'Corrected movement' }] });
  const mutation = observed(h.context.correctMovement(7, 91, payload));
  const patch = h.request('PATCH', '/api/boxes/7/movements/91/');
  assert.deepEqual(patch.payload, payload);
  patch.respond(corrected);
  assert.equal((await mutation).error, undefined);
  assert.deepEqual(structuredClone(h.state.data.boxDetails[7]), corrected, 'PATCH publishes the full returned detail before refresh');
  const fresh = observed(h.context.refreshMovementState(7));
  h.request('GET', '/api/boxes/7/', 1).respond(corrected);
  h.request('GET', '/api/thermal-zones/').respond({ results: [{ id: 2 }], next: null });
  assert.equal((await fresh).error, undefined);
  h.request('GET', '/api/boxes/7/').respond(box());
  assertSuperseded(await old);
  assertZero(h);
  assert.equal(h.state.data.boxDetails[7].movements[0].to_thermal_zone.id, 2);
});

test('delayed movement PATCH snapshot cannot overwrite newer committed zero, even when movement refresh fails', async () => {
  const h = harness();
  const movementState = {
    thermal_zone: { id: 2, name: 'Destination' },
    movements: [{ id: 91, revision: 1, to_thermal_zone: { id: 2, name: 'Destination' },
      moved_at: '2026-10-12T10:12:13.123456Z', notes: 'Saved correction' }],
  };
  const correction = observed(h.context.correctMovement(7, 91, { expected_revision: 0, to_thermal_zone_id: 2 }));
  const delayedPatch = h.request('PATCH', '/api/boxes/7/movements/91/');
  const measurement = await startMeasurement(h);
  h.request('GET', '/api/boxes/7/').respond(box(7, 0, movementState));
  assert.equal((await measurement.pending).error, undefined);
  assertZero(h);
  const committed = h.state.data;
  delayedPatch.respond(box(7, 10, movementState));
  assert.equal((await correction).error, undefined, 'concurrent committed movement correction still succeeds');
  assert.equal(h.state.data, committed, 'delayed full PATCH snapshot must not publish over the newer measurement');
  assertZero(h);
  const refresh = observed(h.context.refreshMovementState(7));
  h.request('GET', '/api/boxes/7/', 1).respond({ detail: 'Movement refresh unavailable' }, 503);
  h.request('GET', '/api/thermal-zones/').respond({ results: [{ id: 2 }], next: null });
  assert.equal((await refresh).error.status, 503);
  assert.equal(h.state.data.boxDetails, committed.boxDetails,
    'failed movement detail reconciliation preserves the already authoritative zero');
  assert.deepEqual(structuredClone(h.state.data.zones), [{ id: 2 }],
    'the independent complete zone refresh can still publish');
  assertZero(h);
  assert.equal(h.state.data.boxDetails[7].current_polyp_state.polyp_count, 0);
  assert.equal(h.state.recovery, null);
  assert.equal(h.fetches.filter(request => request.method === 'PATCH').length, 1);
  assert.equal(h.fetches.filter(request => request.method === 'POST').length, 1);
});

test('queued movement PATCH snapshot becomes a no-op after newer measurement zero and fresh detail publish', async () => {
  const h = harness();
  const movementState = {
    thermal_zone: { id: 2, name: 'Destination' },
    movements: [{ id: 91, revision: 1, to_thermal_zone: { id: 2, name: 'Destination' },
      moved_at: '2026-10-12T10:12:13.123456Z', notes: 'Saved correction' }],
  };
  const immediateSetData = h.context.setData;
  h.queueUpdates();
  const correction = observed(h.context.correctMovement(7, 91, { expected_revision: 0, to_thermal_zone_id: 2 }));
  h.request('PATCH', '/api/boxes/7/movements/91/').respond(box(7, 10, movementState));
  assert.equal((await correction).error, undefined);
  assert.equal(h.queuedUpdates.length, 1, 'successful movement snapshot reaches the real queued publication path');
  const queuedMovementSnapshot = h.queuedUpdates.shift();
  h.context.setData = immediateSetData;
  const measurement = await startMeasurement(h);
  h.request('GET', '/api/boxes/7/').respond(box(7, 0, movementState));
  assert.equal((await measurement.pending).error, undefined);
  assertZero(h);
  const committed = h.state.data;
  h.state.data = queuedMovementSnapshot(h.state.data);
  assert.equal(h.state.data, committed, 'queued movement updater must recheck its own committed revision');
  assertZero(h);
  assert.equal(h.state.data.boxDetails[7].current_polyp_state.polyp_count, 0);
  assert.equal(h.state.data.boxDetails[7].movements[0].revision, 1);
  assert.equal(h.state.recovery, null);
});

for (const failedRead of ['detail', 'zones']) {
  test(`movement correction preserves returned detail after ${failedRead} refresh failure; component retry is GET-only`, async () => {
    const h = harness();
    const corrected = box(7, 10, {
      thermal_zone: { id: 2, name: 'Destination' },
      current_location_started_at: '2026-10-12T10:12:13.123456Z',
      movements: [{ id: 91, revision: 1, to_thermal_zone: { id: 2, name: 'Destination' },
        moved_at: '2026-10-12T10:12:13.123456Z', notes: 'Saved correction', user: 'lab-author' }],
    });
    const save = observed(h.context.correctMovement(7, 91, { expected_revision: 0, to_thermal_zone_id: 2 }));
    h.request('PATCH', '/api/boxes/7/movements/91/').respond(corrected);
    assert.equal((await save).error, undefined);
    const committed = h.state.data;
    const firstRefresh = observed(h.context.refreshMovementState(7));
    h.request('GET', '/api/boxes/7/').respond(failedRead === 'detail' ? { detail: 'Read unavailable' } : corrected,
      failedRead === 'detail' ? 503 : 200);
    h.request('GET', '/api/thermal-zones/').respond(failedRead === 'zones' ? { detail: 'Zones unavailable' }
      : { results: [], next: null }, failedRead === 'zones' ? 503 : 200);
    assert.equal((await firstRefresh).error.status, 503, 'component receives the read failure separately from save success');
    assert.deepEqual(structuredClone(h.state.data), structuredClone(committed),
      'failed detail refresh may publish independent complete zones, but cannot discard the saved correction');
    assert.deepEqual(structuredClone(h.state.data.boxDetails[7]), corrected);
    assert.equal(h.state.recovery, null, 'movement refresh recovery belongs to the component, not App');
    const beforeRetry = h.fetches.length;
    // Invoke the same App onRefresh callback that the component retries after a saved correction.
    const retry = observed(h.context.refreshMovementState(7));
    h.request('GET', '/api/boxes/7/', 1).respond(corrected);
    h.request('GET', '/api/thermal-zones/', 1).respond({ results: [{ id: 2 }], next: null });
    assert.equal((await retry).error, undefined);
    assert.ok(h.fetches.slice(beforeRetry).every(request => request.method === 'GET'));
    assert.equal(h.fetches.filter(request => request.method === 'PATCH').length, 1, 'retry never resubmits the correction');
    assert.deepEqual(structuredClone(h.state.data.boxDetails[7]), corrected);
  });
}

test('movement detail resolved before a later mutation is superseded while zone pagination is pending', async () => {
  const h = harness();
  h.state.data.zones = [{ id: 99 }];
  const refresh = observed(h.context.refreshMovementState(7));
  h.request('GET', '/api/boxes/7/').respond(box());
  await tick();
  h.request('GET', '/api/thermal-zones/').respond({ results: [{ id: 1 }], next: '/api/thermal-zones/?offset=1' });
  await tick();
  const lastPage = h.request('GET', '/api/thermal-zones/?offset=1');
  const mutation = observed(h.context.deactivateBox(7, {}));
  h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 0, { status: 'inactive' }));
  assert.equal((await mutation).error, undefined);
  const committed = h.state.data;
  lastPage.respond({ results: [{ id: 2 }], next: null });
  assertSuperseded(await refresh);
  assert.equal(h.state.data, committed, 'neither stale detail nor completed zone pages may publish');
  assert.deepEqual(h.state.data.zones, [{ id: 99 }]);
  assertZero(h);
  assert.equal(h.state.recovery, null);
});

for (const order of ['superseded-first', 'sibling-first']) {
  test(`recent boxes publish independently when one read is superseded (${order})`, async () => {
    const h = harness({ seed: false });
    h.effect('recent', [7, 8]);
    const mutation = observed(h.context.deactivateBox(7, {}));
    h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 0, { status: 'inactive' }));
    assert.equal((await mutation).error, undefined);
    const finishSuperseded = async () => {
      h.request('GET', '/api/boxes/7/').respond(box());
      await tick();
      assertZero(h);
      assert.equal(h.context.requestedRecentBoxIdsRef.current.has(7), false, 'superseded recent box can be retried');
    };
    const finishSibling = async () => {
      h.request('GET', '/api/boxes/8/').respond(box(8, 12));
      await tick();
      assert.equal(h.state.data.boxes.find(item => item.id === 8)?.latest_measurement.polyp_count, 12,
        'unaffected sibling publishes without waiting for the superseded read');
      assert.equal(h.context.requestedRecentBoxIdsRef.current.has(8), true);
    };
    if (order === 'superseded-first') { await finishSuperseded(); await finishSibling(); }
    else { await finishSibling(); await finishSuperseded(); }
    assert.equal(h.state.data.boxDetails[7].status, 'inactive');
    assert.equal(h.state.error, undefined);
  });
}

test('subculture refresh superseded by a later measurement keeps both committed operations successful', async () => {
  const h = harness();
  const result = { children: [box(8, 4)], allocated_polyp_count: 4, parent_polyp_count_after: 6,
    allocations: [{ allocated_polyps: 4 }] };
  const subculture = observed(h.context.createSubculture(7, {}));
  h.request('POST', '/api/boxes/7/subcultures/').respond(result);
  await tick();
  assert.equal(h.state.data.boxDetails[7].current_polyp_state.polyp_count, 6);
  const measurement = await startMeasurement(h);
  h.request('GET', '/api/boxes/7/', 1).respond(box(7, 0));
  assert.equal((await measurement.pending).error, undefined);
  const committed = h.state.data;
  h.request('GET', '/api/boxes/7/').respond(box(7, 6));
  assert.deepEqual((await subculture).value, result);
  assert.equal(h.state.data, committed, 'obsolete subculture refresh cannot replace the later measurement');
  assertZero(h);
  assert.equal(h.state.data.boxes.find(item => item.id === 8).latest_measurement.polyp_count, 4);
  assert.equal(h.state.recovery, null, 'do not offer recovery for the superseded subculture refresh');
});

for (const kind of ['create', 'correction']) {
  test(`${kind}: scientific dates, authors, provenance and decimal precision survive provisional and refreshed publication`, async () => {
    const h = harness();
    const previous = box().latest_measurement;
    if (kind === 'create') previous.measured_on = '2026-10-05';
    h.state.data.boxDetails[7].biological_measurements = [previous];
    const measurement = {
      id: kind === 'create' ? 82 : 81, measured_on: '2026-10-12', polyp_count: 0,
      ephyrae_count: 0, strobila_count: null, salinity_psu: '32.125000',
      culture_status: 'stable', needs_attention: false, notes: 'Scientific observation',
      user: 'lab-author', user_identity: { first_name: 'Lab', last_name: 'Author', email: 'author@example.invalid' },
      created_at: '2026-10-12T10:12:13.123456Z', can_edit: true,
      edit_deadline: '2026-10-19T10:12:13.123456Z', edit_restriction: null,
    };
    const provenance = { kind: 'measurement', id: measurement.id, measured_on: measurement.measured_on,
      timestamp: measurement.created_at };
    const authoritative = box(7, 0, {
      created_on: '2026-09-28', entered_on: '2026-09-29', volume_liters: '1.250000',
      current_location_started_at: '2026-09-29T08:09:10.654321Z',
      latest_measurement: measurement,
      biological_measurements: kind === 'create' ? [measurement, previous] : [measurement],
      latest_salinity_psu: measurement.salinity_psu,
      current_polyp_state: { polyp_count: 0, revision: 'opaque-state-revision', source: provenance },
      biological_timeline: [{ kind: 'measurement', id: measurement.id, identity: `measurement:${measurement.id}`,
        effective_date: measurement.measured_on, state_sequence: 2, timestamp: measurement.created_at,
        author: { id: 4, username: 'lab-author' }, user_identity: measurement.user_identity,
        polyp_count_before: 10, polyp_count_after: 0, allocated_polyps: null, allocations: [], children: [],
        can_edit: true, measurement }],
    });
    const old = observed(h.context.refreshBoxAfterMeasurement(7));
    const payload = { measured_on: measurement.measured_on, polyp_count: 0, ephyrae_count: 0,
      strobila_count: null, salinity_psu: measurement.salinity_psu, notes: measurement.notes };
    const pending = observed(kind === 'create' ? h.context.createMeasurement(7, payload)
      : h.context.updateMeasurement(7, 81, payload));
    const mutation = h.request(kind === 'create' ? 'POST' : 'PATCH', kind === 'create'
      ? '/api/boxes/7/measurements/' : '/api/boxes/7/measurements/81/');
    assert.deepEqual(mutation.payload, payload, 'transport preserves zero, null and precision without coercion');
    mutation.respond(measurement);
    await tick();
    assert.deepEqual(structuredClone(h.state.data.boxDetails[7].latest_measurement), measurement,
      'provisional publication preserves the complete server measurement');
    assert.equal(h.state.data.boxDetails[7].latest_salinity_psu, '32.125000');
    h.request('GET', '/api/boxes/7/', 1).respond(authoritative);
    assert.deepEqual((await pending).value, measurement);
    h.request('GET', '/api/boxes/7/').respond(box());
    assertSuperseded(await old);
    assert.deepEqual(structuredClone(h.state.data.boxDetails[7]), authoritative,
      'authoritative scientific dates, microseconds, provenance and history remain exact');
    assert.deepEqual(structuredClone(h.state.data.boxes.find(item => item.id === 7)), authoritative);
  });
}

test('successful A mutation evicts only A detail sharing while B detail remains pending', async () => {
  const h = harness();
  const path = '/api/boxes/7/';
  const oldA = observed(h.context.refreshBoxAfterMeasurement(7));
  // Seed concurrent client scopes without organization lifetime eviction, isolating mutation scoping.
  const setStoredOrganization = id => h.context.window.localStorage.setItem('polypbase.activeOrganizationId', String(id));
  setStoredOrganization(2);
  const readB = observed(h.client.apiGet(path));
  setStoredOrganization(1);
  const mutation = observed(h.context.deactivateBox(7, {}));
  h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 0, { status: 'inactive' }));
  assert.equal((await mutation).error, undefined);
  const freshA = observed(h.context.refreshBoxAfterMeasurement(7));
  setStoredOrganization(2);
  const joinedB = observed(h.client.apiGet(path));
  setStoredOrganization(1);
  const detailFetches = h.fetches.filter(request => request.path === path);
  assert.deepEqual(detailFetches.map(request => request.organization), ['1', '2', '1'],
    'mutation starts fresh A detail but must not evict the pending B entry');
  detailFetches[2].respond(box(7, 0, { status: 'inactive' }));
  assert.equal((await freshA).error, undefined);
  detailFetches[1].respond(box(7, 22));
  assert.equal((await readB).value.latest_measurement.polyp_count, 22);
  assert.equal((await joinedB).value.latest_measurement.polyp_count, 22);
  detailFetches[0].respond(box());
  assertSuperseded(await oldA);
  assertZero(h);
});

test('subculture invalidates parent and returned children, but not unrelated boxes', async () => {
  const h = harness();
  const parent = observed(h.context.refreshBoxAfterMeasurement(7));
  const child = observed(h.context.refreshBoxAfterMeasurement(8));
  const unrelated = observed(h.context.refreshBoxAfterMeasurement(9));
  const mutation = observed(h.context.createSubculture(7, {}));
  h.request('POST', '/api/boxes/7/subcultures/').respond({ children: [box(8, 4)],
    allocated_polyp_count: 4, parent_polyp_count_after: 6, allocations: [{ allocated_polyps: 4 }] });
  await tick();
  h.request('GET', '/api/boxes/7/', 1).respond(box(7, 6));
  assert.equal((await mutation).error, undefined);
  h.request('GET', '/api/boxes/7/').respond(box());
  h.request('GET', '/api/boxes/8/').respond(box(8, 10));
  h.request('GET', '/api/boxes/9/').respond(box(9, 12));
  assertSuperseded(await parent);
  assertSuperseded(await child);
  assert.equal((await unrelated).error, undefined);
  assert.equal(h.state.data.boxDetails[7].current_polyp_state.polyp_count, 6);
  assert.equal(h.state.data.boxes.find(item => item.id === 8).latest_measurement.polyp_count, 4);
  assert.equal(h.state.data.boxDetails[9].current_polyp_state.polyp_count, 12);
});

test('batch qualification invalidates successes only, leaving failed boxes readable', async () => {
  const h = harness();
  const success = observed(h.context.refreshBoxAfterMeasurement(7));
  const failure = observed(h.context.refreshBoxAfterMeasurement(8));
  const mutation = observed(h.context.qualifyBoxesBatch({ box_ids: [7, 8] }));
  h.request('POST', '/api/admin/box-inventory/batch-qualify/').respond({ successes: [{ box_id: 7, status: 'inactive' }], errors: [{ box_id: 8 }] });
  assert.equal((await mutation).error, undefined);
  h.request('GET', '/api/boxes/7/').respond(box());
  h.request('GET', '/api/boxes/8/').respond(box(8));
  assertSuperseded(await success);
  assert.equal((await failure).error, undefined);
  assert.equal(h.state.data.boxDetails[7].status, 'inactive');
});

test('a rejected mutation does not supersede a still-valid detail read', async () => {
  const h = harness();
  const read = observed(h.context.refreshBoxAfterMeasurement(7));
  const mutation = observed(h.context.deactivateBox(7, {}));
  h.request('POST', '/api/boxes/7/deactivate/').respond({ detail: 'forbidden' }, 403);
  assert.equal((await mutation).error.status, 403);
  h.request('GET', '/api/boxes/7/').respond(box(7, 12));
  assert.equal((await read).error, undefined);
  assert.equal(h.state.data.boxDetails[7].current_polyp_state.polyp_count, 12);
});

test('A -> B -> A starts a new A fetch and rejects the original App generation', async () => {
  const h = harness();
  const oldA = observed(h.context.refreshBoxAfterMeasurement(7));
  h.switchTo(2);
  const readB = observed(h.context.refreshBoxAfterMeasurement(7));
  h.switchTo(1);
  const freshA = observed(h.context.refreshBoxAfterMeasurement(7));
  assert.deepEqual(h.fetches.map(item => item.organization), ['1', '2', '1']);
  h.request('GET', '/api/boxes/7/', 2).respond(box(7, 0));
  assert.equal((await freshA).error, undefined);
  h.request('GET', '/api/boxes/7/').respond(box());
  h.request('GET', '/api/boxes/7/', 1).respond(box(7, 22));
  assert.ok((await oldA).error instanceof h.client.ApiResourceCancelledError);
  assert.ok((await readB).error instanceof h.client.ApiResourceCancelledError);
  assertZero(h);
});

for (const outcome of ['resolve', 'reject']) {
  test(`client cleanup: old ${outcome} cannot remove a newer shared entry`, async () => {
    const h = harness();
    const old = observed(h.client.apiGet('/api/boxes/7/'));
    h.client.invalidateInFlightGet('/api/boxes/7/', 1);
    const fresh = observed(h.client.apiGet('/api/boxes/7/'));
    if (outcome === 'resolve') h.fetches[0].respond(box()); else h.fetches[0].reject(new Error('old failure'));
    await old;
    const joined = observed(h.client.apiGet('/api/boxes/7/'));
    assert.equal(h.fetches.length, 2);
    h.fetches[1].respond(box(7, 0));
    assert.equal((await fresh).value.latest_measurement.polyp_count, 0);
    assert.equal((await joined).value.latest_measurement.polyp_count, 0);
  });
}

test('GET sharing honors organization, global context and opt-out request options', async () => {
  const h = harness();
  const path = '/api/boxes/7/';
  const pending = [observed(h.client.apiGet(path)), observed(h.client.apiGet(path))];
  assert.equal(h.fetches.length, 1);
  for (const options of [{ headers: { Accept: 'application/json' } }, { signal: new AbortController().signal }, { cache: 'no-store' }]) {
    pending.push(observed(h.client.apiGet(path, options)), observed(h.client.apiGet(path, options)));
  }
  assert.equal(h.fetches.length, 7, 'custom options never share');
  pending.push(observed(h.client.apiGet(path, { skipOrganizationContext: true })));
  h.switchTo(2);
  pending.push(observed(h.client.apiGet(path, { skipOrganizationContext: true })));
  assert.equal(h.fetches.length, 8, 'global requests share independently of organization switches');
  assert.equal(h.fetches[7].organization, null);
  pending.push(observed(h.client.apiGet(path)));
  assert.equal(h.fetches.length, 9);
  assert.equal(h.fetches[8].organization, '2');
  h.fetches.forEach(request => request.respond(box()));
  assert.ok((await Promise.all(pending)).every(result => !result.error));
});

test('scoped invalidation leaves another organization and global entry intact; omitted scope invalidates all', async () => {
  const h = harness();
  const path = '/api/account/invitations/';
  const pending = [];
  const get = options => pending.push(observed(h.client.apiGet(path, options)));
  get();
  h.switchTo(2);
  get();
  get({ skipOrganizationContext: true });
  h.client.invalidateInFlightGet(path, 1);
  get();
  get({ skipOrganizationContext: true });
  assert.equal(h.fetches.length, 3, 'scoped invalidation preserves B and global sharing');
  h.switchTo(1);
  get();
  assert.equal(h.fetches.length, 4, 'scoped invalidation evicts A');
  h.client.invalidateInFlightGet(path);
  get();
  get({ skipOrganizationContext: true });
  h.switchTo(2);
  get();
  assert.equal(h.fetches.length, 7, 'legacy no-argument scope evicts every organization and global entry');
  h.fetches.forEach(request => request.respond({ results: [] }));
  assert.ok((await Promise.all(pending)).every(result => !result.error));
});

function seedBox(h, detail) {
  h.state.data.boxDetails[detail.id] = detail;
  h.state.data.boxes.push(detail);
}
function subcultureResult(children) {
  return { children, allocated_polyp_count: 4, parent_polyp_count_after: 6,
    allocations: children.map(child => ({ child, allocated_polyps: 4 })) };
}

test('three overlapping operations on one Box supersede reconciliation without stale publication or recovery', async () => {
  const h = harness();
  const server = serverLedger();
  const first = observed(h.context.deactivateBox(7, {}));
  const second = observed(h.context.qualifyBox(7, {}));
  const third = observed(h.context.reactivateBox(7, {}));
  const deliverFirst = server.commit(h.request('POST', '/api/boxes/7/deactivate/'), box(7, 5, { status: 'inactive' }));
  const deliverSecond = server.commit(h.request('POST', '/api/boxes/7/qualify/'), box(7, 3, { status: 'inactive' }));
  const deliverThird = server.commit(h.request('POST', '/api/boxes/7/activate/'), box(7, 0));
  deliverThird();
  assert.equal((await third).error, undefined);
  const committed = h.state.data;
  deliverSecond();
  await tick();
  assert.equal(h.state.data, committed);
  deliverFirst();
  await tick();
  assert.equal(h.state.data, committed);
  assert.equal(h.fetches.filter(item => item.path === '/api/boxes/7/').length, 2, 'every success invalidates read sharing');
  h.request('GET', '/api/boxes/7/').respond(box(7, 3));
  assert.equal((await second).error, undefined, 'superseded reconciliation is still a successful write');
  assert.equal(h.state.recovery, null);
  h.request('GET', '/api/boxes/7/', 1).respond(server.detail());
  assert.equal((await first).error, undefined);
  assertBox(h, server.detail());
});

test('different Boxes publish independently despite reverse mutation responses and an unrelated boundary', async () => {
  const h = harness();
  seedBox(h, box(8));
  const a = observed(h.context.deactivateBox(7, {}));
  const b = observed(h.context.deactivateBox(8, {}));
  const authoritativeB = box(8, 0, { status: 'inactive' });
  h.request('POST', '/api/boxes/8/deactivate/').respond(authoritativeB);
  assert.equal((await b).error, undefined);
  const authoritativeA = box(7, 5, { status: 'inactive' });
  h.request('POST', '/api/boxes/7/deactivate/').respond(authoritativeA);
  assert.equal((await a).error, undefined);
  assertBox(h, authoritativeA);
  assertBox(h, authoritativeB);
  assert.equal(h.fetches.filter(item => item.method === 'GET').length, 0, 'B does not force reconciliation for A');
});

for (const phase of ['mutation', 'reconciliation']) {
  test(`organization switch while ${phase} is pending cannot publish or schedule old recovery`, async () => {
    const h = harness();
    const delayed = observed(h.context.deactivateBox(7, {}));
    if (phase === 'reconciliation') {
      const newer = observed(h.context.reactivateBox(7, {}));
      h.request('POST', '/api/boxes/7/activate/').respond(box(7, 0));
      assert.equal((await newer).error, undefined);
      h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 5, { status: 'inactive' }));
      await tick();
      assertZero(h);
      assert.equal(h.request('GET', '/api/boxes/7/').organization, '1');
    }
    h.switchTo(2);
    h.state.data = { ...h.state.data, boxes: [box(7, 42)], boxDetails: { 7: box(7, 42) } };
    const destination = h.state.data;
    if (phase === 'mutation') h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 5, { status: 'inactive' }));
    else h.request('GET', '/api/boxes/7/').respond({ detail: 'old organization unavailable' }, 503);
    assert.equal((await delayed).error?.constructor.name, 'ApiResourceCancelledError');
    assert.equal(h.state.data, destination);
    assert.equal(h.state.recovery, null);
    assert.equal(h.fetches.filter(item => item.method === 'GET').length, phase === 'mutation' ? 0 : 1);
  });
}

for (const changed of ['parent', 'child', 'both']) {
  test(`subculture: ${changed} already mutated is guarded independently with parent/child integrity`, async () => {
    const h = harness();
    seedBox(h, box(8));
    const pending = observed(h.context.createSubculture(7, {}));
    const parent = box(7, 0, { notes: 'authoritative parent', biological_timeline: [{ identity: 'parent-zero' }] });
    const child = box(8, 0, { status: 'inactive', thermal_zone: null,
      movements: [{ id: 19, revision: 3 }], notes: 'authoritative child',
      current_polyp_state: { polyp_count: 0, revision: 'child-zero', source: 'measurement' } });
    if (changed !== 'child') {
      const mutation = observed(h.context.qualifyBox(7, {}));
      h.request('POST', '/api/boxes/7/qualify/').respond(parent);
      assert.equal((await mutation).error, undefined);
    }
    if (changed !== 'parent') {
      const mutation = observed(h.context.deactivateBox(8, {}));
      h.request('POST', '/api/boxes/8/deactivate/').respond(child);
      assert.equal((await mutation).error, undefined);
    }
    const staleChild = box(8, 4, { notes: 'child at subculture commit' });
    const result = subcultureResult([staleChild]);
    h.request('POST', '/api/boxes/7/subcultures/').respond(result);
    await tick();
    if (changed !== 'child') assertBox(h, parent);
    else assert.equal(h.state.data.boxDetails[7].current_polyp_state.polyp_count, 6, 'safe parent transition is independent of unsafe child');
    if (changed !== 'parent') assertBox(h, child);
    else assert.deepEqual(structuredClone(h.state.data.boxes.find(item => item.id === 8)), staleChild, 'safe child publishes despite unsafe parent');
    assert.equal(h.fetches.filter(item => item.path === '/api/boxes/8/').length, changed === 'parent' ? 0 : 1);
    h.request('GET', '/api/boxes/7/').respond(parent);
    if (changed !== 'parent') h.request('GET', '/api/boxes/8/').respond(child);
    assert.deepEqual(structuredClone((await pending).value), result, 'committed subculture remains successful');
    assertBox(h, parent);
    if (changed !== 'parent') assertBox(h, child);
    assert.equal(h.state.recovery, null);
  });
}

test('batch partial success guards each Box, reconciles only unsafe successes and preserves failed Box reads', async () => {
  const h = harness();
  seedBox(h, box(8));
  seedBox(h, box(9));
  const failedRead = observed(h.context.refreshBoxAfterMeasurement(9));
  const batch = observed(h.context.qualifyBoxesBatch({ box_ids: [7, 8, 9] }));
  const newer = observed(h.context.reactivateBox(7, {}));
  const authoritative = box(7, 0, { notes: 'newer successful write' });
  h.request('POST', '/api/boxes/7/activate/').respond(authoritative);
  assert.equal((await newer).error, undefined);
  const failedDetail = h.state.data.boxDetails[9];
  const result = { successes: [{ box_id: 7, status: 'inactive' }, { box_id: 8, status: 'inactive' }],
    errors: [{ box_id: 9, detail: 'qualification rejected' }] };
  h.request('POST', '/api/admin/box-inventory/batch-qualify/').respond(result);
  await tick();
  assertBox(h, authoritative);
  assert.equal(h.state.data.boxDetails[8].status, 'inactive');
  assert.equal(h.state.data.boxDetails[8].thermal_zone, null);
  assert.equal(h.state.data.boxDetails[9], failedDetail);
  assert.deepEqual(h.fetches.filter(item => item.method === 'GET').map(item => item.path), ['/api/boxes/9/', '/api/boxes/7/']);
  h.request('GET', '/api/boxes/9/').respond(box(9, 12));
  assert.equal((await failedRead).error, undefined);
  h.request('GET', '/api/boxes/7/').respond(authoritative);
  assert.deepEqual(structuredClone((await batch).value), result);
  assertBox(h, authoritative);
  assertBox(h, box(9, 12));
  assert.equal(h.state.recovery, null);
});

for (const overlap of [false, true]) {
  test(`create unknown result ID: ${overlap ? 'intervening lifecycle success requires GET' : 'unrelated Box boundary leaves publication safe'}`, async () => {
    const h = harness();
    // The result ID is not present in App data and cannot be known at dispatch.
    const create = observed(h.context.createBox({}));
    const id = overlap ? 8 : 7;
    const lifecycle = observed(h.context.deactivateBox(id, {}));
    const authoritative = box(id, 0, { status: 'inactive', thermal_zone: null });
    h.request('POST', `/api/boxes/${id}/deactivate/`).respond(authoritative);
    assert.equal((await lifecycle).error, undefined);
    const before = h.state.data;
    const created = box(8, 4, { notes: 'creation snapshot' });
    h.request('POST', '/api/boxes/').respond(created);
    await tick();
    if (overlap) {
      assert.equal(h.state.data, before, 'unknown ID must use the dispatch-time revision snapshot');
      h.request('GET', '/api/boxes/8/').respond(authoritative);
    }
    assert.deepEqual(structuredClone((await create).value), created);
    assertBox(h, overlap ? authoritative : created);
    assert.equal(h.fetches.filter(item => item.method === 'GET').length, overlap ? 1 : 0);
  });
}

test('create unknown result ID with an existing dispatch revision can publish when that revision is unchanged', async () => {
  const h = harness();
  const earlier = observed(h.context.deactivateBox(8, {}));
  h.request('POST', '/api/boxes/8/deactivate/').respond(box(8, 0, { status: 'inactive' }));
  assert.equal((await earlier).error, undefined);
  delete h.state.data.boxDetails[8];
  h.state.data.boxes = h.state.data.boxes.filter(item => item.id !== 8);
  const create = observed(h.context.createBox({}));
  const created = box(8, 4);
  h.request('POST', '/api/boxes/').respond(created);
  assert.equal((await create).error, undefined);
  assertBox(h, created);
  assert.equal(h.fetches.filter(item => item.method === 'GET').length, 0);
});

for (const [name, path] of [
  ['moveBox', '/api/boxes/7/move/'],
  ['assignBoxInitialLocation', '/api/admin/box-inventory/7/assign-location/'],
]) {
  for (const failureOrder of ['before-measurement', 'after-measurement']) {
    test(`${name}: side-zone recovery survives same-Box measurement boundary (${failureOrder})`, async () => {
      const h = harness();
      const zonesPath = '/api/thermal-zones/?limit=80';
      const previousZones = [{ id: 1, name: 'Previous zone snapshot' }];
      h.state.data.zones = previousZones;
      const fullSnapshot = box(7, 10, { thermal_zone: 2, notes: 'safe full mutation snapshot' });
      let fullCompleted = false;
      const full = observed(h.context[name](7, {})).then(result => { fullCompleted = true; return result; });
      h.request('POST', path).respond(fullSnapshot);
      await tick();
      assertBox(h, fullSnapshot);
      assert.equal(fullCompleted, false, 'successful full response still awaits the side-zone read');
      assert.equal(h.fetches.filter(item => item.path === '/api/boxes/7/').length, 0, 'safe full response needs no detail read');
      const zoneRequest = h.request('GET', zonesPath);
      let recovery;
      if (failureOrder === 'before-measurement') {
        zoneRequest.respond({ detail: 'temporary zone read failure' }, 503);
        assert.equal((await full).error, undefined, 'zone failure does not undo the successful mutation');
        recovery = h.state.recovery;
        assert.equal(typeof recovery, 'function');
      }

      const { pending: measurement } = await startMeasurement(h);
      assert.equal(h.fetches.filter(item => item.path === zonesPath).length, 1, 'measurement refreshes detail, not zones');
      if (failureOrder === 'before-measurement') {
        assert.equal(h.state.recovery, recovery, 'measurement commit must not prune organization-fresh side-zone recovery');
      } else {
        assert.equal(h.state.recovery, null);
        assert.equal(fullCompleted, false, 'original zone request remains pending across the Box boundary');
      }
      const authoritative = box(7, 0, { thermal_zone: 2, notes: 'newer measurement detail',
        biological_timeline: [{ identity: 'measurement-zero', polyp_count_after: 0 }],
        movements: [{ id: 19, revision: 3, notes: 'authoritative movement' }] });
      h.request('GET', '/api/boxes/7/').respond(authoritative);
      assert.equal((await measurement).error, undefined);
      assertBox(h, authoritative);
      assert.equal(h.state.data.zones, previousZones, 'failed or pending zone read leaves existing zones intact');

      if (failureOrder === 'after-measurement') {
        zoneRequest.respond({ detail: 'zone failure after newer Box boundary' }, 503);
        assert.equal((await full).error, undefined);
        recovery = h.state.recovery;
        assert.equal(typeof recovery, 'function', 'obsolete Box boundary must not suppress side-zone recovery');
      }
      assert.equal(h.state.recovery, recovery);
      assertBox(h, authoritative);
      const beforeRetry = h.fetches.length;
      const detailBeforeRetry = h.state.data.boxDetails[7];
      const itemBeforeRetry = h.state.data.boxes.find(item => item.id === 7);
      const retry = observed(recovery());
      await tick();
      assert.deepEqual(h.fetches.slice(beforeRetry).map(request => [request.method, request.path]),
        [['GET', zonesPath]], 'side-resource retry requests zones only; no Box GET or mutation replay');
      const freshZones = [{ id: 2, name: 'Recovered zone snapshot' }];
      h.request('GET', zonesPath, 1).respond({ results: freshZones, next: null });
      assert.equal((await retry).error, undefined);
      assert.deepEqual(structuredClone(h.state.data.zones), freshZones);
      assert.equal(h.state.data.boxDetails[7], detailBeforeRetry, 'zone recovery never reapplies the old full Box snapshot');
      assert.equal(h.state.data.boxes.find(item => item.id === 7), itemBeforeRetry);
      assertBox(h, authoritative);
      assertZero(h);
      assert.equal(h.state.recovery, null);
      assert.equal(h.fetches.filter(item => item.method !== 'GET').length, 2, 'each mutation is dispatched exactly once');
      assert.equal(h.fetches.filter(item => item.path === '/api/boxes/7/').length, 1, 'only measurement performs the detail GET');
    });
  }
}

for (const boundary of ['none', 'before-retry', 'during-retry']) {
  test(`unsafe successful full mutation recovery is GET-only (${boundary})`, async () => {
    const h = harness();
    const delayed = observed(h.context.deactivateBox(7, {}));
    const newer = observed(h.context.reactivateBox(7, {}));
    const authoritative = box(7, 0, { notes: 'latest server snapshot' });
    h.request('POST', '/api/boxes/7/activate/').respond(authoritative);
    assert.equal((await newer).error, undefined);
    h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 5, { status: 'inactive' }));
    await tick();
    assertBox(h, authoritative);
    h.request('GET', '/api/boxes/7/').respond({ detail: 'temporary reconciliation failure' }, 503);
    assert.equal((await delayed).error, undefined, 'successful write is not converted into refresh failure');
    assertBox(h, authoritative);
    const recovery = h.state.recovery;
    assert.equal(typeof recovery, 'function');
    const advanceBoundary = async () => {
      const next = observed(h.context.qualifyBox(7, {}));
      h.request('POST', '/api/boxes/7/qualify/').respond(box(7, 0, { status: 'inactive', notes: 'newest boundary' }));
      assert.equal((await next).error, undefined);
    };
    if (boundary === 'before-retry') {
      await advanceBoundary();
      assert.equal(h.state.recovery, null, 'newer Box boundary immediately prunes obsolete detail recovery metadata');
    }
    const beforeRetry = h.fetches.length;
    const retry = observed(recovery());
    await tick();
    if (boundary !== 'before-retry') {
      assert.equal(h.fetches.length, beforeRetry + 1);
      assert.equal(h.fetches.at(-1).method, 'GET', 'retry never replays the successful POST');
      if (boundary === 'during-retry') await advanceBoundary();
      const committed = h.state.data;
      h.request('GET', '/api/boxes/7/', 1).respond(authoritative);
      assert.equal((await retry).error, undefined);
      if (boundary === 'during-retry') assert.equal(h.state.data, committed, 'obsolete recovery GET cannot overwrite newer mutation');
      else assertBox(h, authoritative);
    } else {
      assert.equal((await retry).error, undefined);
      assert.equal(h.fetches.length, beforeRetry, 'obsolete callback sends no GET');
      assert.equal(h.state.data.boxDetails[7].notes, 'newest boundary');
    }
    assert.equal(h.state.recovery, null);
    assert.equal(h.fetches.filter(item => item.method !== 'GET').length, boundary === 'none' ? 2 : 3);
  });
}

// Lifecycle fixtures execute App code, not switchTo's synthetic generation change.
// Only unrelated profile/dashboard bootstrap work is stubbed; Box traffic always
// goes through the production client and the deferred fetch transport above.
function installRevisionLifecycle(h) {
  const originalSetData = h.context.setData;
  h.context.setData = update => {
    if (typeof update === 'function') originalSetData(update);
    else h.state.data = update;
    h.context.data = h.state.data;
  };
  h.state.data.profile = { id: 1, interface_language: 'en' };
  Object.assign(h.context, {
    data: h.state.data, needsOrganizationChoice: false,
    isBoxRoute: false, isZoneRoute: false, activeTab: 'pilotage',
    setIsOrganizationMenuOpen() {}, setIsCreateBoxOpen() {},
    setQrLabelSelection() {}, setMeasurementPrefill() {}, setExportOptionsRequested() {},
    setRecentBoxIds(ids) { h.state.recentIds = ids; },
    setNeedsOrganizationChoice(value) { h.context.needsOrganizationChoice = value; },
    setIsLoading(value) { h.state.loading = value; },
    setProfileActiveOrganization: (profile, organization) => ({ ...profile, organization }),
    setStoredInterfaceLanguage() {}, getSelectableOrganizations: () => [{ id: 1 }, { id: 2 }],
    resolveActiveOrganizationId: () => h.context.activeOrganizationId,
    getRecentBoxIds: () => [],
    async fetchScopedData(profile, organization) {
      h.client.setActiveOrganizationContext(organization);
      return { boxes: [], boxDetails: {}, zones: [], dashboard: null, overview: null,
        zoneRead: { isCurrent: () => true },
        exportOptions: null, profile: { ...profile, organization } };
    },
  });
  const lifecycleText = text => disableRevisionCleanup
    ? text.replaceAll(/boxDetailRevisionsRef\.current\.clear\(\);/g, '') : text;
  for (const name of ['chooseOrganization', 'logoutCurrentUser']) {
    h.evaluate(lifecycleText(appFunction(name).getText(appAst)));
  }
  const bootstrap = findNode(node => ts.isCallExpression(node)
    && node.expression.getText(appAst) === 'useEffect'
    && ts.isArrowFunction(node.arguments[0])
    && node.arguments[0].getText(appAst).includes("'/api/profile/'"))?.arguments[0];
  assert.ok(bootstrap, 'Expected the actual App profile bootstrap effect');
  return {
    bootstrap: () => h.evaluate(`(${lifecycleText(bootstrap.getText(appAst))})()`),
    async select(organization) {
      const pending = h.context.chooseOrganization(organization);
      // Selection must release old lifetime entries before any awaited work.
      assert.equal(h.context.boxDetailRevisionsRef.current.size, 0, 'revision cleanup is synchronous on real selection');
      await pending;
      assert.equal(h.state.error, null, 'real selection completes without fixture errors');
      assert.equal(h.context.activeOrganizationId, organization);
      // Model the render following the real selection, without altering revisions.
      h.context.operationGeneration = h.context.organizationRequestGenerationRef.current;
    },
  };
}

function populateRevisions(h, ids = [7, 8, 9]) {
  for (const id of ids) h.context.invalidateBoxDetail(id);
  assert.ok(h.context.boxDetailRevisionsRef.current.size >= ids.length);
}

test('revision cleanup: repeated real organization selections release every previous lifetime', async () => {
  const h = harness();
  const lifecycle = installRevisionLifecycle(h);
  const map = h.context.boxDetailRevisionsRef.current;
  for (const organization of [2, 1, 2, 1, 2, 1]) {
    populateRevisions(h);
    const generation = h.context.organizationRequestGenerationRef.current;
    await lifecycle.select(organization);
    assert.equal(h.context.organizationRequestGenerationRef.current, generation + 1);
    assert.equal(h.context.boxDetailRevisionsRef.current, map, 'reuse the map rather than retaining old lifetime maps');
    assert.equal(map.size, 0);
  }
  populateRevisions(h);
  const generation = h.context.organizationRequestGenerationRef.current;
  const size = map.size;
  await h.context.chooseOrganization(1);
  assert.equal(h.context.organizationRequestGenerationRef.current, generation, 'selecting the active organization is a no-op');
  assert.equal(map.size, size, 'a no-op selection preserves current resource guards');
});

for (const phase of ['mutation', 'reconciliation']) {
  test(`revision cleanup: real A B A cancels pending ${phase}, GETs and deferred publication`, async () => {
    const h = harness();
    const lifecycle = installRevisionLifecycle(h);
    populateRevisions(h);
    const queuedRead = observed(h.context.refreshBoxAfterMeasurement(8));
    h.queueUpdates();
    h.request('GET', '/api/boxes/8/').respond(box(8, 3));
    assert.equal((await queuedRead).error, undefined);
    assert.ok(h.queuedUpdates.length > 0, 'an actual GET publication is deferred');
    const oldRead = observed(h.context.refreshBoxAfterMeasurement(7));
    const oldMutation = observed(h.context.deactivateBox(7, {}));
    if (phase === 'reconciliation') {
      // Give the later operation its publication immediately, then delay the
      // unsafe first operation's authoritative reconciliation across selection.
      h.context.setData = update => { h.state.data = typeof update === 'function' ? update(h.state.data) : update; };
      const newer = observed(h.context.reactivateBox(7, {}));
      h.request('POST', '/api/boxes/7/activate/').respond(box(7, 0));
      assert.equal((await newer).error, undefined);
      h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 5));
      await tick();
      h.context.data = h.state.data;
    } else {
      // Lifecycle setData accepts objects even while old React updaters remain queued.
      h.context.setData = update => { h.state.data = typeof update === 'function' ? update(h.state.data) : update; h.context.data = h.state.data; };
    }
    await lifecycle.select(2);
    const bRead = observed(h.context.refreshBoxAfterMeasurement(7));
    const bMutation = observed(h.context.qualifyBox(7, {}));
    populateRevisions(h);
    await lifecycle.select(1);
    const fresh = observed(h.context.refreshBoxAfterMeasurement(7));
    const reads = h.fetches.filter(item => item.path === '/api/boxes/7/' && item.method === 'GET');
    assert.deepEqual(reads.map(item => item.organization), phase === 'mutation' ? ['1', '2', '1'] : ['1', '1', '2', '1']);
    const destination = box(7, 42, { notes: 'new A lifetime' });
    reads.at(-1).respond(destination);
    assert.equal((await fresh).error, undefined);
    const committed = h.state.data;
    reads[0].respond(box(7, 5));
    if (phase === 'reconciliation') reads[1].respond({ detail: 'old reconciliation unavailable' }, 503);
    else h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 5));
    reads.at(-2).respond(box(7, 6));
    h.request('POST', '/api/boxes/7/qualify/').respond(box(7, 6));
    for (const pending of [oldRead, oldMutation, bRead, bMutation]) {
      assert.equal((await pending).error?.constructor.name, 'ApiResourceCancelledError');
    }
    h.flush();
    assert.equal(h.state.data, committed, 'old A and B responses and deferred GET updaters cannot publish into new A');
    assertBox(h, destination);
    assert.equal(h.state.recovery, null, 'cancelled reconciliation cannot install recovery');
    assert.equal(h.context.boxDetailRevisionsRef.current.size, 0, 'late completions cannot recreate cleared generation entries');
  });
}

test('revision cleanup: successful actual logout clears synchronously after POST, not before success', async () => {
  const h = harness();
  installRevisionLifecycle(h);
  populateRevisions(h);
  const map = h.context.boxDetailRevisionsRef.current;
  const size = map.size;
  const generation = h.context.organizationRequestGenerationRef.current;
  const failed = observed(h.context.logoutCurrentUser());
  assert.equal(map.size, size);
  h.request('POST', '/api/auth/logout/').respond({ detail: 'logout rejected' }, 503);
  assert.ok((await failed).error);
  assert.equal(map.size, size);
  assert.equal(h.context.organizationRequestGenerationRef.current, generation);
  const read = observed(h.context.refreshBoxAfterMeasurement(7));
  const mutation = observed(h.context.deactivateBox(7, {}));
  const logout = observed(h.context.logoutCurrentUser());
  assert.equal(map.size, size, 'pending logout leaves active lifetime guards intact');
  h.request('POST', '/api/auth/logout/', 1).respond(null);
  assert.equal((await logout).error, undefined);
  assert.equal(map.size, 0);
  assert.equal(h.context.organizationRequestGenerationRef.current, generation + 1);
  assert.equal(h.browser.path, '/login');
  assert.equal(h.state.data.profile, null);
  const destination = h.state.data;
  h.request('GET', '/api/boxes/7/').respond(box(7, 5));
  h.request('POST', '/api/boxes/7/deactivate/').respond(box(7, 5));
  assert.equal((await read).error?.constructor.name, 'ApiResourceCancelledError');
  assert.equal((await mutation).error?.constructor.name, 'ApiResourceCancelledError');
  assert.equal(h.state.data, destination);
  assert.equal(map.size, 0);
  assert.equal(h.state.recovery, null);
});

for (const order of ['old-first', 'new-first']) {
  test(`logout session boundary: same institution starts a fresh detail GET (${order})`, async () => {
    const h = harness();
    const lifecycle = installRevisionLifecycle(h);
    h.evaluate(appFunction('handleAuthenticated').getText(appAst));
    const oldRead = observed(h.context.refreshBoxAfterMeasurement(7));
    const oldRequest = h.request('GET', '/api/boxes/7/');
    const logout = observed(h.context.logoutCurrentUser());
    assert.equal(h.client.getStoredActiveOrganizationId(), 1, 'pending logout retains the session context');
    h.request('POST', '/api/auth/logout/').respond(null);
    assert.equal((await logout).error, undefined);
    assert.equal(h.client.getStoredActiveOrganizationId(), null, 'successful logout clears the API context');

    // Model successful Session B authentication, then run the actual profile
    // bootstrap and explicit organization selection while Session A's GET waits.
    h.context.handleAuthenticated();
    h.context.resolveActiveOrganizationId = () => null;
    const cleanup = lifecycle.bootstrap();
    h.request('GET', '/api/profile/').respond({ id: 2, interface_language: 'en' });
    await tick();
    assert.equal(h.context.needsOrganizationChoice, true);
    assert.equal(h.state.data.profile.id, 2);
    await lifecycle.select(1);
    const freshRead = observed(h.context.refreshBoxAfterMeasurement(7));
    const freshRequest = h.request('GET', '/api/boxes/7/', 1);
    assert.notEqual(freshRequest, oldRequest, 'same institution and URL must start a new network request');
    assert.equal(freshRequest.organization, '1');
    const authoritative = box(7, 0, { notes: 'Session B authoritative detail' });
    authoritative.latest_measurement.salinity_psu = null;
    assert.equal(Object.hasOwn(authoritative.latest_measurement, 'strobila_count'), false);
    const finishOld = async () => {
      const current = h.state.data;
      oldRequest.respond(box(7, 99, { notes: 'Session A stale detail' }));
      assert.equal((await oldRead).error?.constructor.name, 'ApiResourceCancelledError');
      assert.equal(h.state.data, current, 'Session A cannot publish into Session B');
    };
    if (order === 'old-first') await finishOld();
    // Old completion must not remove the replacement GET's sharing entry.
    const joinedRead = observed(h.context.refreshBoxAfterMeasurement(7));
    assert.equal(h.fetches.filter(item => item.path === '/api/boxes/7/').length, 2);
    freshRequest.respond(authoritative);
    assert.equal((await freshRead).error, undefined);
    assert.equal((await joinedRead).error, undefined);
    if (order === 'new-first') await finishOld();
    assertBox(h, authoritative);
    assertZero(h);
    assert.equal(h.state.data.boxDetails[7].latest_measurement.salinity_psu, null);
    assert.equal(Object.hasOwn(h.state.data.boxDetails[7].latest_measurement, 'strobila_count'), false);
    assert.equal(h.state.recovery, null);
    cleanup();
  });
}

test('failed logout preserves the API context and sharing of a still-valid pending detail GET', async () => {
  const h = harness();
  installRevisionLifecycle(h);
  const generation = h.context.organizationRequestGenerationRef.current;
  const profile = h.state.data.profile;
  const read = observed(h.context.refreshBoxAfterMeasurement(7));
  const logout = observed(h.context.logoutCurrentUser());
  h.request('POST', '/api/auth/logout/').respond({ detail: 'logout rejected' }, 503);
  assert.equal((await logout).error?.status, 503);
  assert.equal(h.client.getStoredActiveOrganizationId(), 1);
  assert.equal(h.context.organizationRequestGenerationRef.current, generation);
  assert.equal(h.state.data.profile, profile);
  assert.notEqual(h.browser.path, '/login');
  const joined = observed(h.context.refreshBoxAfterMeasurement(7));
  assert.equal(h.fetches.filter(item => item.path === '/api/boxes/7/').length, 1);
  const authoritative = box(7, 0);
  h.request('GET', '/api/boxes/7/').respond(authoritative);
  assert.equal((await read).error, undefined);
  assert.equal((await joined).error, undefined);
  assertBox(h, authoritative);
  assertZero(h);
});

test('revision cleanup: actual bootstrap effect clears at entry and its cleanup blocks late profile publication', async () => {
  const h = harness();
  const lifecycle = installRevisionLifecycle(h);
  populateRevisions(h);
  const generation = h.context.organizationRequestGenerationRef.current;
  const cleanup = lifecycle.bootstrap();
  assert.equal(h.context.boxDetailRevisionsRef.current.size, 0, 'bootstrap clears before profile GET resolves');
  assert.equal(h.context.organizationRequestGenerationRef.current, generation + 1);
  assert.equal(typeof cleanup, 'function');
  const destination = h.state.data;
  cleanup();
  h.request('GET', '/api/profile/').respond({ id: 9, interface_language: 'en' });
  await tick();
  assert.equal(h.state.data, destination, 'effect cleanup prevents late profile publication');
  assert.equal(h.state.error, null);
  populateRevisions(h);
  const cleanupNext = lifecycle.bootstrap();
  assert.equal(h.context.boxDetailRevisionsRef.current.size, 0, 'a second actual bootstrap also releases revisions');
  h.request('GET', '/api/profile/', 1).respond({ id: 10, interface_language: 'en' });
  await tick();
  assert.equal(h.state.data.profile.id, 10, 'active bootstrap completes');
  assert.equal(h.state.loading, false);
  assert.equal(h.state.error, null);
  cleanupNext();
});

for (const [name, args, path] of [
  ['deactivateBox', [7, {}], '/api/boxes/7/deactivate/'],
  ['createBox', [{}], '/api/boxes/'],
  ['createSubculture', [7, {}], '/api/boxes/7/subcultures/'],
  ['qualifyBoxesBatch', [{ box_ids: [7, 8] }], '/api/admin/box-inventory/batch-qualify/'],
]) {
  test(`scalar dispatch: ${name} never scans a large unrelated revision map`, async () => {
    const h = harness();
    populateRevisions(h, [7, 8]);
    const unrelatedKeys = new Set();
    for (let id = 1000; id < 5096; id++) {
      h.context.invalidateBoxDetail(id);
      unrelatedKeys.add(`${h.context.organizationRequestGenerationRef.current}:${id}`);
    }
    const counters = { iterations: 0, gets: [], unrelatedGets: 0 };
    class InstrumentedMap extends Map {
      get(key) {
        counters.gets.push(key);
        if (unrelatedKeys.has(key)) counters.unrelatedGets++;
        return super.get(key);
      }
      [Symbol.iterator]() { counters.iterations++; return super[Symbol.iterator](); }
      entries() { counters.iterations++; return super.entries(); }
      keys() { counters.iterations++; return super.keys(); }
      values() { counters.iterations++; return super.values(); }
      forEach(...args) { counters.iterations++; return super.forEach(...args); }
    }
    h.context.boxDetailRevisionsRef.current = new InstrumentedMap(h.context.boxDetailRevisionsRef.current);
    const pending = observed(h.context[name](...args));
    h.request('POST', path);
    assert.equal(counters.iterations, 0, 'mutation dispatch must not iterate tracked revisions');
    assert.equal(counters.unrelatedGets, 0, 'mutation dispatch must not visit unrelated Box tokens');
    assert.ok(counters.gets.length > 0 && counters.gets.length <= 2, 'dispatch uses constant-time revision lookups (plus the subculture conflict guard)');
    h.request('POST', path).respond({ detail: 'controlled rejected write' }, 503);
    assert.ok((await pending).error);
    assert.equal(h.state.recovery, null);
  });
}

test('scalar dispatch: same-generation completions keep exact concurrent and independent Box tokens', async () => {
  const h = harness();
  populateRevisions(h, [7, 8]);
  const first = h.context.beginBoxMutation();
  const second = h.context.beginBoxMutation();
  const independent = h.context.beginBoxMutation();
  const a = second(7);
  const b = independent(8);
  assert.equal(a.isCurrent(), true, 'a different Box completion cannot supersede this resource');
  assert.equal(b.isCurrent(), true);
  a.setData(current => ({ ...current, marker: 'newer A' }));
  const older = first(7);
  older.setData(current => ({ ...current, marker: 'stale A' }));
  assert.equal(h.state.data.marker, 'newer A', 'the earlier dispatch remains unsafe after the later completion');
  assert.equal(a.isCurrent(), false, 'every touched Box gets a distinct token even for an unsafe completion');
  assert.equal(older.isCurrent(), true);
  assert.equal(b.isCurrent(), true, 'independent Box retains its exact token');
  h.queueUpdates();
  b.setData(current => ({ ...current, markerB: 'safe B' }));
  older.setData(current => ({ ...current, marker: 'unsafe A' }));
  const unrelated = h.context.beginBoxMutation()(9);
  h.flush();
  assert.equal(h.state.data.markerB, 'safe B');
  assert.equal(h.state.data.marker, 'newer A');
  assert.equal(b.isCurrent(), true);
  assert.equal(unrelated.isCurrent(), true);
});

for (const changed of [false, true]) {
  test(`scalar dispatch: discovered create ID with existing revision ${changed ? 'retains intervening-change guard' : 'stays safe across unrelated completions'}`, async () => {
    const h = harness();
    populateRevisions(h, [8]);
    const created = box(8, 4, { notes: 'discovered result' });
    const pending = observed(h.context.createBox({}));
    const otherId = changed ? 8 : 9;
    const other = observed(h.context.deactivateBox(otherId, {}));
    const authoritative = box(otherId, 0, { status: 'inactive', notes: 'intervening completion' });
    h.request('POST', `/api/boxes/${otherId}/deactivate/`).respond(authoritative);
    assert.equal((await other).error, undefined);
    const before = h.state.data;
    h.request('POST', '/api/boxes/').respond(created);
    await tick();
    if (changed) {
      assert.equal(h.state.data, before, 'existing revision newer than dispatch prevents publication');
      h.request('GET', '/api/boxes/8/').respond(authoritative);
    }
    assert.deepEqual(structuredClone((await pending).value), created);
    assertBox(h, changed ? authoritative : created);
    assert.equal(h.fetches.filter(item => item.method === 'GET').length, changed ? 1 : 0);
    assert.equal(h.state.recovery, null);
  });
}

test('scalar dispatch: subculture siblings and batch successes receive independent exact tokens', async () => {
  const h = harness();
  seedBox(h, box(8));
  populateRevisions(h, [7, 8]);
  const childGuard = h.context.getOperationRequests();
  const childRead = observed(childGuard.apiGet('/api/boxes/8/'));
  const subculture = observed(h.context.createSubculture(7, {}));
  const unrelated = observed(h.context.deactivateBox(9, {}));
  h.request('POST', '/api/boxes/9/deactivate/').respond(box(9, 0, { status: 'inactive' }));
  assert.equal((await unrelated).error, undefined);
  h.request('POST', '/api/boxes/7/subcultures/').respond(subcultureResult([box(8, 4)]));
  await tick();
  assert.equal(h.state.data.boxDetails[7].current_polyp_state.polyp_count, 6);
  assert.deepEqual(structuredClone(h.state.data.boxes.find(item => item.id === 8)), box(8, 4), 'unchanged child revision can publish despite newer unrelated boundaries');
  const parentRead = h.context.getOperationRequests();
  const currentParent = observed(parentRead.apiGet('/api/boxes/7/'));
  const currentChild = h.context.getOperationRequests();
  const newChildRead = observed(currentChild.apiGet('/api/boxes/8/'));
  const batch = observed(h.context.qualifyBoxesBatch({ box_ids: [8, 9] }));
  h.request('POST', '/api/admin/box-inventory/batch-qualify/').respond({
    successes: [{ box_id: 8, status: 'inactive' }], errors: [{ box_id: 9, detail: 'rejected' }],
  });
  assert.equal((await batch).error, undefined);
  assert.equal(parentRead.isCurrent(), true, 'batch child completion preserves parent reconciliation token');
  assert.equal(currentChild.isCurrent(), false, 'batch assigns child a new token');
  h.request('GET', '/api/boxes/8/').respond(box(8, 4));
  assertSuperseded(await childRead);
  h.request('GET', '/api/boxes/8/', 1).respond(box(8, 4));
  assertSuperseded(await newChildRead);
  h.request('GET', '/api/boxes/7/').respond(box(7, 6));
  assert.equal((await currentParent).error, undefined);
  assert.equal((await subculture).error, undefined);
  assert.equal(h.state.data.boxDetails[8].status, 'inactive');
  assert.equal(h.state.recovery, null);
});
