import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { appHarness, ast as appAst, deferred, tick } from './app-operation-test-harness.mjs';

function lineageHarness() {
  const h = appHarness();
  const request = deferred();
  const calls = [];
  const state = { graph: null, loading: false, error: null };
  Object.assign(h.context, {
    box: { id: 7 }, activeInsightTab: 'lineage', lineageGraph: null, lineageGraphError: null,
    isOperationCurrent: () => h.context.organizationRequestGenerationRef.current === 0,
    lineageRequestGenerationRef: { current: 0 }, lineageRequestPendingRef: { current: false },
    setLineageGraph(value) { state.graph = value; h.context.lineageGraph = value; },
    setIsLineageGraphLoading(value) { state.loading = value; },
    setLineageGraphError(value) { state.error = value; h.context.lineageGraphError = value; },
    getErrorMessage: error => error.message,
    onLoadLineageGraph(id) { calls.push(id); return request.promise; },
  });
  let effect, lifetime;
  function visit(node) {
    if (ts.isCallExpression(node) && node.expression.getText(appAst) === 'useEffect') {
      const code = node.arguments[0].getText(appAst);
      if (code.includes('void handleLoadLineageGraph()')) effect = node.arguments[0];
      if (code.includes('lineageRequestGenerationRef.current += 1')) lifetime = node.arguments[0];
    }
    ts.forEachChild(node, visit);
  }
  visit(appAst);
  assert.ok(effect);
  assert.ok(lifetime);
  const cleanup = h.evaluate(`(${lifetime.getText(appAst)})()`);
  return { h, request, calls, state, cleanup, render() { return h.evaluate(`(${effect.getText(appAst)})()`); } };
}
test('lineage load survives leaving and returning to the tab without a stuck loader or duplicate read', async () => {
  const l = lineageHarness();
  l.render();
  assert.equal(l.state.loading, true);
  l.h.context.activeInsightTab = 'movements';
  assert.equal(l.render(), undefined, 'tab leave does not dispose the box-owned request');
  l.h.context.activeInsightTab = 'lineage';
  l.render();
  assert.equal(l.calls.length, 1);
  const graph = { nodes: [{ id: 7 }], edges: [] };
  l.request.resolve(graph);
  await tick();
  assert.equal(l.state.graph, graph);
  assert.equal(l.state.loading, false);
  l.render();
  assert.equal(l.calls.length, 1);
});
for (const replacement of ['box', 'organization']) {
  test(`lineage ignores completion after ${replacement} lifetime ends`, async () => {
    const l = lineageHarness();
    l.render();
    l.cleanup();
    const replacementGraph = { nodes: [{ id: 9 }] };
    l.state.graph = replacementGraph;
    l.state.loading = false;
    l.request.resolve({ nodes: [{ id: 7 }] });
    await tick();
    assert.equal(l.state.graph, replacementGraph);
    assert.equal(l.state.loading, false);
    assert.equal(l.state.error, null);
  });
}
test('lineage failure ends loading and an explicit retry owns a new request', async () => {
  const l = lineageHarness();
  l.render();
  l.request.reject(new Error('lineage offline'));
  await tick();
  assert.equal(l.state.loading, false);
  assert.equal(l.state.error, 'lineage offline');
  l.render();
  assert.equal(l.calls.length, 1, 'no infinite automatic retries');
  const retry = deferred();
  l.h.context.onLoadLineageGraph = () => retry.promise;
  const pending = l.h.context.handleLoadLineageGraph();
  retry.resolve({ nodes: [] });
  await pending;
  assert.equal(l.state.loading, false);
  assert.equal(l.state.error, null);
});

function loadModule(path, imports = {}) {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
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
  }, { filename: path });
  return exports;
}

const dateFormat = loadModule('../src/utils/dateFormat.ts');
const labels = {
  close: 'Close history',
  ephyraeFull: 'Ephyrae',
  historyAllYears: 'All years',
  historyVisibleCount: (visible, total) => `${visible} of ${total} measurements`,
  historyYearFilter: 'Filter by year',
  historyEnteredBy: 'Entered by',
  historyHideComment: 'Hide comment',
  historyObservation: 'Observation',
  historyReadComment: 'Read comment',
  historyShowMore: 'Show more',
  historyYear: 'Date',
  measurementHistory: 'Measurement history',
  noMeasurementHistory: 'No measurement history',
  polyps: 'Polyps',
};

function measurement(overrides = {}) {
  return {
    id: 1,
    measured_on: '2026-09-16',
    polyp_count: 12,
    ephyrae_count: 3,
    strobila_count: 0,
    salinity_psu: null,
    culture_status: 'good',
    needs_attention: false,
    notes: '',
    user: 'tech',
    created_at: '2026-09-16T08:00:00Z',
    can_edit: true,
    edit_deadline: '2026-09-17T08:00:00Z',
    edit_restriction: null,
    ...overrides,
  };
}

function measurementsForYear(year, count, firstId = 1) {
  return Array.from({ length: count }, (_, index) => measurement({
    id: firstId + index,
    measured_on: new Date(Date.UTC(year, 0, index + 1)).toISOString().slice(0, 10),
    user: `author-${firstId + index}`,
  }));
}

function createHistory(measurements, overrides = {}) {
  const slots = [];
  let cursor = 0;
  function slot(initial) {
    const index = cursor++;
    if (!(index in slots)) slots[index] = initial();
    return index;
  }
  const hooks = {
    ...React,
    useState(initial) {
      const index = slot(() => typeof initial === 'function' ? initial() : initial);
      return [slots[index], (value) => {
        slots[index] = typeof value === 'function' ? value(slots[index]) : value;
      }];
    },
    useRef(initial) {
      return slots[slot(() => ({ current: initial }))];
    },
    useId() {
      return slots[slot(() => `history-title-${cursor}`)];
    },
    useMemo: (factory) => factory(),
    // DOM effects (focus trap, inert background, Escape) belong to browser QA.
    useEffect() {},
    useLayoutEffect() {},
  };
  const { MeasurementHistoryModal } = loadModule('../src/components/BoxInsights.tsx', {
    react: hooks,
    'react/jsx-runtime': jsxRuntime,
    '../utils/dateFormat': dateFormat,
    './PolypbaseIcon': { default: () => null },
    './BoxTrackingChart': { default: () => null, buildLifecycleEvents: () => [] },
    './ModalPortal': { default: ({ children }) => children },
  });
  const props = { boxCode: 'BOX-42', labels, measurements, onClose() {}, ...overrides };
  let tree;
  let html;

  // Execute the real nested function components, retaining host event handlers
  // for actions and using React SSR to verify the resulting markup as well.
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
    cursor = 0;
    tree = resolve(MeasurementHistoryModal(props));
    html = renderToStaticMarkup(tree);
  }
  function find(predicate, node = tree) {
    if (Array.isArray(node)) return node.flatMap((child) => find(predicate, child));
    if (!React.isValidElement(node)) return [];
    return [...(predicate(node) ? [node] : []), ...find(predicate, node.props.children)];
  }
  function changeYear(year) {
    find((node) => node.type === 'select')[0].props.onChange({ target: { value: year } });
    render();
  }
  function showMore() {
    const button = find((node) => node.type === 'button' && text(node).startsWith(labels.historyShowMore))[0];
    assert.ok(button, 'Expected a show-more button');
    let focused = false;
    button.props.onClick({ currentTarget: { focus() { focused = true; } } });
    render();
    return focused;
  }
  render();
  return {
    find,
    render,
    changeYear,
    showMore,
    get html() { return html; },
    rows: () => find((node) => node.type === 'article' && node.props.role === 'row'),
    status: () => text(find((node) => node.props.role === 'status')[0]),
    more: () => find((node) => node.type === 'button' && text(node).startsWith(labels.historyShowMore)),
  };
}

function text(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (Array.isArray(node)) return node.map(text).join('');
  if (React.isValidElement(node)) return text(node.props.children);
  return String(node);
}

function cells(row) {
  return React.Children.toArray(row.props.children).filter((node) => node.props.role === 'cell');
}

function cellValue(cell) {
  return text(React.Children.toArray(cell.props.children).filter((node) => node.type !== 'small'));
}

function rowAuthors(history) {
  return history.rows().map((row) => cellValue(cells(row)[4]));
}

test('sorts by date then created_at descending without mutating input', () => {
  const input = Object.freeze([
    Object.freeze(measurement({ id: 1, measured_on: '2025-12-31', created_at: '2027-01-01T00:00:00Z', user: 'older date' })),
    Object.freeze(measurement({ id: 2, created_at: '2026-09-16T08:00:00Z', user: 'earlier entry' })),
    Object.freeze(measurement({ id: 3, created_at: '2026-09-16T12:00:00Z', user: 'later entry' })),
    Object.freeze(measurement({ id: 4, measured_on: '2026-09-17', created_at: '2026-01-01T00:00:00Z', user: 'newest date' })),
  ]);
  const before = JSON.stringify(input);
  const history = createHistory(input);
  assert.deepEqual(rowAuthors(history), ['newest date', 'later entry', 'earlier entry', 'older date']);
  history.changeYear('2026');
  assert.deepEqual(rowAuthors(history), ['newest date', 'later entry', 'earlier entry']);
  assert.equal(JSON.stringify(input), before);
});

test('lists distinct descending years and separates the filter label from Date', () => {
  const history = createHistory([
    measurement({ id: 1, measured_on: '2024-12-31' }),
    measurement({ id: 2, measured_on: '2026-01-01' }),
    measurement({ id: 3, measured_on: '2025-06-01' }),
    measurement({ id: 4, measured_on: '2026-09-01' }),
  ]);
  const select = history.find((node) => node.type === 'select')[0];
  const options = history.find((node) => node.type === 'option');
  assert.equal(select.props.value, 'all');
  assert.deepEqual(options.map((node) => node.props.value), ['all', '2026', '2025', '2024']);
  assert.deepEqual(options.map(text), [labels.historyAllYears, '2026', '2025', '2024']);
  assert.ok(text(history.find((node) => node.type === 'label')[0]).startsWith(labels.historyYearFilter));
  assert.equal(text(history.find((node) => node.props.role === 'columnheader')[0]), labels.historyYear);
  assert.match(history.html, /<label><span>Filter by year<\/span><select/);
});

test('renders an initial batch of 24 and counts visible versus all measurements', () => {
  const calls = [];
  const history = createHistory(measurementsForYear(2026, 53), {
    labels: { ...labels, historyVisibleCount(visible, total) {
      calls.push([visible, total]);
      return labels.historyVisibleCount(visible, total);
    } },
  });
  assert.equal(history.rows().length, 24);
  assert.deepEqual(calls, [[24, 53]]);
  assert.equal(history.status(), '24 of 53 measurements');
  assert.equal(text(history.more()[0]), 'Show more (24)');
  assert.equal(history.find((node) => node.type === 'time').length, 24);
  assert.match(history.html, /role="status">24 of 53 measurements/);
});

test('show more adds 24 then the last partial batch and removes the control', () => {
  const history = createHistory(measurementsForYear(2026, 53));
  assert.equal(history.showMore(), true);
  assert.equal(history.rows().length, 48);
  assert.equal(history.status(), '48 of 53 measurements');
  assert.equal(text(history.more()[0]), 'Show more (5)');
  history.showMore();
  assert.equal(history.rows().length, 53);
  assert.equal(history.status(), '53 of 53 measurements');
  assert.equal(history.more().length, 0);
  assert.equal(new Set(rowAuthors(history)).size, 53);
});

test('filters locally and resets pagination to 24 on year changes and all years', () => {
  const history = createHistory([
    ...measurementsForYear(2025, 31, 101),
    ...measurementsForYear(2026, 53),
  ]);
  history.showMore();
  assert.equal(history.status(), '48 of 84 measurements');
  history.changeYear('2025');
  assert.equal(history.rows().length, 24);
  assert.equal(history.status(), '24 of 31 measurements');
  assert.equal(text(history.more()[0]), 'Show more (7)');
  assert.ok(history.find((node) => node.type === 'time').every((node) => node.props.dateTime.startsWith('2025-')));
  history.showMore();
  assert.equal(history.status(), '31 of 31 measurements');
  assert.equal(history.more().length, 0);
  history.changeYear('2026');
  assert.equal(history.status(), '24 of 53 measurements');
  history.showMore();
  history.changeYear('all');
  assert.equal(history.status(), '24 of 84 measurements');
  assert.equal(history.rows().length, 24);
});

test('does not show pagination when an exact batch of 24 is exhausted', () => {
  const history = createHistory(measurementsForYear(2026, 48));
  history.showMore();
  assert.equal(history.status(), '48 of 48 measurements');
  assert.equal(history.rows().length, 48);
  assert.equal(history.more().length, 0);
});

test('preserves zero-zero and string zero PSU as recorded values', () => {
  const history = createHistory([measurement({ polyp_count: 0, ephyrae_count: 0, salinity_psu: '0' })]);
  const rowCells = cells(history.rows()[0]);
  assert.deepEqual(rowCells.slice(1, 4).map(cellValue), ['0', '0', '0']);
  assert.equal(React.Children.toArray(rowCells[3].props.children)[1].props.className, '');
  assert.equal(history.status(), '1 of 1 measurements');
  assert.doesNotMatch(history.html, /is-missing|No measurement history/);
  assert.equal(history.find((node) => node.type === 'strong' && text(node) === '0').length, 3);
});

test('renders null PSU, null author and empty notes as missing, not zero', () => {
  for (const notes of ['', '  \n\t ', null]) {
    const history = createHistory([measurement({ salinity_psu: null, user: null, notes })]);
    const rowCells = cells(history.rows()[0]);
    assert.deepEqual(rowCells.slice(3).map(cellValue), ['—', '—', '—']);
    assert.equal(React.Children.toArray(rowCells[3].props.children)[1].props.className, 'is-missing');
    assert.equal(history.find((node) => node.type === 'button' && Object.hasOwn(node.props, 'aria-expanded')).length, 0);
    assert.match(history.html, /class="is-missing">—<\/strong>/);
  }
});

test('shows empty history with a zero count and no show-more control', () => {
  const history = createHistory([]);
  assert.equal(history.rows().length, 0);
  assert.equal(history.status(), '0 of 0 measurements');
  assert.equal(history.more().length, 0);
  assert.match(history.html, /No measurement history/);
  assert.deepEqual(history.find((node) => node.type === 'option').map(text), [labels.historyAllYears]);
});

test('trims short notes, formats dates with the real helper and escapes note text', () => {
  const note = '<script>alert("note")</script> & lab';
  const history = createHistory([measurement({ notes: `  ${note}  `, salinity_psu: '32.25' })]);
  assert.equal(cellValue(cells(history.rows()[0])[5]), note);
  assert.equal(cellValue(cells(history.rows()[0])[3]), '32.3');
  const time = history.find((node) => node.type === 'time')[0];
  assert.equal(time.props.dateTime, '2026-09-16');
  assert.equal(text(time), dateFormat.formatDisplayDate('2026-09-16'));
  assert.match(history.html, /&lt;script&gt;alert\(&quot;note&quot;\)&lt;\/script&gt; &amp; lab/);
  assert.doesNotMatch(history.html, /<script>|aria-expanded/);
});

test('only notes longer than 140 characters toggle and expansion is independent per row', () => {
  const firstNote = 'A'.repeat(141);
  const secondNote = 'B'.repeat(160);
  const history = createHistory([
    measurement({ id: 1, notes: ` ${firstNote} ` }),
    measurement({ id: 2, notes: secondNote }),
    measurement({ id: 3, notes: 'C'.repeat(140) }),
  ]);
  const buttons = () => history.find((node) => node.type === 'button' && Object.hasOwn(node.props, 'aria-expanded'));
  const notes = () => history.find((node) => node.type === 'p');
  assert.deepEqual(buttons().map((node) => node.props['aria-expanded']), [false, false]);
  assert.deepEqual(notes().map((node) => node.props.className), ['is-collapsed', 'is-collapsed', '']);
  assert.deepEqual(notes().map(text), [firstNote, secondNote, 'C'.repeat(140)]);
  buttons()[0].props.onClick();
  history.render();
  assert.deepEqual(buttons().map((node) => node.props['aria-expanded']), [true, false]);
  assert.deepEqual(buttons().map(text), [labels.historyHideComment, labels.historyReadComment]);
  assert.deepEqual(notes().map((node) => node.props.className), ['', 'is-collapsed', '']);
  assert.match(history.html, /aria-expanded="true">Hide comment/);
  buttons()[1].props.onClick();
  history.render();
  assert.deepEqual(buttons().map((node) => node.props['aria-expanded']), [true, true]);
  buttons()[0].props.onClick();
  history.render();
  assert.deepEqual(buttons().map((node) => node.props['aria-expanded']), [false, true]);
  assert.equal(text(notes()[0]), firstNote);
});

test('links the modal title, exposes table semantics and wires close and backdrop actions', () => {
  let closed = 0;
  const history = createHistory([measurement()], { onClose() { closed += 1; } });
  const dialog = history.find((node) => node.props.role === 'dialog')[0];
  const title = history.find((node) => node.type === 'h2')[0];
  assert.equal(dialog.props['aria-modal'], 'true');
  assert.equal(dialog.props['aria-labelledby'], title.props.id);
  assert.ok(title.props.id);
  assert.equal(title.props.tabIndex, -1);
  assert.equal(text(title), labels.measurementHistory);
  const titleId = title.props.id;
  history.render();
  assert.equal(history.find((node) => node.type === 'h2')[0].props.id, titleId);
  const table = history.find((node) => node.props.role === 'table')[0];
  assert.equal(table.props['aria-label'], labels.measurementHistory);
  assert.equal(table.props.tabIndex, 0);
  assert.equal(history.find((node) => node.props.role === 'columnheader').length, 6);
  assert.match(history.html, /role="dialog" aria-modal="true" aria-labelledby="history-title-/);
  assert.match(history.html, /BOX-42/);
  history.find((node) => node.type === 'button' && node.props['aria-label'] === labels.close)[0].props.onClick();
  assert.equal(closed, 1);
  let stopped = false;
  dialog.props.onClick({ stopPropagation() { stopped = true; } });
  assert.equal(stopped, true);
  assert.equal(closed, 1);
  history.find((node) => node.props.role === 'presentation')[0].props.onClick();
  assert.equal(closed, 2);
});
