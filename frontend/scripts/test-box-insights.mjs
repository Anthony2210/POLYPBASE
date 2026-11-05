import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const css = read('../src/styles/pages/box-insights.css');
function loadModule(path, imports = {}) {
  const exports = {};
  const { outputText } = ts.transpileModule(read(path), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
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
const i18n = loadModule('../src/i18n/index.ts', {
  './fr': { fr: catalogs.fr }, './en': { en: catalogs.en }, './ja': loadModule('../src/i18n/ja.ts'),
});
const biologicalTimeline = loadModule('../src/utils/biologicalTimeline.ts');
function text(node) {
  if (node == null || typeof node === 'boolean') return '';
  if (Array.isArray(node)) return node.map(text).join('');
  return React.isValidElement(node) ? text(node.props.children) : String(node);
}
function resolve(node) {
  if (Array.isArray(node)) return node.map(resolve);
  if (!React.isValidElement(node)) return node;
  if (typeof node.type === 'function') return resolve(node.type(node.props));
  const children = resolve(node.props.children);
  return Array.isArray(children) ? React.cloneElement(node, {}, ...children) : React.cloneElement(node, {}, children);
}
function labelsFor(language) {
  const t = catalogs[language];
  return {
    ...t,
    measurementsTab: t.analysisTabMeasurements,
    movementsTab: t.analysisTabMovements,
    lineageTab: t.analysisTabLineage,
  };
}
function movement(overrides = {}) {
  return {
    id: 1,
    moved_at: '2026-09-16T08:00:00Z',
    from_thermal_zone: { id: 2, name: 'Origin' },
    to_thermal_zone: { id: 3, name: 'Destination' },
    user: 'internal_unchanged',
    notes: '',
    ...overrides,
  };
}
function createInsights(overrides = {}) {
  const slots = [];
  let cursor = 0;
  let height = 480.2;
  let tree;
  let chartProps;
  const selections = [];
  const hooks = {
    ...React,
    lazy: () => () => React.createElement('div', { className: 'lineage-graph-stub' }),
    useMemo: factory => factory(),
    useRef: () => ({ current: { getBoundingClientRect: () => ({ height }) } }),
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
  };
  const { default: BoxInsights } = loadModule('../src/components/BoxInsights.tsx', {
    react: hooks,
    'react/jsx-runtime': jsxRuntime,
    '../utils/dateFormat': dateFormat,
    '../utils/userIdentity': loadModule('../src/utils/userIdentity.ts'),
    '../i18n': i18n,
    '../utils/biologicalTimeline': biologicalTimeline,
    '../utils/boxHistory': loadModule('../src/utils/boxHistory.ts'),
    '../hooks/useBoxHistory': { useBoxHistory: () => ({
      events: props.historyEvents ?? [...props.movements].sort((a, b) => b.moved_at.localeCompare(a.moved_at)).map(row => ({
        ...row, id: `movement:${row.id}`, recorded_movement_id: row.id, kind: row.kind ?? 'ordinary',
        occurred_at: row.moved_at, temporal_precision: 'exact', actor: row.user_identity,
        provenance: 'box_movement', evidence: {}, reconciliation_status: 'recorded', can_correct: false,
      })), candidates: props.historyCandidates, assessments: props.historyAssessments, loading: props.historyLoading ?? false, failed: props.historyFailed ?? false, retry() {},
    }) },
    './BoxHistoryStatus': { default: () => null },
    './PolypbaseIcon': { default: () => null },
    './BoxTrackingChart': {
      buildLifecycleEvents: () => [],
      default: props => { chartProps = props; return React.createElement('div', { className: 'chart-stub' }); },
    },
    './ModalPortal': { default: ({ children }) => children },
  });
  const props = {
    activeTab: 'movements', graph: null, graphError: null, isGraphLoading: false,
    labels: labelsFor('fr'), language: 'fr', lineage: { parents: [], children: [] },
    movements: [], measurements: [], locations: [],
    onLoadLineageGraph() {}, onOpenHistory() {}, onSelectBox() {},
    onSelectTab(tab) { selections.push(tab); props.activeTab = tab; },
    ...overrides,
  };
  function render() { cursor = 0; tree = resolve(BoxInsights(props)); }
  function find(predicate, node) {
    if (arguments.length === 1) node = tree;
    if (Array.isArray(node)) return node.flatMap(child => find(predicate, child));
    if (!React.isValidElement(node)) return [];
    return [...(predicate(node) ? [node] : []), ...find(predicate, node.props.children)];
  }
  render();
  return {
    find, selections,
    get html() { return renderToStaticMarkup(tree); },
    get chartProps() { return chartProps; },
    select(index, measuredHeight = height) {
      height = measuredHeight;
      find(node => node.props.role === 'tab')[index].props.onClick();
      render();
    },
    panel: () => find(node => node.props.className?.split(' ').includes('insight-panel'))[0],
    rows: () => find(node => node.type === 'li'),
  };
}

for (const language of ['fr', 'en']) {
  test(`${language}: all translated tabs retain their text, button semantics and selected state`, () => {
    const h = createInsights({ labels: labelsFor(language), language });
    const expected = [catalogs[language].analysisTabMeasurements, catalogs[language].analysisTabMovements, catalogs[language].analysisTabLineage];
    assert.equal(h.find(node => node.props.role === 'tablist').length, 1);
    for (const index of [0, 1, 2]) {
      h.select(index);
      const tabs = h.find(node => node.props.role === 'tab');
      assert.deepEqual(tabs.map(text), expected);
      assert.equal(tabs[index].props['aria-selected'], true);
      assert.equal(tabs[index].props.className, 'is-active');
      for (const [i, tab] of tabs.entries()) {
        assert.equal(tab.type, 'button');
        assert.equal(tab.props.type, 'button');
        assert.equal(tab.props['aria-selected'], i === index);
        assert.notEqual(tab.props.tabIndex, -1, 'native keyboard focus is retained');
      }
    }
    assert.deepEqual(h.selections, ['measurements', 'movements', 'lineage']);
  });
}

test('unified history keeps week precision, unresolved evidence, notes and backend correction gates', () => {
  const historical = { id: 'historical:1', kind: 'historical_reconstruction', temporal_precision: 'iso_week',
    occurred_at: null, iso_year: 2026, iso_week: 1, actor: null, evidence: { accepted_by: { first_name: 'Reviewer' } },
    provenance: 'reviewed_week_reconstruction', reconciliation_status: 'accepted', recorded_movement_id: null,
    can_correct: false, notes: 'Reviewed source', from_thermal_zone: null, to_thermal_zone: null };
  const ordinary = { ...historical, id: 'movement:2', kind: 'ordinary', temporal_precision: 'exact',
    occurred_at: '2026-01-01T08:00:00Z', can_correct: true, recorded_movement_id: 2 };
  for (const language of ['fr', 'en']) {
    let corrected;
    const h = createInsights({ language, labels: labelsFor(language),
      historyEvents: [historical, ordinary, { ...historical, id: 'location:3:start', kind: 'location_evidence', temporal_precision: 'unresolved' }],
      correctableMovementId: 2, correctMovementLabel: 'Correct', onCorrectMovement: id => { corrected = id; } });
    assert.equal(h.rows().length, 3);
    assert.equal(h.find(node => node.type === 'time').length, 1);
    assert.match(h.html, language === 'fr' ? /Semaine 1 de 2026/ : /Week 1 of 2026/);
    assert.match(h.html, new RegExp(catalogs[language].historyUnresolved));
    const buttons = h.find(node => node.props.className?.includes('movement-correct-button'));
    assert.equal(buttons.length, 1);
    buttons[0].props.onClick();
    assert.equal(corrected, 2);
    const denied = createInsights({ historyEvents: [{ ...ordinary, can_correct: false }], correctableMovementId: 2,
      onCorrectMovement() {} });
    assert.equal(denied.find(node => node.type === 'button' && node.props.className?.includes('movement-correct-button')).length, 0);
  }
});

test('blocked candidates show localized transition reasons and evidence, and unresolved persisted rows never claim acceptance', () => {
  for (const language of ['fr', 'en']) {
    const historical = { id: 'historical:1', kind: 'historical_reconstruction', temporal_precision: 'unresolved',
      occurred_at: null, iso_year: null, iso_week: null, actor: null,
      provenance: 'unverified_week_reconstruction', reconciliation_status: 'unresolved',
      evidence: { accepted_by: { first_name: 'Reviewer' }, stored_iso_year: 2026, stored_iso_week: 1 },
      recorded_movement_id: null, can_correct: false, notes: '', from_thermal_zone: null, to_thermal_zone: null };
    const candidate = { id: 'c1', box_id: 1, iso_year: 2026, iso_week: 2,
      from_thermal_zone_id: 2, to_thermal_zone_id: 3, reconciliation_status: 'blocked',
      blockers: ['source_location_provenance_unresolved', 'adjacent_measurement_evidence_missing', 'new_backend_reason'],
      evidence: { physical_occurrence_proven: false, measurement_ids: [0, 1] } };
    const h = createInsights({ language, labels: labelsFor(language), historyEvents: [historical], historyCandidates: [candidate],
      locations: [{ thermal_zone: { id: 2, name: 'Origin' } }, { thermal_zone: { id: 3, name: 'Destination' } }] });
    assert.match(h.html, new RegExp(catalogs[language].historyCandidate));
    for (const key of ['historyBlocked', 'historyReasonSource', 'historyReasonRecords', 'historyReasonUnknown', 'historyReconstructionUnresolved'])
      assert.ok(h.html.includes(catalogs[language][key]), key);
    assert.equal(h.html.includes(catalogs[language].historyAcceptedReconstruction), false);
    assert.equal(h.find(node => node.type === 'time').length, 0);
    assert.ok(h.html.includes('physical_occurrence_proven'));
    assert.ok(h.find(node => node.props.className === 'movement-locations').map(text).includes('Origin → Destination'));
    assert.equal(h.find(node => node.props.className?.includes('movement-correct-button')).length, 0);
  }
});

test('final contract renders only actionable candidate diagnostics, marks proposed weeks, and preserves missing-week assessments', () => {
  for (const language of ['fr', 'en']) {
    const base = { box_id: 7, iso_year: 2026, iso_week: 1, from_thermal_zone_id: 2, to_thermal_zone_id: 3,
      blockers: [], evidence: { attributed_week_start: '2025-12-29', destination_week_start: '2026-01-05' } };
    const h = createInsights({ language, labels: labelsFor(language),
      movements: [movement()],
      historyCandidates: [
        { ...base, id: 'accepted', classification: 'accepted', reconciliation_status: 'accepted' },
        { ...base, id: 'recorded', classification: 'already_represented', reconciliation_status: 'already_represented' },
        { ...base, id: 'review', classification: 'eligible', reconciliation_status: 'review_required' },
        { ...base, id: 'blocked', classification: 'ambiguous', reconciliation_status: 'blocked', blockers: ['measurement_week_inconsistent'] },
      ],
      historyAssessments: [{ ...base, id: 'missing-week', classification: 'insufficient_evidence', reconciliation_status: 'blocked',
        blockers: ['adjacent_measurement_evidence_missing'] },
      { id: 'missing-location', box_id: 7, classification: 'insufficient_evidence', reconciliation_status: 'blocked',
        blockers: ['location_history_missing'], evidence: {} }],
    });
    assert.equal(h.find(node => node.props.className === 'movement-event').length, 5);
    const titles = h.find(node => node.props.className === 'movement-event').map(text);
    assert.equal(titles.filter(title => title === catalogs[language].historyCandidate).length, 2);
    assert.equal(titles.filter(title => title === catalogs[language].historyAssessment).length, 2);
    assert.equal(titles.includes(catalogs[language].historyReconstruction), false);
    assert.equal(h.find(node => node.type === 'time').length, 1);
    assert.equal(h.html.includes(catalogs[language].historyWeekOnly), false);
    assert.equal(h.html.includes(catalogs[language].historyAcceptedReconstruction), false);
    const proposal = catalogs[language].historyProposedWeek.replace('{week}', catalogs[language].historyIsoWeek
      .replace('{week}', '1').replace('{year}', '2026'));
    assert.ok(h.find(node => node.type === 'small').some(node => text(node) === proposal));
    for (const key of ['historyReasonRecordWeek', 'historyReasonRecords', 'historyReasonMissingLocations', 'historyUnresolved'])
      assert.ok(h.html.includes(catalogs[language][key]), key);
    assert.ok(h.html.includes('insufficient_evidence'));
  }
});

test('loading and failed history never claim that the history is empty', () => {
  for (const overrides of [{ historyLoading: true }, { historyFailed: true }]) {
    const h = createInsights(overrides);
    assert.equal(h.find(node => node.props.className?.includes('movement-empty')).length, 0);
  }
});

test('empty movement history is compact and contains no invented events', () => {
  const h = createInsights();
  assert.equal(h.rows().length, 0);
  assert.equal(text(h.find(node => node.props.className?.includes('movement-empty'))[0]), catalogs.fr.noMovementHistory);
  assert.equal(h.panel().props.style, undefined);
  assert.equal(h.find(node => node.type === 'time').length, 0);
});

test('one movement keeps actual locations, timestamp, structured author and notes', () => {
  const row = movement({ user_identity: { first_name: 'Élise', last_name: 'du Pont-Martin', email: '' }, notes: 'Line one\nLine two <script>not markup</script>' });
  const h = createInsights({ movements: [row] });
  assert.equal(h.rows().length, 1);
  const time = h.find(node => node.type === 'time')[0];
  assert.equal(time.props.dateTime, row.moved_at);
  assert.equal(text(time), dateFormat.formatDisplayDateTime(row.moved_at));
  const locations = h.find(node => node.props.className === 'movement-locations')[0];
  assert.deepEqual(h.find(node => node.type === 'span', locations).map(text), ['Origin', '→', 'Destination']);
  assert.equal(text(h.find(node => node.type === 'small')[0]), 'Élise DU PONT-MARTIN');
    assert.doesNotMatch(h.html, /internal_unchanged/);
  assert.equal(text(h.find(node => node.type === 'p')[0]), row.notes);
  assert.match(h.html, /&lt;script&gt;not markup&lt;\/script&gt;/);
  assert.equal(h.find(node => node.props.className === 'movement-event').length, 1);
  assert.equal(h.find(node => node.type === 'ol')[0].props.role, 'list');
});

for (const language of ['fr', 'en']) {
  test(`${language}: historical movement authors ignore arbitrary raw usernames`, () => {
    for (const user of ['internal_opaque', 'legacy.tech', 'raw@example.org']) {
      const h = createInsights({ language, labels: labelsFor(language), movements: [movement({ user, user_identity: null })] });
      assert.equal(text(h.find(node => node.type === 'small')[0]), catalogs[language].historyActorUnknown);
      assert.equal(h.html.includes(user), false);
    }
  });
}

test('multiple movements sort newest first including same-day times without mutating input', () => {
  const input = Object.freeze([
    Object.freeze(movement({ id: 1, moved_at: '2025-12-31T20:00:00Z' })),
    Object.freeze(movement({ id: 2, moved_at: '2026-09-16T08:00:00Z' })),
    Object.freeze(movement({ id: 3, moved_at: '2026-09-16T12:00:00Z' })),
  ]);
  const before = JSON.stringify(input);
  const h = createInsights({ movements: input });
  assert.equal(h.rows().length, 3);
  assert.deepEqual(h.find(node => node.type === 'time').map(node => node.props.dateTime), [input[2].moved_at, input[1].moved_at, input[0].moved_at]);
  assert.equal(JSON.stringify(input), before);
});

test('missing origin does not invent a previous place, author or note', () => {
  const h = createInsights({ movements: [movement({ from_thermal_zone: null, user: null })] });
  const locations = h.find(node => node.props.className === 'movement-locations')[0];
  assert.deepEqual(h.find(node => node.type === 'span', locations).map(text), [catalogs.fr.movedTo, 'Destination']);
  assert.equal(h.find(node => node.props.className === 'movement-arrow').length, 0);
  assert.equal(text(h.find(node => node.type === 'small')[0]), catalogs.fr.historyActorUnknown);
  const detail = h.find(node => node.props.className === 'movement-detail')[0];
  assert.equal(detail.props.children.filter(node => React.isValidElement(node) && node.type === 'p').length, 0);
});

test('long locations are rendered in full, with no directional color contract', () => {
  const origin = 'Origin'.repeat(60);
  const destination = 'Destination'.repeat(60);
  const h = createInsights({ movements: [movement({ from_thermal_zone: { name: origin }, to_thermal_zone: { name: destination } })] });
  assert.ok(h.html.includes(origin));
  assert.ok(h.html.includes(destination));
  assert.doesNotMatch(h.html, /is-arrival|is-departure|is-entry|is-exit/);
});

test('movement history inherits analytical height without inflating charts on return', () => {
  const h = createInsights({ activeTab: 'measurements' });
  h.select(1, 480.2);
  assert.equal(h.panel().props.className, 'insight-panel insight-panel--movements');
  assert.equal(h.panel().props.style.minHeight, 481, 'movement panel preserves the chart height');
  h.select(2, 1600);
  assert.equal(h.panel().props.style.minHeight, 481, 'long movement lists cannot inflate lineage');
  h.select(0, 530.1);
  assert.equal(h.panel().props.style.minHeight, 531, 'measurement/lineage stabilization is preserved');
  h.select(1, 350);
  assert.equal(h.panel().props.style.minHeight, 531, 'all tabs keep the reserved analytical height');
  h.select(0, 2000);
  assert.equal(h.panel().props.style.minHeight, 531);
});

test('typed timeline and unchanged historical readings pass through to the chart', () => {
  const measurements = Object.freeze([{ id: 7, polyp_count: 50, ephyrae_count: 0 }]);
  const timeline = Object.freeze([
    Object.freeze({ kind: 'subculture', id: 7, identity: 'subculture:7', effective_date: '2026-10-04', polyp_count_before: 50, polyp_count_after: 0, allocated_polyps: 50 }),
    Object.freeze({ kind: 'subculture_initialization', id: 7, identity: 'subculture_initialization:7', effective_date: '2026-10-04', polyp_count_after: 0, allocated_polyps: 0 }),
  ]);
  for (const language of ['fr', 'en']) {
    const h = createInsights({ activeTab: 'measurements', biologicalTimeline: timeline, measurements, language, labels: labelsFor(language) });
    assert.equal(h.chartProps.biologicalTimeline, timeline);
    assert.equal(h.chartProps.measurements, measurements);
    assert.equal(h.chartProps.language, language);
    assert.equal(measurements[0].polyp_count, 50);
  }
  assert.equal(createInsights({ activeTab: 'measurements' }).chartProps.biologicalTimeline, undefined);
});

test('opening details still delegates the exact existing callback and payload to the chart', () => {
  let opened = 0;
  const measurements = [{ id: 7, polyp_count: 0, ephyrae_count: 0 }];
  const temperatures = [{ date: '2026-01-01', average_temperature_c: 0 }];
  const h = createInsights({ activeTab: 'measurements', measurements, temperatures, onOpenHistory() { opened++; } });
  assert.equal(h.chartProps.measurements, measurements);
  assert.equal(h.chartProps.temperatures, temperatures);
  h.chartProps.onOpenHistory();
  assert.equal(opened, 1);
  const chart = read('../src/components/BoxTrackingChart.tsx');
  assert.match(chart, /className="secondary-button compact-button" onClick=\{onOpenHistory\}/);
  assert.match(chart, /\{labels.historyButton\}/);
});

function rule(selector) {
  const start = css.indexOf(`${selector} {`);
  assert.ok(start >= 0, `Missing rule: ${selector}`);
  return css.slice(start, css.indexOf('}', start) + 1);
}
test('320px selector uses equal shrinkable tracks and wrapping inside stretched active buttons', () => {
  assert.match(rule('.box-insights .insight-tabs'), /repeat\(3, minmax\(0, 1fr\)\)/);
  assert.match(rule('.box-insights .insight-tabs'), /align-items: stretch/);
  const buttons = rule('.box-insights .insight-tabs button');
  for (const contract of ['min-width: 0', 'min-height: 44px', 'padding: var(--space-2)', 'white-space: normal', 'overflow-wrap: anywhere']) assert.ok(buttons.includes(contract));
  assert.doesNotMatch(buttons, /(?<!-)\bheight:\s*\d+px|overflow:\s*hidden|text-overflow/);
  assert.match(read('../src/styles/components/tabs.css'), /\.insight-tabs button\.is-active/);
  assert.match(read('../src/styles/base.css'), /button:focus-visible/);
});

test('movement panel shares the default minimum while compact rows wrap without false flow colors', () => {
  assert.match(rule('.insight-panel'), /min-height: 390px/);
  assert.doesNotMatch(css, /\.insight-panel\.insight-panel--movements\s*\{/);
  assert.doesNotMatch(rule('.movement-timeline'), /min-height/);
  assert.doesNotMatch(rule('.movement-empty'), /min-height/);
  assert.match(rule('.movement-locations'), /flex-wrap: wrap/);
  assert.match(rule('.movement-detail'), /overflow-wrap: anywhere/);
  const movementStyles = css.slice(css.indexOf('.movement-timeline {'), css.indexOf('.lineage-inline-status {'));
  assert.doesNotMatch(movementStyles, /color-success|color-danger|is-arrival|is-departure/);
  assert.match(movementStyles, /white-space: pre-line/);
});

test('details action is locally subordinate with a 44px target and no callback replacement', () => {
  const button = rule('.box-insights .chart-window-action .secondary-button');
  assert.match(button, /min-height: 44px/);
  assert.match(button, /min-width: 44px/);
  assert.match(button, /background: transparent/);
  assert.match(button, /border-color: transparent/);
  assert.match(button, /font-weight: 700/);
  assert.match(css, /\.box-insights \.chart-window-action \.secondary-button:is\(:hover, :focus-visible\)/);
});

test('typed operation details wrap within the existing scrollable history styles', () => {
  assert.match(rule('.measurement-history-date .measurement-history-timestamp'), /white-space: normal/);
  assert.match(rule('.measurement-history-operation-details dd'), /overflow-wrap: anywhere/);
  assert.match(rule('.measurement-history-operation-details ul'), /list-style: none/);
  assert.match(css, /\.measurement-history-operation-details \{ flex-basis: 100%; \}/);
  assert.match(rule('.measurement-history-entry--operation'), /var\(--color-surface-subtle\)/);
});

test('modal density preserves scroll ownership, sticky headers, focus and compact scientific columns', () => {
  assert.match(rule('.measurement-history-table'), /min-height: 0; overflow: auto; overscroll-behavior: contain/);
  assert.match(rule('.measurement-history-columns'), /position: sticky/);
  assert.match(rule('.measurement-history-entry'), /padding-block: var\(--space-2\)/);
  assert.match(css, /\.measurement-history-entry \{ grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/);
  assert.match(css, /measurement-history-table\):focus-visible/);
  for (const selector of ['.measurement-history-toolbar select', '.measurement-history-note button', '.measurement-history-footer button']) assert.match(rule(selector), /min-height: 44px/);
});
