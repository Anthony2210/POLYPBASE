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
    AbortController,
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
function createInsights(overrides = {}, apiPayload) {
  const slots = [];
  const pendingEffects = [];
  const requests = [];
  const context = new AbortController().signal;
  let cursor = 0;
  let height = 480.2;
  let tree;
  let chartProps;
  const selections = [];
  const hooks = {
    ...React,
    lazy: () => () => React.createElement('div', { className: 'lineage-graph-stub' }),
    useMemo(factory, deps) {
      if (apiPayload === undefined) return factory();
      const index = cursor++;
      if (!slots[index] || deps.some((dep, i) => dep !== slots[index].deps[i])) slots[index] = { deps, value: factory() };
      return slots[index].value;
    },
    useEffect(effect, deps) {
      const index = cursor++;
      if (!slots[index] || deps.some((dep, i) => dep !== slots[index].deps[i])) {
        const previous = slots[index];
        slots[index] = { deps };
        pendingEffects.push(() => { previous?.cleanup?.(); slots[index].cleanup = effect(); });
      }
    },
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
    '../hooks/useBoxHistory': apiPayload === undefined ? { useBoxHistory: () => ({
      events: props.historyEvents ?? [...props.movements].sort((a, b) => b.moved_at.localeCompare(a.moved_at)).map(row => ({
        ...row, id: `movement:${row.id}`, recorded_movement_id: row.id, kind: row.kind ?? 'ordinary',
        occurred_at: row.moved_at, temporal_precision: 'exact', actor: row.user_identity,
        provenance: 'box_movement', evidence: {}, reconciliation_status: 'recorded', can_correct: false,
      })), candidates: props.historyCandidates, assessments: props.historyAssessments, historicalTransitions: props.historyTransitions, startingLocation: props.historyStartingLocation, loading: props.historyLoading ?? false, failed: props.historyFailed ?? false, retry() {},
    }) } : loadModule('../src/hooks/useBoxHistory.ts', {
      react: hooks,
      '../utils/boxHistory': loadModule('../src/utils/boxHistory.ts'),
      '../api/client': {
        getOrganizationResourceSignal: () => context,
        apiGetResource: async (url, options) => {
          requests.push({ url, options });
          return JSON.stringify(apiPayload);
        },
      },
    }),
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
  function render() {
    cursor = 0;
    tree = resolve(BoxInsights(props));
    pendingEffects.splice(0).forEach(effect => effect());
  }
  function find(predicate, node) {
    if (arguments.length === 1) node = tree;
    if (Array.isArray(node)) return node.flatMap(child => find(predicate, child));
    if (!React.isValidElement(node)) return [];
    return [...(predicate(node) ? [node] : []), ...find(predicate, node.props.children)];
  }
  render();
  return {
    find, selections, requests,
    async settleHistory() { await new Promise(resolve => setTimeout(resolve, 0)); render(); },
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
    assert.equal(h.find(node => node.type === 'details').length, 0);
    assert.equal(h.html.includes(catalogs[language].historyProvenance), false);
    assert.equal(h.html.includes('source_location_provenance_unresolved'), false);
    assert.equal(h.html.includes(catalogs[language].historyAcceptedReconstruction), false);
    assert.equal(h.find(node => node.type === 'time').length, 0);
    assert.equal(h.html.includes('physical_occurrence_proven'), false);
    assert.equal(h.find(node => node.props.className === 'movement-locations').map(text).includes('Origin → Destination'), false);
    assert.equal(h.html.includes('accepted_by'), false);
    assert.equal(h.html.includes('Reviewer'), false);
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

test('starting location shows only the first observation date, title and established zone', () => {
  const qualification = { id: 'lifecycle:1', kind: 'qualification', provenance: 'lifecycle_audit',
    temporal_precision: 'unresolved', occurred_at: null, evidence: {}, reconciliation_status: 'recorded',
    from_thermal_zone: null, to_thermal_zone: null, actor: { first_name: 'Anthony', last_name: 'Audit' } };
  const first = { id: 1, measured_on: '2024-04-01', polyp_count: 0 };
  const period = { id: 2, thermal_zone: { id: 10, name: 'Zone 10.0°C' },
    starts_at: '2024-01-01T00:00:00+01:00', ends_at: null, end_date_unknown: false };
  for (const language of ['fr', 'en']) {
    const h = createInsights({ language, labels: labelsFor(language), historyEvents: [qualification],
      measurements: [first], locations: [period],
      historyStartingLocation: { observed_on: first.measured_on, thermal_zone: period.thermal_zone } });
    assert.equal(h.rows().length, 1);
    assert.deepEqual(h.find(node => node.type === 'time').map(node => node.props.dateTime), ['2024-04-01']);
    assert.ok(h.html.includes('1 avr. 2024'));
    assert.deepEqual(h.find(node => node.props.className === 'movement-event').map(text),
      [language === 'fr' ? 'Emplacement de départ' : 'Starting location']);
    assert.deepEqual(h.find(node => node.props.className === 'movement-locations').map(text), ['Zone 10.0°C']);
    assert.equal(h.find(node => node.type === 'small').length, 0);
    for (const value of ['Anthony', catalogs[language].historyUnresolved, catalogs[language].historyNoDestination])
      assert.equal(h.html.includes(value), false);
    assert.equal(createInsights({ language, historyEvents: [qualification], measurements: [first], locations: [period] }).rows().length, 0,
      'the UI never guesses from the current zone or qualification');
    const existing = { ...qualification, id: 'movement:initial', kind: 'initial_location', provenance: 'box_movement',
      temporal_precision: 'exact', occurred_at: '2024-04-02T12:00:00Z', to_thermal_zone: period.thermal_zone };
    const laterAssignment = createInsights({ language, historyEvents: [qualification, existing],
      historyStartingLocation: { observed_on: first.measured_on, thermal_zone: period.thermal_zone } });
    assert.equal(laterAssignment.rows().length, 2, 'a later assignment is not the first observation');
    assert.equal(text(laterAssignment.rows().at(-1)), `1 avr. 2024${catalogs[language].historyStartingLocation}Zone 10.0°C`);
    const linked = { ...existing, recorded_movement_id: 7, can_correct: true, notes: 'Original assignment note' };
    let corrected;
    const represented = createInsights({ language, labels: labelsFor(language), historyEvents: [qualification, linked],
      historyStartingLocation: { observed_on: first.measured_on, thermal_zone: period.thermal_zone, represented_movement_id: 7 },
      correctableMovementId: 7, correctMovementLabel: 'Edit', onCorrectMovement: id => { corrected = id; } });
    assert.equal(represented.rows().length, 1, 'only an explicit server relationship collapses an existing assignment');
    assert.equal(represented.find(node => node.type === 'time')[0].props.dateTime, first.measured_on);
    assert.equal(represented.find(node => node.type === 'small').length, 0);
    assert.ok(represented.html.includes(linked.notes));
    assert.equal(represented.html.includes('Anthony'), false);
    represented.find(node => node.props.className?.includes('movement-correct-button'))[0].props.onClick();
    assert.equal(corrected, 7);
  }
});

test('historical origin remains last with a subsequent unpaired movement and keeps its reliable precision', () => {
  const recorded = { id: 'movement:5', recorded_movement_id: 5, kind: 'unclassified', provenance: 'box_movement',
    temporal_precision: 'exact', occurred_at: '2026-10-05T15:36:00+02:00', reconciliation_status: 'recorded',
    evidence: { recorded_via_polypbase: true, period_pairing_reliable: false }, can_correct: true,
    from_thermal_zone: { id: 10, name: 'Zone 10.0°C' }, to_thermal_zone: { id: 15, name: 'Zone 15.0°C' },
    actor: { first_name: 'Jules', last_name: 'Poncet' }, notes: 'Original movement note' };
  for (const language of ['fr', 'en']) {
    for (const initial of [
      { observed_on: '2026-02-11', thermal_zone: recorded.from_thermal_zone },
      { observed_on: null, temporal_precision: 'iso_week', iso_year: 2026, iso_week: 7,
        thermal_zone: recorded.from_thermal_zone, represented_movement_id: null },
    ]) {
      const before = JSON.stringify({ recorded, initial });
      const h = createInsights({ language, labels: labelsFor(language), historyEvents: [recorded], locations: [],
        historyStartingLocation: initial, correctableMovementId: 5, correctMovementLabel: 'Edit', onCorrectMovement() {} });
      assert.equal(h.rows().length, 2);
      assert.equal(h.find(node => node.type === 'time', h.rows()[0])[0].props.dateTime, recorded.occurred_at);
      const anchor = h.rows().at(-1);
      assert.equal(h.find(node => node.props.className === 'movement-event', anchor).map(text)[0], catalogs[language].historyStartingLocation);
      assert.equal(h.find(node => node.props.className === 'movement-locations', anchor).map(text)[0], 'Zone 10.0°C');
      assert.equal(h.find(node => node.type === 'small', anchor).length, 0);
      assert.equal(h.find(node => node.type === 'p', anchor).length, 0);
      assert.equal(h.find(node => node.type === 'details').length, 0);
      if (initial.observed_on !== null) {
        assert.equal(h.find(node => node.type === 'time', anchor)[0].props.dateTime, initial.observed_on);
      } else {
        assert.equal(h.find(node => node.type === 'time', anchor).length, 0);
        assert.equal(h.find(node => node.props.className === 'movement-interval', anchor).map(text)[0],
          language === 'fr' ? 'Semaine 7 de 2026' : 'Week 7 of 2026');
        assert.equal(text(anchor).includes('févr.'), false, 'a source week does not certify a calendar day');
      }
      assert.equal(h.html.includes(catalogs[language].historyUnresolved), false);
      assert.equal(h.find(node => node.props.className?.includes('movement-correct-button')).length, 1);
      assert.ok(text(h.rows()[0]).includes(recorded.notes));
      assert.equal(JSON.stringify({ recorded, initial }), before);
    }
  }
});

test('history API starting_location reaches the real hook and renders last after recorded and reconstructed rows', async () => {
  const recorded = { id: 'movement:51', recorded_movement_id: 51, kind: 'unclassified', provenance: 'box_movement',
    temporal_precision: 'exact', occurred_at: '2026-10-05T15:36:00+02:00', reconciliation_status: 'recorded',
    evidence: { recorded_via_polypbase: true, period_pairing_reliable: false }, can_correct: true,
    from_thermal_zone: { id: 3, name: 'Zone 10.0°C' }, to_thermal_zone: { id: 4, name: 'Zone 20.0°C' },
    actor: { first_name: 'Recorded', last_name: 'Author' }, notes: '' };
  for (const language of ['fr', 'en']) {
    for (const initial of [
      { observed_on: '2026-01-14', thermal_zone: { id: 2, name: 'Zone 15.0°C' }, represented_movement_id: null },
      { observed_on: null, temporal_precision: 'iso_week', iso_year: 2026, iso_week: 3,
        thermal_zone: { id: 2, name: 'Zone 15.0°C' }, represented_movement_id: null },
    ]) {
      const payload = { events: [recorded], candidates: [], assessments: [],
        historical_transitions: [sourceTransition('established')], starting_location: initial };
      const before = JSON.stringify(payload);
      const h = createInsights({ boxId: 42, language, labels: labelsFor(language),
        correctableMovementId: 51, correctMovementLabel: 'Edit', onCorrectMovement() {} }, payload);
      assert.equal(h.rows().length, 0, 'the anchor is not guessed while the request is pending');
      await h.settleHistory();
      assert.equal(h.requests.length, 1);
      assert.equal(h.requests[0].url, '/api/boxes/42/history/');
      assert.equal(h.rows().length, 3);
      assert.equal(h.find(node => node.type === 'time', h.rows()[0])[0].props.dateTime, recorded.occurred_at);
      assert.equal(h.find(node => node.props.className === 'movement-event', h.rows()[1]).map(text)[0], catalogs[language].historyHistoricalMovement);
      const anchor = h.rows().at(-1);
      assert.equal(h.find(node => node.props.className === 'movement-event', anchor).map(text)[0], catalogs[language].historyStartingLocation);
      assert.equal(h.find(node => node.props.className === 'movement-locations', anchor).map(text)[0], initial.thermal_zone.name);
      if (initial.observed_on === null) {
        assert.equal(h.find(node => node.type === 'time', anchor).length, 0);
        assert.equal(h.find(node => node.props.className === 'movement-interval', anchor).map(text)[0],
          language === 'fr' ? 'Semaine 3 de 2026' : 'Week 3 of 2026');
      } else assert.equal(h.find(node => node.type === 'time', anchor)[0].props.dateTime, initial.observed_on);
      assert.equal(h.find(node => ['small', 'p', 'details'].includes(node.type), anchor).length, 0);
      assert.equal(h.html.includes('Premier emplacement connu'), false);
      assert.equal(h.html.includes('recorded_via_polypbase'), false);
      assert.equal(h.find(node => node.props.className?.includes('movement-correct-button')).length, 1);
      assert.equal(JSON.stringify(payload), before);
    }
  }
});

test('history API explicit representation collapses one assignment, not another operational event', async () => {
  const initial = { observed_on: '2026-01-14', thermal_zone: { id: 2, name: 'Zone 15.0°C' }, represented_movement_id: 7 };
  const assignment = { id: 'movement:7', recorded_movement_id: 7, kind: 'initial_location', provenance: 'box_movement',
    temporal_precision: 'exact', occurred_at: '2026-01-01T08:00:00Z', reconciliation_status: 'recorded',
    from_thermal_zone: null, to_thermal_zone: initial.thermal_zone, actor: { first_name: 'Original', last_name: 'Author' },
    evidence: { recorded_via_polypbase: true }, notes: '', can_correct: true };
  const later = { ...assignment, id: 'movement:8', recorded_movement_id: 8, occurred_at: '2026-01-20T08:00:00Z' };
  const h = createInsights({ boxId: 42, correctableMovementId: 7, correctMovementLabel: 'Edit', onCorrectMovement() {} },
    { events: [later, assignment], starting_location: initial });
  await h.settleHistory();
  assert.equal(h.rows().length, 2);
  assert.equal(h.find(node => node.type === 'time', h.rows()[0])[0].props.dateTime, later.occurred_at);
  assert.equal(h.find(node => node.props.className === 'movement-event').map(text).filter(value => value === 'Emplacement de départ').length, 1);
  assert.equal(h.find(node => node.type === 'time', h.rows().at(-1))[0].props.dateTime, initial.observed_on);
  assert.equal(h.find(node => node.type === 'small', h.rows().at(-1)).length, 0);
  assert.equal(h.find(node => node.props.className?.includes('movement-correct-button')).length, 1);
});

test('history API null or absent starting_location never falls back to a later destination or period', async () => {
  const assignment = { id: 'movement:8', recorded_movement_id: 8, kind: 'initial_location', provenance: 'box_movement',
    temporal_precision: 'exact', occurred_at: '2026-10-05T16:03:00+02:00', reconciliation_status: 'recorded',
    from_thermal_zone: null, to_thermal_zone: { id: 4, name: 'Later zone' }, actor: null,
    evidence: { recorded_via_polypbase: true }, notes: '', can_correct: false };
  for (const initial of [{ starting_location: null }, {}]) {
    const h = createInsights({ boxId: 42, movements: [movement()], measurements: [{ id: 1, measured_on: '2024-04-01' }],
      locations: [{ id: 1, thermal_zone: assignment.to_thermal_zone, starts_at: '2024-01-01T00:00:00Z', ends_at: null }] },
      { events: [assignment], ...initial });
    await h.settleHistory();
    assert.equal(h.rows().length, 1, 'the genuine assignment remains visible');
    assert.equal(h.find(node => node.type === 'time')[0].props.dateTime, assignment.occurred_at);
    assert.equal(h.html.includes(catalogs.fr.historyStartingLocation), false);
    assert.equal(h.html.includes('2024'), false);
    assert.ok(h.html.includes('Later zone'));
  }
});

test('only audited operational movements receive the POLYPBASE label without changing correction data', () => {
  const recorded = { id: 'movement:5', recorded_movement_id: 5, kind: 'unclassified', provenance: 'box_movement',
    temporal_precision: 'exact', occurred_at: '2026-10-05T15:36:00+02:00', reconciliation_status: 'recorded',
    evidence: { recorded_via_polypbase: true, period_pairing_reliable: false }, can_correct: true,
    from_thermal_zone: { id: 10, name: 'Zone 10.0°C' }, to_thermal_zone: { id: 15, name: 'Zone 15.0°C' },
    actor: { first_name: 'Jules', last_name: 'Poncet' }, notes: 'Original note' };
  const before = JSON.stringify(recorded);
  for (const language of ['fr', 'en']) {
    const h = createInsights({ language, labels: labelsFor(language), historyEvents: [recorded],
      correctableMovementId: 5, onCorrectMovement() {}, correctMovementLabel: 'Edit' });
    assert.deepEqual(h.find(node => node.props.className === 'movement-event').map(text),
      [language === 'fr' ? 'Réalisé via POLYPBASE' : 'Carried out via POLYPBASE']);
    assert.equal(h.find(node => node.type === 'time')[0].props.dateTime, recorded.occurred_at);
    assert.ok(h.html.includes('Jules'));
    assert.ok(h.html.includes('Original note'));
    assert.equal(h.find(node => node.props.className?.includes('movement-correct-button')).length, 1);
    const unknown = createInsights({ language, labels: labelsFor(language), historyEvents: [{ ...recorded, evidence: {} }] });
    assert.ok(unknown.html.includes(catalogs[language].movementKindUnclassified));
  }
  assert.equal(JSON.stringify(recorded), before);
});

test('historical interval and exact date share typography and can wrap in the temporal column', () => {
  assert.match(css, /\.movement-timeline time,\s*\.movement-timeline \.movement-interval\s*\{[^}]*font-size: \.76rem;[^}]*line-height: 1\.5;[^}]*white-space: normal;[^}]*overflow-wrap: anywhere;/);
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
    assert.equal(h.find(node => node.type === 'details').length, 0);
    assert.equal(h.find(node => node.type === 'time').length, 2);
    assert.ok(h.find(node => node.type === 'time').every(node => node.props.dateTime === events[0].occurred_at));
    for (const key of ['movementKindQualification', 'historyLocationEvidence', 'historyEvidenceUnresolved', 'historyEvidence'])
      assert.equal(h.html.includes(catalogs[language][key]), false, key);
    assert.ok(h.html.includes(catalogs[language].historyReactivation));
    assert.ok(h.html.includes(catalogs[language].historyLocationEntry.replace('{zone}', 'Destination')));
    assert.equal(h.html.includes('box_location'), false);
    assert.equal(h.html.includes('source_id'), false);
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
    assert.equal(h.find(node => node.type === 'details').length, 0);
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

const intervalLabel = {
  fr: 'Réalisé entre les relevés des semaines 7 et 8 de 2026.',
  en: 'Carried out between the records from weeks 7 and 8 of 2026.',
};

test('a historical temperature change reads as a reconstructed movement dated by its observation weeks', () => {
  for (const language of ['fr', 'en']) {
    const h = createInsights({ language, labels: labelsFor(language), historyEvents: [], historyTransitions: [sourceTransition('established')], locations: [] });
    assert.equal(h.rows().length, 1);
    assert.deepEqual(h.find(node => node.props.className === 'movement-event').map(text), [catalogs[language].historyHistoricalMovement]);
    assert.equal(catalogs[language].historyHistoricalMovement, language === 'fr' ? 'Déplacement historique reconstruit' : 'Reconstructed historical movement');
    assert.deepEqual(h.find(node => node.props.className === 'movement-locations').map(text), ['Zone 15.0°C → Zone 10.0°C']);
    // The observation interval is the row's temporal label, in place of "date not established".
    assert.deepEqual(h.find(node => node.props.className === 'movement-interval').map(text), [intervalLabel[language]]);
    assert.equal(h.find(node => node.type === 'em').length, 0);
    for (const key of ['historyUnresolved', 'historyPotentialMovement', 'historyActorUnknown', 'historyProvenance', 'historyReconciliation',
      'historyTransitionReview', 'historyEnvironmentOnly', 'historyCandidate'])
      assert.equal(h.html.includes(catalogs[language][key]), false, key);
    assert.equal(h.find(node => node.type === 'time').length, 0);
    assert.equal(h.find(node => node.type === 'details').length, 0);
    assert.equal(h.find(node => node.type === 'pre').length, 0);
    for (const proof of ['temperature_cell', 'Suivi.xlsx', 'BOX-1.01', 'established', 'historical_tracking'])
      assert.equal(h.html.includes(proof), false, proof);
  }
});

test('source transition interval names both ISO years at a boundary', () => {
  const crossing = sourceTransition('established', { origin_observation: observation(2025, 52, '15', '2025-12-22'),
    destination_observation: observation(2026, 1, '10', '2025-12-29') });
  const h = createInsights({ language: 'fr', labels: labelsFor('fr'), historyEvents: [], historyTransitions: [crossing], locations: [] });
  assert.deepEqual(h.find(node => node.props.className === 'movement-interval').map(text),
    ['Réalisé entre le relevé de la semaine 52 de 2025 et celui de la semaine 1 de 2026.']);
});

test('every displayed temperature change becomes a reconstructed movement without technical wording', () => {
  const transitions = [
    sourceTransition('environment_only', { id: 'a', from_thermal_zone: null, to_thermal_zone: null, adds_location_history: false, blockers: ['location_mapping_unavailable'] }),
    sourceTransition('review', { id: 'b', adds_location_history: false, blockers: ['source_not_validated'] }),
    sourceTransition('review', { id: 'c', adds_location_history: false, blockers: ['location_period_conflict'] }),
  ];
  const before = JSON.stringify(transitions);
  for (const language of ['fr', 'en']) {
    const h = createInsights({ language, labels: labelsFor(language), historyEvents: [], historyTransitions: transitions });
    assert.equal(h.rows().length, 3);
    assert.deepEqual(h.find(node => node.props.className === 'movement-event').map(text), Array(3).fill(catalogs[language].historyHistoricalMovement));
    // Without a single matching location, the temperature categories identify the route.
    assert.ok(h.find(node => node.props.className === 'movement-locations').map(text).includes('15 °C → 10 °C'));
    assert.deepEqual(h.find(node => node.props.className === 'movement-interval').map(text), Array(3).fill(intervalLabel[language]));
    for (const key of ['historyUnresolved', 'historyTransitionReview', 'historyEnvironmentOnly', 'historyCandidate', 'historyProvenance'])
      assert.equal(h.html.includes(catalogs[language][key]), false, key);
    for (const proof of ['location_mapping_unavailable', 'source_not_validated', 'location_period_conflict'])
      assert.equal(h.html.includes(proof), false, proof);
    assert.equal(h.find(node => node.props.role === 'status').length, 0);
  }
  assert.equal(JSON.stringify(transitions), before);
});

test('changes already recorded, contradicted by recorded movements or outside the plotted series add no row', () => {
  const transitions = [
    sourceTransition('review', { adds_location_history: false, blockers: ['conflicting_recorded_movement'] }),
    sourceTransition('review', { adds_location_history: false, blockers: ['multiple_recorded_candidates'] }),
    sourceTransition('review', { adds_location_history: false, blockers: ['observation_measurement_not_canonical'] }),
    sourceTransition('review', { adds_location_history: false, blockers: ['observation_association_not_confirmed'],
      destination_observation: { ...observation(2026, 8, '10', '2026-02-16'), association_status: 'pending_review' } }),
    sourceTransition('already_represented', { representation: { kind: 'box_movement', event_id: 'missing' } }),
  ];
  for (const language of ['fr', 'en']) {
    for (const transition of transitions) {
      const h = createInsights({ language, labels: labelsFor(language), historyEvents: [], historyTransitions: [transition] });
      assert.equal(h.rows().length, 0, JSON.stringify(transition));
      assert.equal(h.find(node => node.props.role === 'status').length, 0);
    }
  }
});

test('a recorded movement stays exact and keeps its correction without showing source evidence', () => {
  const recorded = { id: 'movement:9', kind: 'ordinary', temporal_precision: 'exact', occurred_at: '2026-02-17T08:12:34Z', iso_year: 2026, iso_week: 8,
    from_thermal_zone: { id: 2, name: 'Zone 15.0°C' }, to_thermal_zone: { id: 3, name: 'Zone 10.0°C' }, actor: { first_name: 'Original', last_name: 'Author' },
    provenance: 'box_movement', reconciliation_status: 'recorded', evidence: { revision: 1 }, recorded_movement_id: 9, can_correct: true, notes: 'kept' };
  const transition = sourceTransition('already_represented', { adds_location_history: false, representation: { kind: 'box_movement', event_id: 'movement:9' } });
  const transitions = [transition, sourceTransition('review', { blockers: ['conflicting_recorded_movement'] })];
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
    assert.equal(h.html.includes('historical_source_transitions'), false);
    assert.equal(h.find(node => node.type === 'details').length, 0);
    assert.equal(h.html.includes(catalogs[language].historyHistoricalMovement), false);
    assert.equal(h.find(node => node.props.role === 'status').length, 0);
    const buttons = h.find(node => node.props.className?.includes('movement-correct-button'));
    assert.equal(buttons.length, 1);
    buttons[0].props.onClick();
    assert.equal(corrected, 9);
    assert.equal(JSON.stringify([recorded, transitions]), before);
  }
});

test('starting observation remains last after exact and reconstructed movements', () => {
  const exact = { id: 'movement:90', kind: 'unclassified', provenance: 'box_movement', recorded_movement_id: 90,
    occurred_at: '2026-10-05T15:36:00+02:00', temporal_precision: 'exact', reconciliation_status: 'recorded',
    from_thermal_zone: { id: 10, name: 'Zone 10.0°C' }, to_thermal_zone: { id: 15, name: 'Zone 15.0°C' }, evidence: {}, notes: '' };
  const transitions = [sourceTransition('established'), sourceTransition('established', { id: 'later',
    origin_observation: observation(2026, 9, '10', '2026-02-23'), destination_observation: observation(2026, 10, '15', '2026-03-02') })];
  const h = createInsights({ historyEvents: [exact], historyTransitions: transitions,
    historyStartingLocation: { observed_on: '2026-02-02', thermal_zone: { id: 20, name: 'Zone 20.0°C' } } });
  assert.equal(h.rows().length, 4);
  assert.equal(h.find(node => node.type === 'time', h.rows()[0])[0].props.dateTime, exact.occurred_at);
  assert.match(text(h.rows()[1]), /semaines 9 et 10/);
  assert.match(text(h.rows()[2]), /semaines 7 et 8/);
  assert.equal(text(h.rows().at(-1)), '2 févr. 2026Emplacement de départZone 20.0°C');
  assert.equal(h.find(node => node.type === 'small', h.rows().at(-1)).length, 0);
});

for (const language of ['fr', 'en']) {
  test(`${language}: lifecycle departures and entries stay distinct without phantom audit transfers`, () => {
    const origin = { id: 20, name: 'Zone 20.0°C' };
    const exit = { id: 'lifecycle:exit', kind: 'deactivation', provenance: 'lifecycle_audit',
      occurred_at: '2026-10-05T14:22:00+02:00', temporal_precision: 'exact', reconciliation_status: 'recorded',
      from_thermal_zone: origin, to_thermal_zone: null, actor: { first_name: 'Anthony', last_name: 'Audit' },
      evidence: { closed_location_ids: [8], period_pairing_reliable: true }, notes: '' };
    const phantom = { ...exit, id: 'lifecycle:phantom', kind: 'reactivation', occurred_at: null,
      temporal_precision: 'unresolved', from_thermal_zone: null, evidence: {} };
    for (const destination of [origin, { id: 15, name: 'Zone 15.0°C' }]) {
      const entry = { ...exit, id: 'movement:51', kind: 'reactivation', provenance: 'box_movement',
        occurred_at: '2026-10-05T16:03:00+02:00', from_thermal_zone: null, to_thermal_zone: destination,
        recorded_movement_id: 51, can_correct: true, evidence: { recorded_via_polypbase: true }, notes: 'Kept note' };
      const audit = { ...entry, id: 'lifecycle:entry', provenance: 'lifecycle_audit', recorded_movement_id: null,
        reconciliation_status: 'paired', evidence: { paired_movement_id: 51, lifecycle_pairing_reliable: true } };
      const technicalExit = { ...phantom, id: 'lifecycle:unresolved-exit', kind: 'deactivation' };
      const input = [exit, entry, audit, phantom, technicalExit];
      const before = JSON.stringify(input);
      const h = createInsights({ language, labels: labelsFor(language), historyEvents: input,
        historyStartingLocation: { observed_on: '2026-02-02', thermal_zone: origin },
        correctableMovementId: 51, correctMovementLabel: 'Edit', onCorrectMovement() {} });
      assert.equal(h.rows().length, 3);
      assert.deepEqual(h.find(node => node.props.className === 'movement-event').map(text),
        [catalogs[language].historyReactivation, catalogs[language].historyDeactivation, catalogs[language].historyStartingLocation]);
      assert.deepEqual(h.find(node => node.props.className === 'movement-locations').map(text),
        [catalogs[language].historyLocationEntry.replace('{zone}', destination.name),
          catalogs[language].historyLocationExit.replace('{zone}', origin.name), origin.name]);
      assert.equal(h.find(node => node.props.className === 'movement-arrow').length, 0);
      assert.equal(h.find(node => node.props.className?.includes('movement-correct-button')).length, 1);
      assert.equal(h.html.includes(catalogs[language].historyNoDestination), false);
      assert.equal(h.html.includes(catalogs[language].historyUnresolved), false);
      assert.equal(h.find(node => node.type === 'details').length, 0);
      assert.equal(JSON.stringify(input), before);
      const actualAssignment = createInsights({ language, labels: labelsFor(language),
        historyEvents: [{ ...entry, kind: 'initial_location' }, phantom] });
      assert.equal(actualAssignment.rows().length, 1);
      assert.equal(actualAssignment.html.includes(catalogs[language].historyReactivation), false,
        'an assignment is not relabelled as a reactivation by proximity');
      assert.ok(actualAssignment.html.includes(catalogs[language].historyRecordedViaPolypbase));
    }
  });
}

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
