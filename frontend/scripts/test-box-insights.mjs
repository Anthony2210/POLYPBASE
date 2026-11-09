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
      })), candidates: props.historyCandidates, assessments: props.historyAssessments, historicalTransitions: props.historyTransitions, loading: props.historyLoading ?? false, failed: props.historyFailed ?? false, retry() {},
    }) },
    './BoxHistoryStatus': loadModule('../src/components/BoxHistoryStatus.tsx', {
      'react/jsx-runtime': jsxRuntime, '../i18n': i18n,
    }),
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

test('unified history keeps persisted week precision, notes and correction gates but hides technical evidence', () => {
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
    assert.equal(h.rows().length, 2);
    const ordinaryRow = h.rows().find(row => h.find(node => node.props.className === 'movement-event', row)
      .some(node => text(node) === (language === 'fr' ? 'Déplacement' : 'Movement')));
    assert.ok(ordinaryRow);
    assert.equal(h.find(node => node.props.className === 'movement-event', ordinaryRow).map(text)[0],
      language === 'fr' ? 'Déplacement' : 'Movement');
    assert.doesNotMatch(text(ordinaryRow), /Déplacement ordinaire|Ordinary movement/);
    const note = h.find(node => node.type === 'em')[0];
    assert.equal(text(note), `${catalogs[language].historyAcceptedReconstruction} ${language === 'fr'
      ? '29 décembre 2025 au 4 janvier 2026' : '29 December 2025 to 4 January 2026'}.`);
    assert.ok(h.html.includes('Reviewed source'));
    assert.equal(h.find(node => node.type === 'time').length, 1);
    assert.match(h.html, language === 'fr' ? /Semaine 1 de 2026/ : /Week 1 of 2026/);
    assert.equal(h.html.includes(catalogs[language].historyUnresolved), false);
    const buttons = h.find(node => node.props.className?.includes('movement-correct-button'));
    assert.equal(buttons.length, 1);
    buttons[0].props.onClick();
    assert.equal(corrected, 2);
    const denied = createInsights({ historyEvents: [{ ...ordinary, can_correct: false }], correctableMovementId: 2,
      onCorrectMovement() {} });
    assert.equal(denied.find(node => node.type === 'button' && node.props.className?.includes('movement-correct-button')).length, 0);
  }
});

test('orphan candidates stay hidden while unresolved persisted receipts remain without claiming acceptance', () => {
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
    assert.equal(h.rows().length, 1);
    assert.equal(h.html.includes(catalogs[language].historyCandidate), false);
    for (const key of ['historyBlocked', 'historyReasonSource', 'historyReasonRecords', 'historyReasonUnknown', 'historyEvidenceUnresolved', 'historyEvidence'])
      assert.equal(h.html.includes(catalogs[language][key]), false, key);
    assert.ok(h.html.includes(catalogs[language].historyReconstructionUnresolved));
    assert.ok(h.find(node => node.type === 'summary').every(node => text(node) === catalogs[language].historyProvenance));
    assert.equal(h.html.includes('source_location_provenance_unresolved'), false);
    assert.equal(h.html.includes(catalogs[language].historyAcceptedReconstruction), false);
    assert.equal(h.find(node => node.type === 'time').length, 0);
    assert.equal(h.html.includes('physical_occurrence_proven'), false);
    assert.equal(h.find(node => node.props.className === 'movement-locations').map(text).includes('Origin → Destination'), false);
    assert.ok(h.html.includes('accepted_by'));
    assert.ok(h.html.includes('Reviewer'));
    assert.equal(h.find(node => node.props.role === 'status').length, 0);
    assert.equal(h.find(node => node.props.className?.includes('movement-correct-button')).length, 0);
  }
});

test('standalone candidates and assessments do not add rows, provenance or a partial banner', () => {
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
    assert.equal(h.find(node => node.props.className === 'movement-event').length, 1);
    const titles = h.find(node => node.props.className === 'movement-event').map(text);
    assert.equal(titles.filter(title => title === catalogs[language].historyCandidate).length, 0);
    assert.equal(titles.filter(title => title === catalogs[language].historyAssessment).length, 0);
    assert.equal(titles.includes(catalogs[language].historyReconstruction), false);
    assert.equal(h.find(node => node.type === 'time').length, 1);
    assert.equal(h.html.includes(catalogs[language].historyWeekOnly), false);
    assert.equal(h.html.includes(catalogs[language].historyAcceptedReconstruction), false);
    const proposal = catalogs[language].historyProposedWeek.replace('{week}', catalogs[language].historyIsoWeek
      .replace('{week}', '1').replace('{year}', '2026'));
    assert.equal(h.find(node => node.type === 'small').some(node => text(node) === proposal), false);
    for (const key of ['historyReasonRecordWeek', 'historyReasonRecords', 'historyReasonMissingLocations'])
      assert.equal(h.html.includes(catalogs[language][key]), false, key);
    assert.equal(h.html.includes(catalogs[language].historyUnresolved), false);
    for (const proof of ['proposed_iso_week', 'measurement_week_inconsistent', 'insufficient_evidence'])
      assert.equal(h.html.includes(proof), false, proof);
    assert.equal(h.find(node => node.props.role === 'status').length, 0);
  }
});

test('recorded qualification and lifecycle rows keep exact dates while technical period evidence is hidden', () => {
  for (const language of ['fr', 'en']) {
    const events = ['qualification', 'location_evidence', 'reactivation'].map((kind, index) => ({
      id: `source:${index}`, kind, temporal_precision: index === 1 ? 'unresolved' : 'exact',
      occurred_at: index === 1 ? null : '2026-10-14T08:12:34Z', actor: null,
      provenance: kind === 'location_evidence' ? 'box_location' : 'box_movement',
      evidence: { source_id: index }, reconciliation_status: 'recorded',
      from_thermal_zone: { id: 2, name: 'Origin' }, to_thermal_zone: { id: 3, name: 'Destination' },
      notes: 'Source note', can_correct: false, recorded_movement_id: null,
    }));
    const before = JSON.stringify(events);
    const h = createInsights({ language, labels: labelsFor(language), historyEvents: events });
    assert.equal(h.rows().length, 2);
    assert.equal(h.find(node => node.type === 'details').length, 2);
    assert.equal(h.find(node => node.type === 'time').length, 2);
    assert.ok(h.find(node => node.type === 'time').every(node => node.props.dateTime === events[0].occurred_at));
    for (const key of ['movementKindQualification', 'historyLocationEvidence', 'historyEvidenceUnresolved', 'historyEvidence'])
      assert.equal(h.html.includes(catalogs[language][key]), false, key);
    assert.ok(h.html.includes(catalogs[language].movementKindReactivation));
    assert.equal(h.html.includes('box_location'), false);
    assert.ok(h.html.includes('source_id'));
    assert.ok(h.html.includes('Source note'));
    assert.equal(JSON.stringify(events), before);
  }
});

test('exact and persisted reconstructed movements remain visible without standalone unconfirmed movements', () => {
  for (const language of ['fr', 'en']) {
    const exact = { id: 'move:1', kind: 'ordinary', temporal_precision: 'exact', occurred_at: '2026-09-17T08:12:34Z',
      iso_year: null, iso_week: null, from_thermal_zone: { id: 2, name: 'Origin' }, to_thermal_zone: { id: 3, name: 'Destination' },
      actor: { first_name: 'Original', last_name: 'Author' }, provenance: 'box_movement', reconciliation_status: 'recorded',
      evidence: {}, recorded_movement_id: 1, can_correct: false, notes: '' };
    const historical = { ...exact, id: 'historical:2', kind: 'historical_reconstruction', temporal_precision: 'iso_week',
      occurred_at: null, iso_year: 2026, iso_week: 38, actor: null, reconciliation_status: 'accepted' };
    const unresolved = { ...historical, id: 'historical:3', reconciliation_status: 'unresolved',
      temporal_precision: 'unresolved', iso_year: null, iso_week: null, evidence: { stored_iso_year: 2026, stored_iso_week: 42 } };
    const candidate = { id: 'pending', box_id: 7, iso_year: 2026, iso_week: 42,
      from_thermal_zone_id: 2, to_thermal_zone_id: 3, reconciliation_status: 'blocked', blockers: ['whole_week_period_evidence_required'],
      evidence: { origin_location_id: 10, destination_location_id: 11, represented_movement_ids: [] } };
    const input = { historyEvents: [exact, historical, unresolved], historyCandidates: [candidate] };
    const before = JSON.stringify(input);
    const h = createInsights({ ...input, language, labels: labelsFor(language),
      locations: [{ thermal_zone: exact.from_thermal_zone }, { thermal_zone: exact.to_thermal_zone }] });
    assert.equal(h.rows().length, 3);
    assert.deepEqual(h.find(node => node.props.className === 'movement-event').map(text),
      Array(3).fill(catalogs[language].movementKindOrdinary));
    assert.equal(h.find(node => node.type === 'time').length, 1);
    assert.equal(h.find(node => node.type === 'time')[0].props.dateTime, exact.occurred_at);
    assert.equal(h.find(node => node.type === 'details').length, 3);
    assert.equal(h.find(node => node.type === 'em').filter(node => text(node) === catalogs[language].historyReconstructionUnresolved).length, 1);
    assert.equal(h.find(node => node.type === 'em').filter(node => text(node) === catalogs[language].historyPotentialUnordered).length, 0);
    assert.ok(h.find(node => node.type === 'em').some(node => text(node).includes(language === 'fr' ? '14 au 20 septembre 2026' : '14 to 20 September 2026')));
    assert.equal(h.find(node => node.type === 'span').some(node => /Semaine 42|Week 42/.test(text(node))), false);
    assert.equal(JSON.stringify(input), before);
  }
});

test('potential movements remain hidden even when an origin record and both zones are known', () => {
  const zones = { from: { id: 2, name: 'Zone 15.0°C' }, to: { id: 3, name: 'Zone 10.0°C' } };
  const locations = [{ id: 10, starts_at: '2026-01-05T00:00:00+01:00', thermal_zone: zones.from },
    { id: 11, starts_at: '2026-02-16T00:00:00+01:00', thermal_zone: zones.to }];
  const evidence = { origin_location_id: 10, destination_location_id: 11, represented_movement_ids: [],
    attributed_week_start: '2026-02-09', measurement_weeks: ['2026-02-09', '2026-02-16'], measurement_ids: [0, 1] };
  const candidate = { id: 'c', box_id: 7, iso_year: 2026, iso_week: 7, from_thermal_zone_id: 2, to_thermal_zone_id: 3,
    reconciliation_status: 'review_required', classification: 'eligible', blockers: [], evidence };
  const before = JSON.stringify([candidate, locations]);
  for (const language of ['fr', 'en']) {
    const h = createInsights({ language, labels: labelsFor(language), historyCandidates: [candidate], locations });
    assert.equal(h.rows().length, 0);
    assert.equal(h.find(node => node.type === 'em').length, 0);
    assert.equal(h.find(node => node.type === 'time').length, 0);
    assert.equal(h.find(node => node.type === 'details').length, 0);
    assert.equal(h.find(node => node.props.role === 'status').length, 0);
    assert.equal(h.html.includes(catalogs[language].historyPotentialMovement), false);
    assert.equal(JSON.stringify([candidate, locations]), before);
  }
});

const observation = (year, week, value, start) => ({ reading_id: week, biological_measurement_id: week, iso_year: year, iso_week: week,
  week_start: start, environment_temperature_c: value, association_status: 'confirmed',
  sources: [{ source_file: 'Suivi.xlsx', temperature_cell: `C${week}`, source_box: 'BOX-1.01' }] });
const sourceTransition = (status, extra = {}) => ({ id: `historical-transition:${status}`, box_id: 1, status, from_environment_c: '15', to_environment_c: '10',
  from_thermal_zone: { id: 2, name: 'Zone 15.0°C' }, to_thermal_zone: { id: 3, name: 'Zone 10.0°C' },
  origin_observation: observation(2026, 7, '15', '2026-02-09'), destination_observation: observation(2026, 8, '10', '2026-02-16'),
  representation: null, blockers: [], planner_candidate_ids: [], adds_location_history: true, ...extra });

test('established source transitions read as historical movements between observation weeks', () => {
  const expected = {
    fr: 'Réalisé entre les relevés des semaines 7 et 8 de 2026 (d’après les données historiques).',
    en: 'Carried out between the records from weeks 7 and 8 of 2026 (based on historical data).',
  };
  for (const language of ['fr', 'en']) {
    const h = createInsights({ language, labels: labelsFor(language), historyEvents: [], historyTransitions: [sourceTransition('established')], locations: [] });
    assert.deepEqual(h.find(node => node.props.className === 'movement-event').map(text), [catalogs[language].historyHistoricalMovement]);
    assert.deepEqual(h.find(node => node.props.className === 'movement-locations').map(text), ['Zone 15.0°C → Zone 10.0°C']);
    assert.deepEqual(h.find(node => node.type === 'em').map(text), [expected[language]]);
    assert.equal(h.html.includes(catalogs[language].historyUnresolved), false);
    assert.equal(h.html.includes(catalogs[language].historyPotentialMovement), false);
    assert.equal(h.html.includes(catalogs[language].historyActorUnknown), false);
    assert.equal(h.find(node => node.type === 'time').length, 0);
    assert.deepEqual(h.find(node => node.type === 'summary').map(text), [catalogs[language].historyProvenance]);
    assert.equal(h.find(node => node.type === 'pre').length, 1);
    assert.equal(h.find(node => node.type === 'details').length, 1);
    assert.ok(h.html.includes('temperature_cell'));
  }
});

test('source transition interval names both ISO years at a boundary', () => {
  const crossing = sourceTransition('established', { origin_observation: observation(2025, 52, '15', '2025-12-22'),
    destination_observation: observation(2026, 1, '10', '2025-12-29') });
  const h = createInsights({ language: 'fr', labels: labelsFor('fr'), historyEvents: [], historyTransitions: [crossing], locations: [] });
  assert.deepEqual(h.find(node => node.type === 'em').map(text),
    ['Réalisé entre le relevé de la semaine 52 de 2025 et celui de la semaine 1 de 2026 (d’après les données historiques).']);
});

test('unresolved, missing-zone, blocked and orphan represented transitions produce no row or banner', () => {
  const transitions = [
    sourceTransition('environment_only', { from_thermal_zone: null, to_thermal_zone: null, adds_location_history: false, blockers: ['location_mapping_unavailable'] }),
    sourceTransition('review', { adds_location_history: false, blockers: ['conflicting_recorded_movement'] }),
    sourceTransition('established', { from_thermal_zone: null }),
    sourceTransition('established', { to_thermal_zone: null }),
    sourceTransition('established', { blockers: ['conflicting_recorded_movement'] }),
    sourceTransition('already_represented', { representation: { kind: 'box_movement', event_id: 'missing' } }),
  ];
  const before = JSON.stringify(transitions);
  for (const language of ['fr', 'en']) {
    for (const transition of transitions) {
      const h = createInsights({ language, labels: labelsFor(language), historyEvents: [], historyTransitions: [transition] });
      assert.equal(h.rows().length, 0, JSON.stringify(transition));
      assert.equal(h.find(node => node.type === 'details').length, 0);
      assert.equal(h.find(node => node.props.role === 'status').length, 0);
      assert.equal(h.html.includes(catalogs[language].historyHistoricalMovement), false);
    }
  }
  assert.equal(JSON.stringify(transitions), before);
});

test('a recorded movement stays exact and only gains the source evidence in provenance', () => {
  const recorded = { id: 'movement:9', kind: 'ordinary', temporal_precision: 'exact', occurred_at: '2026-02-17T08:12:34Z', iso_year: 2026, iso_week: 8,
    from_thermal_zone: { id: 2, name: 'Zone 15.0°C' }, to_thermal_zone: { id: 3, name: 'Zone 10.0°C' }, actor: { first_name: 'Original', last_name: 'Author' },
    provenance: 'box_movement', reconciliation_status: 'recorded', evidence: { revision: 1 }, recorded_movement_id: 9, can_correct: true, notes: 'kept' };
  const transition = sourceTransition('already_represented', { adds_location_history: false, representation: { kind: 'box_movement', event_id: 'movement:9' } });
  const transitions = [transition, sourceTransition('review'),
    sourceTransition('established', { from_thermal_zone: null }),
    sourceTransition('established', { blockers: ['conflicting_recorded_movement'] })];
  const before = JSON.stringify([recorded, transitions]);
  for (const language of ['fr', 'en']) {
    let corrected;
    const h = createInsights({ language, labels: labelsFor(language), historyEvents: [recorded], historyTransitions: transitions,
      locations: [], correctableMovementId: 9, correctMovementLabel: 'Correct', onCorrectMovement: id => { corrected = id; } });
    assert.equal(h.rows().length, 1);
    assert.equal(h.find(node => node.type === 'time')[0].props.dateTime, recorded.occurred_at);
    assert.deepEqual(h.find(node => node.props.className === 'movement-event').map(text), [catalogs[language].movementKindOrdinary]);
    assert.ok(h.html.includes('Original'));
    assert.ok(h.html.includes('kept'));
    assert.ok(h.html.includes('historical_source_transitions'));
    assert.equal(h.html.includes(catalogs[language].historyHistoricalMovement), false);
    assert.equal(h.find(node => node.props.role === 'status').length, 0);
    const buttons = h.find(node => node.props.className?.includes('movement-correct-button'));
    assert.equal(buttons.length, 1);
    buttons[0].props.onClick();
    assert.equal(corrected, 9);
    assert.equal(JSON.stringify([recorded, transitions]), before);
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
