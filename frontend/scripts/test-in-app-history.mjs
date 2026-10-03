import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/inAppHistory.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports, URL });
const { createInAppHistory } = exports;
const markerKey = '__polypbaseInAppHistory';
const context = (path, organization = 1) => ({ path, organization });
const isAppPath = (path) => /^(?:\/(?:zones|overview|labels|exports|profile)?|\/boxes\/[^/]+|\/zones\/[1-9]\d*(?:\/(?:boxes|history))?)$/.test(new URL(path, 'https://test.invalid').pathname);

// Traversal is queued, like browser History: back() does not update location until
// a later popstate. No network, storage, DOM, or browser history is touched.
class FakeHistory {
  constructor(path = '/', state = null) {
    this.entries = [{ path: 'https://external.invalid/', state: null }, { path, state }];
    this.index = 1;
    this.backCalls = 0;
    this.pushCalls = 0;
    this.replaceCalls = 0;
    this.queued = null;
    this.failWrite = false;
  }
  get state() { return structuredClone(this.entries[this.index].state); }
  get path() { return this.entries[this.index].path; }
  pushState(state, unused, path) {
    if (this.failWrite) throw new Error('write failed');
    this.pushCalls++;
    this.entries.splice(this.index + 1);
    this.entries.push({ path, state: structuredClone(state) });
    this.index++;
  }
  replaceState(state, unused, path) {
    if (this.failWrite) throw new Error('write failed');
    this.replaceCalls++;
    this.entries[this.index] = { path, state: structuredClone(state) };
  }
  back() { this.backCalls++; this.queued = this.index - 1; }
  flush(helper, organization = 1) {
    assert.notEqual(this.queued, null);
    this.index = this.queued;
    this.queued = null;
    helper.sync(context(this.path, organization));
  }
  travel(helper, delta, organization = 1) {
    this.index += delta;
    helper.sync(context(this.path, organization));
  }
}

function setup(path = '/', organization = 1, state = null, predicate = isAppPath) {
  const history = new FakeHistory(path, state);
  const helper = createInAppHistory(history, context(path, organization), predicate);
  return { history, helper };
}

test('direct entry has no trusted predecessor and replaces with fallback', () => {
  const { history, helper } = setup('/boxes/A');
  assert.equal(helper.canGoBack(context(history.path)), false);
  assert.equal(helper.back(context(history.path), '/'), 'fallback');
  assert.equal(history.path, '/');
  assert.equal(history.backCalls, 0);
  assert.equal(history.entries.length, 2);
});

test('decoded dot segments are rejected without writes or damage to trusted provenance', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/SAFE'));
  const writes = [history.pushCalls, history.replaceCalls];
  for (const path of ['/boxes/A%2F..%2FB', '/boxes/A%2F.%2FB', '/boxes/%2e%2e%2Flabels',
    '/boxes/%2E%2flabels', '/boxes/A%2f%2E%2e%2FB', '/boxes/A%2F%2e%2fB',
    '/boxes/A%2F.%2e%2fB', '/boxes/A%2F..', '/boxes/A%2F%2E',
    '/boxes/A%2f..%2fB/?source=qr#reading', '/boxes/.%2e%2Flabels']) {
    assert.throws(() => setup(path), /Unsafe in-app path/, path);
    assert.throws(() => helper.push(context(path)), /Unsafe in-app path/, path);
    assert.throws(() => helper.replace(context(path)), /Unsafe in-app path/, path);
    assert.throws(() => helper.reset(context(path)), /Unsafe in-app path/, path);
    assert.throws(() => helper.sync(context(path)), /Unsafe in-app path/, path);
    assert.throws(() => helper.back(context(history.path), path), /Unsafe in-app path/, path);
    assert.equal(history.path, '/boxes/SAFE');
    assert.deepEqual([history.pushCalls, history.replaceCalls], writes);
    assert.equal(history.backCalls, 0);
    assert.equal(helper.canGoBack(context(history.path)), true);
  }
  assert.equal(helper.back(context(history.path), '/'), 'back');
  history.flush(helper);
  assert.equal(history.path, '/zones');
});

for (const code of ['AUR-42', 'AUR 42', 'AUR /42', 'AUR/ZONE/42', 'AUR%2F42',
  'AUR/..B/42', 'AUR/.../42', 'AUR%2F..%2FB']) {
  const path = `/boxes/${encodeURIComponent(code)}`;
  test(`opaque box identifier preserves replace and traversal provenance: ${code}`, () => {
    const { history, helper } = setup('/zones');
    helper.push(context('/boxes/OLD'));
    assert.equal(helper.replace(context(path)), 'replace');
    assert.equal(history.path, path);
    assert.equal(helper.push(context(path)), 'noop');
    assert.equal(history.pushCalls, 1);
    assert.equal(helper.canGoBack(context(path)), true);
    assert.equal(helper.back(context(path), '/'), 'back');
    history.flush(helper);
    assert.equal(history.path, '/zones');
    history.travel(helper, 1);
    assert.equal(history.path, path);
    assert.equal(helper.canGoBack(context(path)), true);
    const refreshed = createInAppHistory(history, context(path), isAppPath);
    assert.equal(refreshed.back(context(path), '/'), 'fallback');
    assert.equal(history.path, '/');
    assert.equal(history.backCalls, 1, 'Reload must not trust the previous ledger');
  });
  test(`direct opaque box identifier has no trusted predecessor: ${code}`, () => {
    const { history, helper } = setup(path);
    assert.equal(history.path, path);
    assert.equal(helper.canGoBack(context(path)), false);
    assert.equal(helper.back(context(path), '/'), 'fallback');
    assert.equal(history.path, '/');
    assert.equal(history.backCalls, 0);
    assert.equal(history.pushCalls, 0);
  });
}

test('encoded box identifier cannot bypass an organization provenance reset', () => {
  const { history, helper } = setup('/zones');
  const path = '/boxes/AUR%20%2F42';
  helper.push(context(path));
  helper.reset(context(path, 2));
  assert.equal(helper.back(context(path, 2), '/'), 'fallback');
  assert.equal(history.path, '/');
  assert.equal(history.backCalls, 0);
});

test('initialization preserves unrelated record state and envelopes non-record state', () => {
  const { history } = setup('/', 1, { scroll: 42, feature: { visible: true } });
  assert.equal(history.state.scroll, 42);
  assert.deepEqual(history.state.feature, { visible: true });
  for (const state of [0, false, 'state', [1, 2], new Date('2026-01-01')]) {
    const result = setup('/', 1, state);
    assert.deepEqual(result.history.state[markerKey].originalState, state);
    result.helper.reset(context('/labels'));
    assert.deepEqual(result.history.state[markerKey].originalState, state);
  }
});

test('back traverses only a verified immediate predecessor and waits for popstate', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/zones/3/history?direction=departure'));
  helper.push(context('/boxes/A'));
  assert.equal(helper.canGoBack(context(history.path)), true);
  assert.equal(helper.back(context(history.path), '/'), 'back');
  assert.equal(history.path, '/boxes/A');
  assert.equal(helper.back(context(history.path), '/'), 'pending');
  assert.equal(helper.push(context('/labels')), 'pending');
  assert.equal(helper.replace(context('/overview')), 'pending');
  assert.equal(history.backCalls, 1);
  history.flush(helper);
  assert.equal(history.path, '/zones/3/history?direction=departure');
  assert.equal(helper.canGoBack(context(history.path)), true);
});

test('reload never trusts preexisting markers even from this module/session', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/A'));
  const oldMarker = history.state[markerKey];
  const refreshed = createInAppHistory(history, context(history.path), isAppPath);
  assert.notEqual(history.state[markerKey].session, oldMarker.session);
  assert.equal(refreshed.back(context(history.path), '/'), 'fallback');
  assert.equal(history.backCalls, 0);
});

test('forged preexisting marker cannot establish a safe chain', () => {
  const { history, helper } = setup('/boxes/A', 1, {
    [markerKey]: { session: 'stale', id: 123, previous: '/zones' }, other: 7,
  });
  assert.equal(history.state.other, 7);
  assert.equal(helper.back(context(history.path), '/'), 'fallback');
  assert.equal(history.backCalls, 0);
});

test('duplicate async openBox pushes and same-path replaces are noops', () => {
  const { history, helper } = setup('/zones');
  assert.equal(helper.push(context('/boxes/A')), 'push');
  assert.equal(helper.push(context('/boxes/A')), 'noop');
  assert.equal(helper.replace(context('/boxes/A')), 'noop');
  assert.equal(history.pushCalls, 1);
  assert.equal(helper.back(context(history.path), '/'), 'back');
  history.flush(helper);
  assert.equal(history.path, '/zones');
});

test('canonical replace preserves predecessor and unrelated current state', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/OLD'));
  history.replaceState({ ...history.state, scroll: 12 }, '', history.path);
  assert.equal(helper.replace(context('/boxes/NEW')), 'replace');
  assert.equal(history.state.scroll, 12);
  assert.equal(helper.back(context(history.path), '/'), 'back');
  history.flush(helper);
  assert.equal(history.path, '/zones');
});

test('replace to the predecessor path does not offer a meaningless back loop', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/A'));
  helper.replace(context('/zones'));
  assert.equal(helper.canGoBack(context(history.path)), false);
  assert.equal(helper.back(context(history.path), '/'), 'fallback');
  assert.equal(history.backCalls, 0);
});

test('browser back and forward maintain the known branch', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/zones/3/boxes'));
  helper.push(context('/boxes/A'));
  history.travel(helper, -1);
  history.travel(helper, 1);
  assert.equal(helper.back(context(history.path), '/'), 'back');
  history.flush(helper);
  assert.equal(history.path, '/zones/3/boxes');
});

test('push after back discards forward ledger entries', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/A'));
  const discardedState = history.state;
  history.travel(helper, -1);
  helper.push(context('/labels'));
  assert.equal(history.entries.length, 3);
  // Even a copied marker from the discarded branch cannot be resurrected.
  history.pushState(discardedState, '', '/boxes/A');
  helper.sync(context(history.path));
  assert.equal(helper.back(context(history.path), '/'), 'fallback');
  assert.equal(history.backCalls, 0);
});

test('replace while back preserves a still-valid forward entry', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/A'));
  history.travel(helper, -1);
  helper.replace(context('/labels'));
  history.travel(helper, 1);
  helper.back(context(history.path), '/');
  history.flush(helper);
  assert.equal(history.path, '/labels');
});

test('unknown/unowned entries and location mismatch fail closed', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/A'));
  assert.equal(helper.canGoBack(context('/boxes/WRONG')), false);
  history.pushState(null, '', '/overview');
  helper.sync(context(history.path));
  assert.equal(helper.canGoBack(context(history.path)), false);
  history.travel(helper, -1);
  assert.equal(helper.canGoBack(context(history.path)), false);
  assert.equal(helper.back(context(history.path), '/'), 'fallback');
  assert.equal(history.backCalls, 0);
});

test('organization changes reset both navigation and old browser branches', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/A'));
  helper.reset(context('/zones', 2));
  helper.push(context('/boxes/B', 2));
  helper.back(context(history.path, 2), '/');
  history.flush(helper, 2);
  assert.equal(helper.canGoBack(context(history.path, 2)), false);
  history.travel(helper, -1, 2);
  assert.equal(helper.back(context(history.path, 2), '/'), 'fallback');
  assert.equal(history.backCalls, 1);
});

test('org mismatches automatically break the chain for push, replace, sync, and back', () => {
  for (const action of ['push', 'replace', 'sync', 'back']) {
    const { history, helper } = setup('/zones');
    helper.push(context('/boxes/A'));
    if (action === 'back') helper.back(context(history.path, 2), '/zones');
    else helper[action](context(action === 'sync' ? history.path : '/labels', 2));
    assert.equal(helper.canGoBack(context(history.path, 2)), false);
    assert.equal(history.backCalls, 0);
  }
});

test('auth and explicit access redirects invalidate meaningful provenance', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/A'));
  helper.replace(context('/login?next=%2Fboxes%2FA', null), true);
  helper.replace(context('/boxes/A'), true);
  assert.equal(helper.back(context(history.path), '/'), 'fallback');
  assert.equal(history.backCalls, 0);
  helper.push(context('/zones'));
  helper.replace(context('/'), true);
  assert.equal(helper.canGoBack(context(history.path)), false);
});

test('unrecognized initial routes do not become trusted predecessors', () => {
  for (const path of ['/unknown', '/login', '/reset-password/user/token', '/bac/42']) {
    const { history, helper } = setup(path);
    helper.push(context('/boxes/A'));
    assert.equal(helper.back(context(history.path), '/'), 'fallback');
    assert.equal(history.backCalls, 0);
  }
});

test('route eligibility is rechecked at back time (e.g. desktop Administration)', () => {
  let allowed = true;
  const { history, helper } = setup('/profile', 1, null, path => isAppPath(path) && (path !== '/profile' || allowed));
  helper.push(context('/boxes/A'));
  allowed = false;
  assert.equal(helper.back(context(history.path), '/'), 'fallback');
  assert.equal(history.backCalls, 0);
});

test('reset during queued back cannot revive the old context when popstate arrives', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/A'));
  helper.back(context(history.path), '/');
  helper.reset(context('/labels', 2));
  history.flush(helper, 2);
  assert.equal(helper.canGoBack(context(history.path, 2)), false);
  helper.back(context(history.path, 2), '/');
  assert.equal(history.backCalls, 1);
  assert.equal(history.path, '/');
});

test('unowned writes cannot silently establish push/replace provenance', () => {
  for (const action of ['push', 'replace']) {
    const { history, helper } = setup('/zones');
    helper.push(context('/boxes/A'));
    history.replaceState(null, '', '/overview');
    assert.equal(helper[action](context('/labels')), 'replace');
    assert.equal(helper.back(context(history.path), '/'), 'fallback');
    assert.equal(history.backCalls, 0);
  }
});

test('rejects external, noncanonical, malformed and unrecognized destinations', () => {
  const { history, helper } = setup('/zones');
  for (const path of ['https://external.invalid', '//external.invalid', '/\\external', '/zones/../labels',
    '/%2fexternal', '/%5cexternal', '/boxes/%zz', '/labels\n', '/boxes%2FAUR', '/boxes/A%2FB/extra',
    '/boxes/A%2FB//', '/zones/3%2Fhistory', '/administration%2Fteam', '/unknown/A%2FB',
    '/boxes/A/../B', '/boxes/%2e%2e', '/boxes/%2e%2e/labels', '/boxes/A%2F42%zz',
    '/boxes/A%2F42%5C', '/boxes/A%2F42%00', '/boxes/A%2F42%0a', '/boxes/A%2F42%7f']) {
    const writes = [history.pushCalls, history.replaceCalls];
    assert.throws(() => helper.push(context(path)), undefined, path);
    assert.throws(() => helper.replace(context(path)), undefined, path);
    assert.throws(() => helper.back(context(history.path), path), undefined, path);
    assert.throws(() => helper.reset(context(path)), undefined, path);
    assert.deepEqual([history.pushCalls, history.replaceCalls], writes, 'Rejected paths must not write history');
  }
  assert.throws(() => helper.push(context('/unknown')));
  assert.throws(() => helper.back(context(history.path), '/unknown'));
  assert.equal(history.path, '/zones');
  assert.equal(history.backCalls, 0);
});

test('failed writes do not corrupt the verified ledger', () => {
  const { history, helper } = setup('/zones');
  helper.push(context('/boxes/A'));
  history.failWrite = true;
  assert.throws(() => helper.push(context('/labels')), /write failed/);
  assert.throws(() => helper.replace(context('/overview')), /write failed/);
  assert.throws(() => helper.reset(context('/')), /write failed/);
  history.failWrite = false;
  assert.equal(helper.canGoBack(context(history.path)), true);
  helper.back(context(history.path), '/');
  history.flush(helper);
  assert.equal(history.path, '/zones');
});
