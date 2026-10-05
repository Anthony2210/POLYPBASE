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
const catalogs = {
  fr: loadModule('../src/i18n/fr.ts').fr,
  en: loadModule('../src/i18n/en.ts').en,
};
const i18n = loadModule('../src/i18n/index.ts', { './fr': { fr: catalogs.fr }, './en': { en: catalogs.en } });
const biologicalTimeline = loadModule('../src/utils/biologicalTimeline.ts');
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
    user_identity: { first_name: '', last_name: '', email: `author-${firstId + index}@example.org` },
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
    '../utils/userIdentity': loadModule('../src/utils/userIdentity.ts'),
    '../i18n': i18n,
    '../utils/biologicalTimeline': biologicalTimeline,
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
    bindList(element) { find((node) => node.props.role === 'table')[0].ref.current = element; },
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
    Object.freeze(measurement({ id: 1, measured_on: '2025-12-31', created_at: '2027-01-01T00:00:00Z', user: 'older date', user_identity: { first_name: '', last_name: '', email: 'older@example.org' } })),
    Object.freeze(measurement({ id: 2, created_at: '2026-09-16T08:00:00Z', user: 'earlier entry', user_identity: { first_name: '', last_name: '', email: 'earlier@example.org' } })),
    Object.freeze(measurement({ id: 3, created_at: '2026-09-16T12:00:00Z', user: 'later entry', user_identity: { first_name: '', last_name: '', email: 'later@example.org' } })),
    Object.freeze(measurement({ id: 4, measured_on: '2026-09-17', created_at: '2026-01-01T00:00:00Z', user: 'newest date', user_identity: { first_name: '', last_name: '', email: 'newest@example.org' } })),
  ]);
  const before = JSON.stringify(input);
  const history = createHistory(input);
  assert.deepEqual(rowAuthors(history), ['newest@example.org', 'later@example.org', 'earlier@example.org', 'older@example.org']);
  history.changeYear('2026');
  assert.deepEqual(rowAuthors(history), ['newest@example.org', 'later@example.org', 'earlier@example.org']);
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
    assert.deepEqual(rowCells.slice(3).map(cellValue), ['—', catalogs.fr.historicalUser, '—']);
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

for (const language of ['fr', 'en']) {
  test(`${language}: measurement and operation history use structured names, email or historical fallback`, () => {
    const identities = [
      { first_name: 'ÉLISE-Anne', last_name: 'du Pont-Müller', email: 'fallback@example.org' },
      { first_name: 'LÉA', last_name: '', email: '' },
      { first_name: '', last_name: 'de la tour', email: '' },
      { first_name: '', last_name: '', email: 'person@example.org' },
      null,
    ];
    const expected = ['Élise-Anne DU PONT-MÜLLER', 'Léa', 'DE LA TOUR', 'person@example.org', catalogs[language].historicalUser];
    for (let index = 0; index < identities.length; index++) {
      const user_identity = identities[index];
      const history = createHistory([measurement({ user: 'legacy-measurement', user_identity })], {
        language,
        biologicalTimeline: [operation({ user_identity, author: { username: 'legacy-subculture' } })],
      });
      assert.deepEqual(rowAuthors(history), [expected[index], expected[index]]);
      assert.doesNotMatch(history.html, /legacy-measurement|legacy-subculture/);
    }
  });
}

test('raw compatibility usernames never become readable authors in FR or EN', () => {
  const authors = ['internal_unchanged', 'raw.USERNAME', 'technician@example.org'];
  for (const language of ['fr', 'en']) {
    const history = createHistory(authors.map((user, index) => measurement({ id: index + 1, user })), { language });
    assert.deepEqual(rowAuthors(history), authors.map(() => catalogs[language].historicalUser));
    for (const author of authors) assert.equal(history.html.includes(author), false);
  }
});

test('later batches and year changes preserve recorded zero versus absent PSU and count progress', () => {
  const history = createHistory([
    ...measurementsForYear(2026, 53).map((row, index) => ({
      ...row, polyp_count: 0, ephyrae_count: 0, salinity_psu: index % 2 ? null : '0',
    })),
    measurement({ id: 100, measured_on: '2025-01-01', polyp_count: 0, ephyrae_count: 0, salinity_psu: null }),
  ]);
  history.changeYear('2026');
  assert.equal(history.status(), '24 of 53 measurements');
  history.showMore();
  assert.equal(history.status(), '48 of 53 measurements');
  history.showMore();
  assert.equal(history.status(), '53 of 53 measurements');
  for (const row of history.rows()) {
    const values = cells(row).slice(1, 4).map(cellValue);
    assert.deepEqual(values.slice(0, 2), ['0', '0']);
    assert.ok(['0', '—'].includes(values[2]));
  }
  assert.equal(history.rows().filter(row => cellValue(cells(row)[3]) === '0').length, 27);
  assert.equal(history.rows().filter(row => cellValue(cells(row)[3]) === '—').length, 26);
  history.changeYear('2025');
  assert.equal(history.status(), '1 of 1 measurements');
  assert.deepEqual(cells(history.rows()[0]).slice(1, 4).map(cellValue), ['0', '0', '—']);
  history.changeYear('all');
  assert.equal(history.status(), '24 of 54 measurements');
});

test('year selection resets list scroll and final batch moves focus into the retained reading area', () => {
  const history = createHistory(measurementsForYear(2026, 53));
  const focusCalls = [];
  const list = { scrollTop: 200, focus(options) { focusCalls.push(options); } };
  history.bindList(list);
  history.changeYear('2026');
  assert.equal(list.scrollTop, 0);
  assert.equal(history.showMore(), true, 'intermediate batch retains button focus');
  assert.equal(focusCalls.length, 0);
  history.showMore();
  assert.equal(history.more().length, 0);
  assert.equal(focusCalls.length, 1);
  assert.equal(focusCalls[0].preventScroll, true);
});

function operation(overrides = {}) {
  const entry = {
    kind: 'subculture', id: 1,
    effective_date: '2026-09-17', timestamp: '2026-09-17T09:15:00Z', state_sequence: 2,
    author: { id: 2, username: 'subculture-author' },
    polyp_count_before: 12, polyp_count_after: 0, allocated_polyps: 12,
    allocations: [
      { id: 1, position: 1, child_box_id: 3, child_global_code: 'CHILD-003', allocated_polyps: 0 },
      { id: 2, position: 0, child_box_id: 2, child_global_code: 'CHILD-002', allocated_polyps: 12 },
    ],
    children: [{ id: 2, global_code: 'CHILD-002' }, { id: 3, global_code: 'CHILD-003' }],
    notes: '', can_edit: false,
    ...overrides,
  };
  return { ...entry, identity: overrides.identity ?? `${entry.kind}:${entry.id}` };
}
function details(history, row = history.rows()[0]) {
  return history.find(node => node.type === 'dl', row)[0];
}
function detailValues(history, row) {
  return history.find(node => node.type === 'dd', details(history, row)).map(text);
}

for (const language of ['fr', 'en']) {
  test(`${language}: history distinguishes measurements, parent allocations and child initialization`, () => {
    const catalog = catalogs[language];
    const t = i18n.createTranslator(language);
    const reading = Object.freeze(measurement({ polyp_count: 12, ephyrae_count: 3, salinity_psu: '32.25', strobila_count: 7 }));
    const timeline = Object.freeze([
      Object.freeze({ ...operation({ kind: 'measurement', effective_date: reading.measured_on, state_sequence: 1 }), measurement: reading, polyp_count_after: 999 }),
      Object.freeze(operation()),
      Object.freeze(operation({ kind: 'subculture_initialization', effective_date: '2026-09-18', state_sequence: 3,
        parent: { id: 42, global_code: 'PARENT-042' }, event_id: 1, allocated_polyps: 0, polyp_count_after: 0, allocations: [], children: [] })),
    ]);
    const before = JSON.stringify({ reading, timeline });
    const history = createHistory([reading], { biologicalTimeline: timeline, language, labels: { ...labels, ...catalog,
      historyVisibleCount: (visible, total) => catalog.historyVisibleCount.replace('{visible}', visible).replace('{total}', total) } });
    assert.equal(history.rows().length, 3, 'embedded readings are deduplicated, not hidden or fabricated');
    assert.deepEqual(history.rows().map(row => row.props['data-entry-kind']), ['subculture_initialization', 'subculture', 'measurement']);
    assert.equal(new Set(history.rows().map(row => row.key)).size, 3, 'numeric IDs across tables cannot collide');
    assert.deepEqual(history.find(node => node.props.className === 'measurement-history-type').map(text), [
      `${catalog.subcultureEvent} — ${catalog.auditMetaInitialPolypCounts}`, catalog.subcultureEvent, catalog.auditObjectMeasurement,
    ]);
    assert.deepEqual(cells(history.rows()[2]).slice(1, 4).map(cellValue), ['12', '3', '32.3'], 'real counts are never replaced by operation snapshots');
    for (const row of history.rows().slice(0, 2)) assert.deepEqual(cells(row).slice(1, 4).map(cellValue), ['0', '—', '—']);
    const parent = history.rows()[1];
    assert.deepEqual(detailValues(history, parent), [
      `${catalog.auditMetaBefore}: 12 → ${catalog.auditMetaAfter}: 0`, '12', 'CHILD-002: 12CHILD-003: 0',
    ]);
    assert.deepEqual(history.find(node => node.type === 'dt', details(history, parent)).map(text), [
      catalog.confirmDetailParentBox, biologicalTimeline.getBiologicalTimelineLabels(t).allocatedPolyps, catalog.confirmDetailChildren,
    ]);
    assert.deepEqual(detailValues(history, history.rows()[0]), ['PARENT-042', '0']);
    const times = history.find(node => node.type === 'time', parent);
    assert.deepEqual(times.map(node => node.props.dateTime), ['2026-09-17', '2026-09-17T09:15:00Z']);
    assert.equal(text(times[1]), dateFormat.formatDisplayDateTime(timeline[1].timestamp));
    assert.equal(JSON.stringify({ reading, timeline }), before, 'exact historical payloads and allocation arrays remain unchanged');
    assert.equal(history.find(node => node.type === 'button', parent).length, 0, 'subculture cannot offer a correction action');
  });
}

for (const language of ['fr', 'en']) {
  test(`${language}: partial subculture history preserves zero and unknown child allocations`, () => {
    const history = createHistory([], { language, biologicalTimeline: [operation({
      polyp_count_before: 100, polyp_count_after: null, allocated_polyps: null,
      allocations: [
        { position: 0, child_box_id: 2, child_global_code: 'CHILD-002', allocated_polyps: 0 },
        { position: 1, child_box_id: 3, child_global_code: 'CHILD-003', allocated_polyps: null },
      ],
    })] });
    const row = history.rows()[0];
    assert.equal(cellValue(cells(row)[1]), '—');
    assert.ok(text(row).includes('CHILD-002: 0'));
    assert.ok(text(row).includes(`CHILD-003: ${catalogs[language].subcultureUnknown}`));
    assert.equal(text(row).includes('null'), false);
    assert.equal(history.find(node => node.type === 'button', row).length, 0);
  });
}

test('legacy subculture retains lineage but never fabricates zero counts or occurrence timestamps', () => {
  const history = createHistory([], { biologicalTimeline: [operation({
    timestamp: null, polyp_count_before: null, polyp_count_after: null, allocated_polyps: null,
    allocations: [], author: { username: null },
  })] });
  const row = history.rows()[0];
  assert.equal(history.rows().length, 1);
  assert.deepEqual(cells(row).slice(1, 5).map(cellValue), ['—', '—', '—', catalogs.fr.historicalUser]);
  assert.deepEqual(detailValues(history, row), ['Avant: — → Après: —', '—', 'CHILD-002CHILD-003']);
  assert.equal(history.find(node => node.type === 'time').length, 1);
  assert.doesNotMatch(history.html, />0<|No measurement history/);
});

test('timeline-only real readings retain their payload and edit capability without reclassifying legacy readings', () => {
  const reading = Object.freeze(measurement({ notes: 'legacy initialization reading', polyp_count: 0, ephyrae_count: 0, salinity_psu: '0', can_edit: true }));
  const history = createHistory([], { biologicalTimeline: [
    { kind: 'measurement', id: reading.id, identity: `measurement:${reading.id}`, effective_date: reading.measured_on,
      timestamp: reading.created_at, measurement: reading, polyp_count_after: 500, can_edit: false },
    operation({ can_edit: true }),
  ] });
  const row = history.rows().find(entry => entry.props['data-entry-kind'] === 'measurement');
  assert.deepEqual(cells(row).slice(1, 4).map(cellValue), ['0', '0', '0']);
  assert.equal(cellValue(cells(row)[5]), reading.notes);
  assert.equal(reading.can_edit, true);
  assert.equal(history.find(node => node.type === 'button' && text(node) === catalogs.fr.correctMeasurement).length, 0,
    'this modal stays read-only; correction remains in the existing measurement editor');
});

test('same-day typed sources sort by server sequence, not numeric IDs or wall-clock ties', () => {
  const reading = measurement({ id: 999, measured_on: '2026-09-17', created_at: '2026-09-17T23:59:00Z' });
  const history = createHistory([reading], { biologicalTimeline: [
    { kind: 'measurement', id: reading.id, effective_date: reading.measured_on, state_sequence: 1, measurement: reading },
    operation({ id: 7, state_sequence: 2 }),
    operation({ id: 2, state_sequence: 3 }),
  ] });
  assert.deepEqual(history.rows().map(row => row.key), ['subculture:2', 'subculture:7', 'measurement:999']);
});

test('typed identities keep long-note expansion independent across overlapping IDs and year filters', () => {
  const history = createHistory([measurement({ notes: 'M'.repeat(141) })], { biologicalTimeline: [
    operation({ notes: 'S'.repeat(141) }),
    operation({ kind: 'subculture_initialization', notes: 'I'.repeat(141), effective_date: '2025-12-31', allocations: [], children: [] }),
  ] });
  const buttons = () => history.find(node => node.type === 'button' && Object.hasOwn(node.props, 'aria-expanded'));
  buttons()[0].props.onClick();
  history.render();
  assert.deepEqual(buttons().map(node => node.props['aria-expanded']), [true, false, false]);
  buttons()[1].props.onClick();
  history.render();
  assert.deepEqual(buttons().map(node => node.props['aria-expanded']), [true, true, false]);
  history.changeYear('2025');
  assert.deepEqual(buttons().map(node => node.props['aria-expanded']), [false]);
  history.changeYear('all');
  assert.deepEqual(buttons().map(node => node.props['aria-expanded']), [true, true, false]);
});

test('mixed entries share pagination and year filtering while preserving scroll and final-batch focus', () => {
  const readings = measurementsForYear(2026, 25);
  const timeline = [
    ...Array.from({ length: 25 }, (_, index) => operation({ id: index + 1 })),
    operation({ id: 30, kind: 'subculture_initialization', effective_date: '2024-01-01', allocations: [], children: [] }),
  ];
  const history = createHistory(readings, { biologicalTimeline: timeline });
  const calls = [];
  const list = { scrollTop: 400, focus(options) { calls.push(options); } };
  history.bindList(list);
  assert.deepEqual(history.find(node => node.type === 'option').map(node => node.props.value), ['all', '2026', '2024']);
  assert.equal(history.rows().length, 24);
  assert.equal(history.status(), '24 of 51 measurements');
  history.showMore();
  assert.equal(list.scrollTop, 400, 'loading a batch does not jump the reading area');
  assert.equal(history.rows().length, 48);
  history.showMore();
  assert.equal(history.rows().length, 51);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].preventScroll, true);
  history.changeYear('2024');
  assert.equal(list.scrollTop, 0);
  assert.equal(history.rows().length, 1);
  assert.equal(history.rows()[0].props['data-entry-kind'], 'subculture_initialization');
  history.changeYear('2026');
  assert.equal(history.rows().length, 24);
  assert.equal(history.status(), '24 of 50 measurements');
});

test('missing optional timeline preserves history and duplicate typed events appear only once', () => {
  const reading = measurement();
  assert.equal(createHistory([reading], { biologicalTimeline: [] }).rows().length, 1);
  const history = createHistory([reading], { biologicalTimeline: [
    { kind: 'measurement', id: reading.id, effective_date: reading.measured_on },
    operation(), operation(),
  ] });
  assert.deepEqual(history.rows().map(row => row.key), ['subculture:1', 'measurement:1']);
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
