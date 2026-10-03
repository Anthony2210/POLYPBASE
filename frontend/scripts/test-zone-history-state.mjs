import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

function loadModule(path, imports = {}, globals = {}) {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), `Unexpected import: ${name}`);
      return imports[name];
    },
    ...globals,
  }, { filename: path });
  return exports;
}

const catalogs = {
  fr: loadModule('../src/i18n/fr.ts').fr,
  en: loadModule('../src/i18n/en.ts').en,
};
const dateFormat = loadModule('../src/utils/dateFormat.ts');
const zone = (id = 1) => ({ id, name: `Zone ${id}`, organization: { id: 1, name: 'Test laboratory' } });
const movement = (code = 'ENTRY-A', direction = 'arrival') => ({
  location_id: 1,
  event_type: direction,
  occurred_at: '2026-09-28T08:00:00Z',
  box_id: 42,
  box_code: code,
  box_status: 'active',
  related_zone_id: 2,
  related_zone_name: 'Related zone',
});
const page = (results = [], overrides = {}) => ({ count: results.length, next: null, previous: null, results, ...overrides });
const week = (entry_count, exit_count, overrides = {}) => ({
  week_start: '2026-09-28', iso_week: 40, iso_year: 2026, entry_count, exit_count, ...overrides,
});
const summary = (weeks = [week(3, 1)], recent_arrivals = [movement()], recent_departures = []) => ({
  weeks, recent_arrivals, recent_departures,
});

function text(node) {
  if (Array.isArray(node)) return node.map(text).join('');
  if (React.isValidElement(node)) return text(node.props.children);
  if (node == null || typeof node === 'boolean') return '';
  return String(node);
}

// Run real component JSX, API deduplication, error handling and effect cleanup.
// Explicit render/commit phases let tests inspect the frame before passive effects.
// This hook harness does not replace browser focus, layout or screen-reader QA.
function createView(kind = 'history', overrides = {}) {
  let organizationId = '1';
  const requests = [];
  const api = loadModule('../src/api/client.ts', {}, {
    Headers,
    window: { localStorage: { getItem: () => organizationId } },
    fetch(path, options) {
      let resolve;
      let reject;
      const promise = new Promise((accept, decline) => { resolve = accept; reject = decline; });
      requests.push({ path, options, resolve, reject });
      return promise;
    },
  });
  const errors = loadModule('../src/utils/errors.ts', { '../api/client': api });
  const slots = [];
  let cursor = 0;
  let dirty = false;
  let pendingEffects = new Map();
  const sameDeps = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const hooks = {
    ...React,
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, (next) => {
        const value = typeof next === 'function' ? next(slots[index].value) : next;
        if (!Object.is(value, slots[index].value)) {
          slots[index].value = value;
          dirty = true;
        }
      }];
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!sameDeps(slots[index]?.deps, deps)) pendingEffects.set(index, { effect, deps });
    },
    useMemo: (factory) => factory(),
  };
  const detailBackButton = loadModule('../src/components/DetailBackButton.tsx', {
    'react/jsx-runtime': jsxRuntime,
    'lucide-react': { ArrowLeft: () => null },
    '../hooks/useIsDesktopApp': { useIsDesktopApp: () => true },
  });
  const components = loadModule('../src/components/ZoneMovementHistory.tsx', {
    react: hooks,
    'react/jsx-runtime': jsxRuntime,
    'lucide-react': { ArrowDownToLine: () => null, ArrowUpFromLine: () => null },
    '../api/client': api,
    '../utils/errors': errors,
    '../utils/dateFormat': dateFormat,
    './BoxTrackingPreview': { default: ({ code }) => React.createElement('span', null, code) },
    './DetailBackButton': detailBackButton,
    './PageLoader': { default: ({ label }) => React.createElement('p', null, label) },
    './SkeletonRows': loadModule('../src/components/SkeletonRows.tsx', { 'react/jsx-runtime': jsxRuntime }),
  });
  const component = kind === 'history' ? components.default : components.ZoneRecentMovements;
  let props = {
    direction: 'arrival', isLoading: false, language: 'en', zone: zone(), zoneId: 1,
    onBack() {}, onChangeDirection() {}, onOpenBox() {}, onOpenHistory() {},
    ...overrides,
  };
  let tree;
  let html;
  function resolve(node) {
    if (Array.isArray(node)) return node.map(resolve);
    if (!React.isValidElement(node)) return node;
    if (typeof node.type === 'function') return resolve(node.type(node.props));
    const children = resolve(node.props.children);
    return Array.isArray(children)
      ? React.cloneElement(node, {}, ...children)
      : React.cloneElement(node, {}, children);
  }
  function render() {
    let iterations = 0;
    do {
      assert.ok(++iterations < 20, 'Render-phase state did not settle');
      dirty = false;
      cursor = 0;
      pendingEffects = new Map();
      tree = resolve(component({ ...props, t: (key) => {
        assert.ok(Object.hasOwn(catalogs[props.language], key), `Missing translation: ${key}`);
        return catalogs[props.language][key];
      } }));
    } while (dirty);
    html = renderToStaticMarkup(tree);
  }
  function commit() {
    const effects = pendingEffects;
    pendingEffects = new Map();
    for (const index of effects.keys()) slots[index]?.cleanup?.();
    for (const [index, { effect, deps }] of effects) slots[index] = { deps, cleanup: effect() };
    if (dirty) render();
  }
  function find(predicate) {
    function visit(node) {
      if (Array.isArray(node)) return node.flatMap(visit);
      if (!React.isValidElement(node)) return [];
      return [...(predicate(node) ? [node] : []), ...visit(node.props.children)];
    }
    return visit(tree);
  }
  async function settle(request, data, status = 200) {
    request.resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));
    await new Promise((accept) => setImmediate(accept));
    render();
  }
  function click(label) {
    const button = find((node) => node.type === 'button' && text(node) === label)[0];
    assert.ok(button, `Missing button: ${label}`);
    assert.notEqual(button.props.disabled, true, `Disabled button: ${label}`);
    button.props.onClick();
    render();
  }
  render();
  commit();
  return {
    requests, find, render, commit, settle, click,
    get html() { return html; },
    setProps(next) { props = { ...props, ...next }; render(); },
    setOrganization(id) { organizationId = String(id); render(); },
    unmount() { slots.forEach((slot) => slot?.cleanup?.()); },
  };
}

function assertLoading(view) {
  assert.match(view.html, /aria-busy="true"/);
  assert.match(view.html, /skeleton-stack/);
  assert.ok(view.find((node) => node.props.role === 'status' && text(node) === 'Loading...').length);
  assert.doesNotMatch(view.html, /zone-movement-row |zone-movement-pagination|No entry recorded|No exit recorded/);
}
function assertError(view, reason) {
  assert.match(view.html, /aria-busy="false"/);
  assert.ok(view.find((node) => node.props.role === 'status' && text(node).includes(reason)).length);
  assert.doesNotMatch(view.html, /role="alert"|zone-movement-row |zone-movement-pagination|skeleton-stack/);
  assert.ok(view.find((node) => node.type === 'button' && text(node) === 'Try again').length);
}

test('successful Entry A -> Exit B -> B fails never relabels or retains A, and retries B locally', async () => {
  const view = createView();
  assertLoading(view);
  await view.settle(view.requests[0], page([movement()], { count: 53, next: 'next' }));
  assert.match(view.html, /ENTRY-A/);
  assert.match(view.html, /aria-label="Entries, ENTRY-A/);
  assert.match(view.html, /1–1 of 53/);
  view.setProps({ direction: 'departure' });
  // Before effect cleanup or loading state is committed, stale rows/counts are absent.
  assertLoading(view);
  assert.doesNotMatch(view.html, /ENTRY-A|of 53|From Related zone/);
  view.commit();
  assert.match(view.requests[1].path, /direction=departure&limit=24&offset=0$/);
  await view.settle(view.requests[1], { detail: 'Exit history unavailable' }, 503);
  assertError(view, 'Exit history unavailable');
  assert.doesNotMatch(view.html, /ENTRY-A|No exit recorded/);
  view.click('Try again');
  assertLoading(view);
  view.commit();
  assert.equal(view.requests[2].path, view.requests[1].path);
  await view.settle(view.requests[2], page([movement('EXIT-B', 'departure')]));
  assert.match(view.html, /aria-label="Exits, EXIT-B/);
  assert.match(view.html, /To Related zone/);
  assert.doesNotMatch(view.html, /ENTRY-A|Exit history unavailable/);
});

for (const lateStatus of [200, 503]) {
  test(`late old history HTTP ${lateStatus} cannot replace the new context`, async () => {
    const view = createView();
    view.setProps({ direction: 'departure' });
    view.commit();
    await view.settle(view.requests[1], page([movement('CURRENT-EXIT', 'departure')]));
    await view.settle(view.requests[0], lateStatus === 200 ? page([movement('LATE-ENTRY')]) : { detail: 'Old failure' }, lateStatus);
    assert.match(view.html, /CURRENT-EXIT/);
    assert.doesNotMatch(view.html, /LATE-ENTRY|Old failure|Try again/);
  });
}

test('a completion between context render and cleanup stays hidden', async () => {
  const view = createView();
  view.setProps({ direction: 'departure' });
  await view.settle(view.requests[0], page([movement('BEFORE-CLEANUP')]));
  assertLoading(view);
  assert.doesNotMatch(view.html, /BEFORE-CLEANUP/);
  view.commit();
  await view.settle(view.requests[1], { detail: 'Current failure' }, 500);
  assertError(view, 'Current failure');
});

test('pagination has exact server counts and hides old pages when the next page fails', async () => {
  const view = createView();
  const first = Array.from({ length: 24 }, (_, index) => ({ ...movement(`BOX-${index}`), location_id: index + 1 }));
  await view.settle(view.requests[0], page(first, { count: 53, next: 'next' }));
  assert.match(view.html, /1–24 of 53/);
  assert.match(view.html, /1 \/ 3/);
  view.click('Next');
  assertLoading(view);
  view.commit();
  assert.match(view.requests[1].path, /offset=24$/);
  await view.settle(view.requests[1], { detail: 'Page unavailable' }, 500);
  assertError(view, 'Page unavailable');
  view.click('Try again');
  view.commit();
  await view.settle(view.requests[2], page([movement('PAGE-2')], { count: 53, previous: 'previous', next: 'next' }));
  assert.match(view.html, /25–25 of 53/);
  assert.match(view.html, /2 \/ 3/);
});

for (const change of ['direction', 'zone', 'organization']) {
  test(`changing ${change} resets offset immediately, including when returning to the old context`, async () => {
    const view = createView();
    await view.settle(view.requests[0], page([movement()], { count: 53, next: 'next' }));
    view.click('Next');
    view.commit();
    await view.settle(view.requests[1], page([movement('OLD-PAGE-2')], { count: 53, previous: 'previous' }));
    if (change === 'direction') view.setProps({ direction: 'departure' });
    if (change === 'zone') view.setProps({ zone: zone(2) });
    if (change === 'organization') view.setOrganization(2);
    assertLoading(view);
    assert.doesNotMatch(view.html, /OLD-PAGE-2/);
    view.commit();
    assert.match(view.requests[2].path, /offset=0$/);
    if (change === 'organization') assert.equal(view.requests[2].options.headers.get('X-Organization-Id'), '2');
    await view.settle(view.requests[2], { detail: 'New context unavailable' }, 503);
    assertError(view, 'New context unavailable');
    if (change === 'direction') view.setProps({ direction: 'arrival' });
    if (change === 'zone') view.setProps({ zone: zone() });
    if (change === 'organization') view.setOrganization(1);
    view.commit();
    assert.match(view.requests[3].path, /offset=0$/);
  });
}

test('empty successful history is local and distinct from loading/error, with no invented pagination', async () => {
  const view = createView();
  await view.settle(view.requests[0], page());
  assert.match(view.html, /No entry recorded\./);
  assert.doesNotMatch(view.html, /skeleton-stack|Try again|zone-movement-pagination/);
  view.setProps({ direction: 'departure' });
  assertLoading(view);
  view.commit();
  await view.settle(view.requests[1], page());
  assert.match(view.html, /No exit recorded\./);
  assert.doesNotMatch(view.html, /No entry recorded\./);
});

test('recent summary success A -> zone B -> B fails removes old chart and rows, then retries B', async () => {
  const view = createView('recent');
  assertLoading(view);
  await view.settle(view.requests[0], summary());
  assert.match(view.html, /ENTRY-A|zone-movement-flow-chart/);
  view.setProps({ zoneId: 2 });
  assertLoading(view);
  assert.doesNotMatch(view.html, /ENTRY-A|zone-movement-flow-chart/);
  view.commit();
  await view.settle(view.requests[1], { detail: 'Summary unavailable' }, 503);
  assertError(view, 'Summary unavailable');
  assert.doesNotMatch(view.html, /ENTRY-A|zone-movement-flow-chart/);
  view.click('Try again');
  assertLoading(view);
  view.commit();
  assert.equal(view.requests[2].path, '/api/thermal-zones/2/history/summary/');
  await view.settle(view.requests[2], summary([], [], []));
  assert.match(view.html, /No movement recorded\./);
  assert.match(view.html, /No entry recorded\./);
  assert.match(view.html, /No exit recorded\./);
  assert.doesNotMatch(view.html, /zone-movement-flow-chart|Summary unavailable/);
});

for (const lateStatus of [200, 503]) {
  test(`late summary HTTP ${lateStatus} cannot replace the new summary`, async () => {
    const view = createView('recent');
    view.setProps({ zoneId: 2 });
    view.commit();
    await view.settle(view.requests[1], summary([week(0, 4)], [], [movement('CURRENT-EXIT', 'departure')]));
    await view.settle(view.requests[0], lateStatus === 200 ? summary() : { detail: 'Old summary failure' }, lateStatus);
    assert.match(view.html, /CURRENT-EXIT/);
    assert.doesNotMatch(view.html, /ENTRY-A|Old summary failure/);
  });
}

test('summary organization changes invalidate rows/chart even with the same zone ID', async () => {
  const view = createView('recent');
  await view.settle(view.requests[0], summary());
  view.setOrganization(2);
  assertLoading(view);
  assert.doesNotMatch(view.html, /ENTRY-A|zone-movement-flow-chart/);
  view.commit();
  assert.equal(view.requests[1].options.headers.get('X-Organization-Id'), '2');
  await view.settle(view.requests[1], { detail: 'Unavailable for this institution' }, 403);
  assertError(view, 'Unavailable for this institution');
});

for (const kind of ['history', 'recent']) {
  test(`${kind} network errors use translated text and retries do not duplicate in-flight requests`, async () => {
    const view = createView(kind, { language: 'fr' });
    view.requests[0].reject(new Error('Network failure'));
    await new Promise((accept) => setImmediate(accept));
    view.render();
    assert.ok(view.html.includes(catalogs.fr.auditValueUnavailable));
    assert.doesNotMatch(view.html, /Impossible de joindre|Network failure/);
    view.click(catalogs.fr.zoneMovementRetry);
    view.commit();
    assert.equal(view.requests.length, 2);
    assert.equal(view.find((node) => node.type === 'button' && text(node) === catalogs.fr.zoneMovementRetry).length, 0);
  });
  test(`${kind} late results after unmount cannot update component state`, async () => {
    const view = createView(kind);
    const before = view.html;
    view.unmount();
    await view.settle(view.requests[0], kind === 'history' ? page([movement()]) : summary());
    assert.equal(view.html, before);
  });
}

test('chart preserves exact weekly counts and integer ticks across year boundaries, with an accessible text equivalent', async () => {
  const view = createView('recent');
  const weeks = [week(7, 2, { week_start: '2025-12-29', iso_week: 1, iso_year: 2026 }), week(0, 0)];
  await view.settle(view.requests[0], summary(weeks, [], []));
  assert.match(view.html, /role="img" aria-label="Entries and exits over the last eight weeks"/);
  assert.match(view.html, /Week 1 of 2026: 7 entries, 2 exits\./);
  assert.match(view.html, /Week 40 of 2026: 0 entries, 0 exits\./);
  assert.equal(view.find((node) => node.type === 'ul' && node.props.className === 'sr-only').length, 1);
  const entries = view.find((node) => node.props.className === 'zone-movement-flow-entry');
  const exits = view.find((node) => node.props.className === 'zone-movement-flow-exit');
  assert.equal(entries.length, 2);
  assert.equal(exits.length, 1, 'Zero exits must not acquire a visible outline at the baseline');
  assert.ok(Math.abs(entries[0].props.height - 7 / 9 * 80) < 1e-10);
  assert.ok(Math.abs(exits[0].props.height - 2 / 9 * 80) < 1e-10);
  assert.equal(entries[1].props.height, 0);
  assert.ok(entries[0].props.x < exits[0].props.x);
  const ticks = view.find((node) => node.props.className === 'zone-movement-flow-grid').map((node) =>
    Number(text(React.Children.toArray(node.props.children).find((child) => child.type === 'text'))));
  assert.deepEqual(ticks, [0, 3, 6, 9]);
});

test('all-zero weeks remain recorded chart data, not an absent chart', async () => {
  const view = createView('recent');
  await view.settle(view.requests[0], summary([week(0, 0)], [], []));
  assert.match(view.html, /zone-movement-flow-chart/);
  assert.match(view.html, /Week 40 of 2026: 0 entries, 0 exits\./);
  assert.doesNotMatch(view.html, /No movement recorded\./);
  assert.equal(view.find((node) => node.props.className === 'zone-movement-flow-entry')[0].props.height, 0);
  assert.equal(view.find((node) => node.props.className === 'zone-movement-flow-exit').length, 0);
});
