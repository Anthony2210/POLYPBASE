import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as scales from 'd3-scale';
import * as shapes from 'd3-shape';
import { resourceClient } from './resource-test-harness.mjs';

const read = path => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
function load(path, imports = {}, globals = {}) {
  const exports = {};
  const code = ts.transpileModule(read(path), { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  vm.runInNewContext(code, { exports, Date, AbortController, ...globals,
    require(name) { assert.ok(name in imports, `Unexpected import ${name}`); return imports[name]; },
  });
  return exports;
}
const history = load('utils/boxHistory.ts');
const fr = load('i18n/fr.ts').fr;
const en = load('i18n/en.ts').en;
const t = key => en[key];
const origin = { id: 1, name: 'Origin' }, destination = { id: 2, name: 'Destination' };
const event = (id, kind, at, evidence = {}, extra = {}) => ({ id, kind, occurred_at: at,
  temporal_precision: at ? 'exact' : 'unresolved', iso_year: null, iso_week: null,
  from_thermal_zone: origin, to_thermal_zone: destination, actor: null,
  provenance: 'box_movement', evidence, reconciliation_status: 'recorded',
  recorded_movement_id: null, can_correct: false, notes: '', ...extra });
const location = { id: 4, starts_at: '2026-07-06T08:00:00Z', ends_at: '2026-07-06T09:00:00Z',
  thermal_zone: destination, end_date_unknown: false };
const movements = [event('movement:1', 'ordinary', location.starts_at,
  { period_pairing_reliable: true }, { recorded_movement_id: 1 }),
  event('movement:2', 'ordinary', location.ends_at,
    { period_pairing_reliable: true }, { recorded_movement_id: 2 })];
const boundaries = ['start', 'end'].map((boundary, index) => event(`location:4:${boundary}`, 'location_evidence',
  index ? location.ends_at : location.starts_at, { location_id: 4, boundary,
    period_pairing_reliable: true, paired_movement_id: index + 1 }));
const evidence = [...movements, ...boundaries];
const project = (events = evidence, locations = [location], dates = []) => history.projectBoxHistory(events, locations, dates, t);

function inactiveGap(start = location.starts_at, end = location.ends_at) {
  const interval = { deactivation_event_id: 'lifecycle:1', reactivation_event_id: 'lifecycle:2',
    previous_location_id: 3, next_location_id: 4, reactivation_movement_id: 2,
    starts_at: start, ends_at: end, duration_seconds: (Date.parse(end) - Date.parse(start)) / 1000,
    measurement_overlap_possible: false, masking_eligible: true };
  const proof = { inactive_pairing_reliable: true, inactive_interval: interval };
  const events = [event('lifecycle:1', 'deactivation', start, proof, { provenance: 'lifecycle_audit' }),
    event('lifecycle:2', 'reactivation', end, { ...proof, lifecycle_pairing_reliable: true, paired_movement_id: 2 },
      { provenance: 'lifecycle_audit' }),
    event('movement:2', 'reactivation', end, {}, { recorded_movement_id: 2 })];
  const locations = [{ ...location, id: 3, starts_at: '2025-01-01T00:00:00Z', ends_at: start },
    { ...location, starts_at: end, ends_at: null }];
  return { events, locations, interval };
}
const gap = inactiveGap();
const gapProject = (events = gap.events, locations = gap.locations, dates = []) => project(events, locations, dates);

test('short active stays and ordinary move markers are never masked', () => {
  const before = JSON.stringify(evidence);
  assert.equal(project().locations.length, 1);
  assert.equal(project().events.length, 2);
  assert.equal(project().maskedLocationIds.size, 0);
  assert.equal(JSON.stringify(evidence), before);
});

test('only reciprocal explicit inactive evidence hides both lifecycle markers and their movement, never bands or history', () => {
  const before = JSON.stringify(gap);
  assert.equal(gapProject().events.length, 0);
  assert.equal(gapProject().locations.length, 2);
  assert.equal(gapProject().maskedLocationIds.size, 0);
  assert.equal(JSON.stringify(gap), before);
  assert.equal(gapProject([...gap.events].reverse()).events.length, 0);
  for (const changed of [
    gap.events.filter(row => row.id !== 'lifecycle:2'),
    gap.events.map(row => row.id !== 'lifecycle:2' ? row : { ...row, reconciliation_status: 'unresolved' }),
    gap.events.map(row => row.id !== 'lifecycle:2' ? row : { ...row, temporal_precision: 'unresolved' }),
    gap.events.map(row => ({ ...row, evidence: { ...row.evidence, inactive_pairing_reliable: false } })),

    gap.events.map(row => row.id !== 'lifecycle:2' ? row : { ...row, evidence: { ...row.evidence,
      inactive_interval: { ...gap.interval, previous_location_id: 999 } } }),
    ...gap.events.map(row => [...gap.events, row]),
  ]) assert.ok(gapProject(changed).events.length > 0);
  for (const patch of [{ masking_eligible: false }, { measurement_overlap_possible: true },
    { measurement_overlap_possible: undefined }, { duration_seconds: 10 }, { starts_at: '2026-07-06' }]) {
    const changed = gap.events.map(row => ({ ...row, evidence: { ...row.evidence,
      inactive_interval: { ...gap.interval, ...patch } } }));
    assert.ok(gapProject(changed).events.length > 0);
  }
  assert.ok(gapProject(gap.events, gap.locations.map(row => ({ ...row, end_date_unknown: true }))).events.length > 0);
});

test('boundary and uncertain records including zero/zero retain markers; backend precision veto is authoritative', () => {
  for (const date of ['2026-07-06', '2026-07-06T08:30:00Z', location.starts_at, location.ends_at, 'invalid']) {
    const record = { measured_on: date, polyp_count: 0, ephyrae_count: 0 };
    assert.equal(gapProject(gap.events, gap.locations, [record.measured_on]).events.length, 2);
    assert.equal(record.polyp_count, 0);
    assert.equal(record.ephyrae_count, 0);
  }
  assert.equal(gapProject(gap.events, gap.locations, ['2026-07-07']).events.length, 0);
  // A legacy-unverified record can cover the whole week, even outside its displayed day.
  const legacy = gap.events.map(row => ({ ...row, evidence: { ...row.evidence,
    inactive_interval: { ...gap.interval, measurement_overlap_possible: true, masking_eligible: false } } }));
  assert.equal(gapProject(legacy, gap.locations, ['2026-07-05']).events.length, 2);
});

test('elapsed seconds govern 24h and DST boundaries, not wall-clock dates', () => {
  for (const [start, end, masked] of [
    ['2026-07-06T08:00:00Z', '2026-07-07T08:00:00Z', false],
    ['2026-07-06T08:00:00Z', '2026-07-07T07:59:59Z', true],
    ['2026-03-28T12:00:00+01:00', '2026-03-29T12:00:00+02:00', true],
    ['2026-10-24T12:00:00+02:00', '2026-10-25T12:00:00+01:00', false],
    ['2026-07-06T08:00:00Z', '2026-07-06T08:00:00Z', false],
  ]) {
    const fixture = inactiveGap(start, end);
    assert.equal(gapProject(fixture.events, fixture.locations).events.length === 0, masked);
  }
});

test('week reconstructions remain intervals across ISO year rollover, with no fake instant', () => {
  const historical = event('historical:1', 'historical_reconstruction', null, {}, {
    temporal_precision: 'iso_week', iso_year: 2026, iso_week: 1,
    reconciliation_status: 'accepted', can_correct: false,
  });
  const plotted = project([historical]).events[0];
  assert.equal(plotted.date, '2026-01-01');
  assert.equal(plotted.intervalStart, '2025-12-29');
  assert.equal(plotted.title, 'Movement');
  assert.equal(historical.occurred_at, null);
  assert.equal(plotted.intervalEnd, '2026-01-04');
  assert.equal(plotted.instant, undefined);
  assert.equal(plotted.temporalLabel, 'Week 1 of 2026');
  assert.equal(history.historyWeekLabel(historical, key => fr[key]), 'Semaine 1 de 2026');
});

test('reconstruction notes use real ISO week bounds in both locales, including month and year changes', () => {
  for (const [year, week, frRange, enRange] of [
    [2026, 38, '14 au 20 septembre 2026', '14 to 20 September 2026'],
    [2026, 40, '28 septembre au 4 octobre 2026', '28 September to 4 October 2026'],
    [2026, 1, '29 décembre 2025 au 4 janvier 2026', '29 December 2025 to 4 January 2026'],
  ]) {
    const source = event('historical', 'historical_reconstruction', null, {}, {
      temporal_precision: 'iso_week', reconciliation_status: 'accepted', iso_year: year, iso_week: week,
    });
    for (const [language, catalog, range] of [['fr', fr, frRange], ['en', en, enRange]]) {
      assert.equal(history.historyReconstructionNote(source, language, key => catalog[key]),
        `${catalog.historyAcceptedReconstruction} ${range}.`);
    }
    assert.equal(source.occurred_at, null);
  }
});

test('invalid ISO weeks and stored or proposed weeks never establish a historical chart date', () => {
  for (const patch of [
    { iso_year: null }, { iso_week: null }, { iso_week: 0 }, { iso_week: 54 }, { iso_week: 1.5 },
    { iso_year: 2025, iso_week: 53 }, { reconciliation_status: 'blocked' }, { reconciliation_status: 'unresolved' },
    { temporal_precision: 'unresolved' },
  ]) {
    const source = event('historical', 'historical_reconstruction', null,
      { stored_iso_year: 2026, stored_iso_week: 1, proposed_iso_year: 2026, proposed_iso_week: 1 },
      { temporal_precision: 'iso_week', reconciliation_status: 'accepted', iso_year: 2026, iso_week: 1, ...patch });
    assert.equal(project([source]).events.length, 0);
    assert.equal(history.historyReconstructionNote(source, 'en', t), en.historyReconstructionUnresolved);
  }
});

test('unresolved persisted reconstructions never become accepted chart intervals', () => {
  const unresolved = event('historical:1', 'historical_reconstruction', null, { accepted_at: '2026-01-01' }, {
    temporal_precision: 'iso_week', iso_year: 2026, iso_week: 1, reconciliation_status: 'unresolved',
  });
  assert.equal(project([unresolved]).events.length, 0);
  assert.equal(project([{ ...unresolved, temporal_precision: 'unresolved' }]).events.length, 0);
});

test('history groups interleave weeks and exact events chronologically without assigning week instants', () => {
  const weekly = (id, week) => event(id, 'historical_reconstruction', null, {}, {
    temporal_precision: 'iso_week', iso_year: 2026, iso_week: week, reconciliation_status: 'accepted',
  });
  const rows = [event('old', 'ordinary', '2025-12-25T10:00:00Z'), weekly('week2', 2),
    event('new', 'ordinary', '2026-01-12T10:00:00Z'), weekly('week1', 1),
    event('week2-exact', 'ordinary', '2026-01-06T10:00:00Z'), event('unknown', 'location_evidence', null)];
  const before = JSON.stringify(rows);
  const result = history.historyTimelineEntries(rows, [], []);
  assert.deepEqual(Array.from(result, row => row.id), ['new', 'week2', 'week2-exact', 'week1', 'old', 'unknown']);
  assert.equal(result[1].occurred_at, null);
  assert.equal(JSON.stringify(rows), before);
  assert.deepEqual(Array.from(history.historyTimelineEntries([...rows].reverse(), [], []), row => row.id),
    Array.from(result, row => row.id));
});

const candidate = (id, status, extra = {}) => ({ id, box_id: 7, iso_year: 2026, iso_week: 1,
  from_thermal_zone_id: 1, to_thermal_zone_id: 2, reconciliation_status: status,
  classification: status === 'review_required' ? 'eligible' : status,
  blockers: status === 'blocked' ? ['measurement_week_inconsistent'] : [],
  evidence: { attributed_week_start: '2025-12-29', destination_week_start: '2026-01-05' }, ...extra });

test('pending transition labels require explicit provenance and never certify a proposed week or duplicate a recorded movement', () => {
  const pending = candidate('pending', 'blocked', { evidence: { origin_location_id: 3, destination_location_id: 4, represented_movement_ids: [] } });
  const rows = history.historyTimelineEntries([], [pending], [location, { ...location, thermal_zone: origin }]);
  assert.equal(rows.length, 1);
  assert.equal(history.isUnconfirmedHistoryTransition(rows[0]), true);
  assert.equal(rows[0].temporal_precision, 'unresolved');
  assert.equal(rows[0].occurred_at, null);
  assert.equal(rows[0].iso_week, null);
  assert.equal(project(rows).events.length, 0);
  const assessment = { ...rows[0], provenance: 'transition_assessment' };
  assert.equal(history.isUnconfirmedHistoryTransition(assessment), true);
  for (const patch of [
    { provenance: 'box_location' }, { kind: 'qualification', provenance: 'box_movement' },
    { evidence: {} }, { evidence: { represented_movement_ids: [1] } },
    { evidence: { ...assessment.evidence, classification: 'initial_assignment' }, provenance: 'transition_assessment' },
    { evidence: { represented_movement_ids: [] }, provenance: 'transition_assessment' },
  ]) assert.equal(history.isUnconfirmedHistoryTransition({ ...assessment, ...patch }), false);
});

test('accepted and already represented candidates do not duplicate persisted events or invent reconstruction rows', () => {
  const events = [event('historical:1', 'historical_reconstruction', null, {}, {
    reconciliation_status: 'accepted', temporal_precision: 'iso_week', iso_year: 2026, iso_week: 1,
  }), event('movement:1', 'ordinary', '2026-01-05T00:00:00+01:00')];
  const candidates = [candidate('accepted', 'accepted'), candidate('recorded', 'already_represented')];
  assert.equal(history.historyTimelineEntries(events, candidates, []).length, 2);
  assert.equal(history.historyTimelineEntries([], candidates, []).length, 0);
});

test('only blocked and review-required candidates become unresolved diagnostics with explicitly proposed origin weeks', () => {
  const candidates = ['blocked', 'review_required', 'accepted', 'already_represented', 'unknown'].map(status => candidate(status, status));
  const rows = history.historyTimelineEntries([], candidates, []);
  assert.equal(rows.length, 2);
  for (const row of rows) {
    assert.equal(row.kind, 'location_evidence');
    assert.equal(row.temporal_precision, 'unresolved');
    assert.equal(row.iso_year, null);
    assert.equal(row.iso_week, null);
    assert.equal(row.occurred_at, null);
    assert.equal(row.evidence.proposed_iso_week, 1);
    assert.equal(row.evidence.attributed_week_start, '2025-12-29');
    assert.equal(row.can_correct, false);
  }
  assert.equal(project(rows, []).events.length, 0);
});

test('missing-week transitions and missing location history assessments remain unresolved diagnostic rows', () => {
  const assessments = [candidate('missing-week', 'blocked', { classification: 'insufficient_evidence',
    blockers: ['adjacent_measurement_evidence_missing'] }),
  { id: 'no-locations', box_id: 7, classification: 'insufficient_evidence', reconciliation_status: 'blocked',
    blockers: ['location_history_missing'], evidence: {} }];
  const before = JSON.stringify(assessments);
  const rows = history.historyTimelineEntries([], [], [], assessments);
  assert.equal(rows.length, 2);
  for (const row of rows) {
    assert.equal(row.provenance, 'transition_assessment');
    assert.equal(row.temporal_precision, 'unresolved');
    assert.equal(row.evidence.classification, 'insufficient_evidence');
  }
  assert.equal(project(rows, []).events.length, 0);
  assert.equal(JSON.stringify(assessments), before);
});

test('initial assignment assessment enriches existing period evidence without adding an event or mutating sources', () => {
  const initial = { id: 'initial', box_id: 7, classification: 'initial_assignment', reconciliation_status: 'not_applicable',
    blockers: [], evidence: { location_id: 4, stored_start: location.starts_at, physical_occurrence_proven: false } };
  const before = JSON.stringify(boundaries);
  const rows = history.historyTimelineEntries(boundaries, [], [location], [initial]);
  assert.equal(rows.length, 2);
  assert.equal(rows.find(row => row.id === 'location:4:start').evidence.assessment.classification, 'initial_assignment');
  assert.equal(JSON.stringify(boundaries), before);
  const standalone = history.historyTimelineEntries([], [], [location], [initial]);
  assert.equal(standalone.length, 1);
  assert.equal(standalone[0].temporal_precision, 'unresolved');
  assert.equal(standalone[0].evidence.classification, 'initial_assignment');
});

test('all final backend blockers have localized labels with a conservative unknown-code fallback', () => {
  const codes = ['measurement_environment_attribution_unresolved', 'measurement_week_inconsistent', 'recorded_period_link_mismatch',
    'multiple_recorded_representations', 'historical_receipt_evidence_changed', 'historical_receipt_audit_unresolved',
    'historical_and_recorded_event_coexist', 'location_history_missing'];
  for (const catalog of [fr, en]) {
    for (const code of codes) {
      const label = history.historyBlockerLabel(code, key => catalog[key]);
      assert.ok(label);
      assert.notEqual(label, code);
      assert.notEqual(label, catalog.historyReasonUnknown);
    }
    assert.equal(history.historyBlockerLabel('future_new_reason', key => catalog[key]), catalog.historyReasonUnknown);
  }
});

test('chart kinds exclude initial, qualification, unclassified, unresolved and period evidence', () => {
  const events = ['ordinary', 'deactivation', 'reactivation', 'initial_location', 'qualification', 'unclassified', 'location_evidence']
    .map(kind => event(kind, kind, location.starts_at));
  events.push(event('unresolved', 'ordinary', null));
  assert.deepEqual(Array.from(project(events, []).events, row => row.kind), ['movement', 'deactivation', 'reactivation']);
});

test('only explicitly paired reactivation sources collapse, never timestamp coincidences', () => {
  const move = event('movement:5', 'reactivation', location.starts_at, {}, { recorded_movement_id: 5 });
  const audit = event('lifecycle:6', 'reactivation', location.starts_at,
    { lifecycle_pairing_reliable: true, paired_movement_id: 5 }, { provenance: 'lifecycle_audit' });
  assert.equal(project([move, audit], []).events.length, 1);
  assert.equal(project([move, { ...audit, evidence: {} }], []).events.length, 1);
});

test('timeline collapses unique explicit lifecycle pairs without losing correction or audit evidence', () => {
  for (const kind of ['reactivation', 'qualification']) {
    const move = event('movement:5', kind, location.starts_at, {}, {
      recorded_movement_id: 5, can_correct: true, notes: 'Original movement note',
    });
    const audit = event('lifecycle:6', kind, location.starts_at,
      { lifecycle_pairing_reliable: true, paired_movement_id: 5 }, { provenance: 'lifecycle_audit' });
    const inputs = [move, audit];
    const before = JSON.stringify(inputs);
    const rows = history.historyTimelineEntries(inputs, [], []);
    assert.equal(rows.length, 1);
    assert.equal(rows[0].id, move.id);
    assert.equal(rows[0].can_correct, true);
    assert.equal(rows[0].recorded_movement_id, 5);
    assert.equal(rows[0].notes, move.notes);
    assert.equal(rows[0].evidence.paired_lifecycle_events[0].id, audit.id);
    assert.equal(JSON.stringify(inputs), before);
    for (const unpaired of [
      { ...audit, evidence: {} },
      { ...audit, occurred_at: location.ends_at },
      { ...audit, kind: 'unclassified' },
    ]) assert.equal(history.historyTimelineEntries([move, unpaired], [], []).length, 2);
    assert.equal(history.historyTimelineEntries([move, audit, { ...audit, id: 'lifecycle:7' }], [], []).length, 3);
  }
});

function freezeEvidence(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.values(value).forEach(freezeEvidence);
    Object.freeze(value);
  }
  return value;
}

function assertUnresolvedAudit(row, source) {
  assert.equal(row.id, source.id);
  assert.equal(row.kind, source.kind);
  assert.equal(row.temporal_precision, 'unresolved');
  assert.equal(row.reconciliation_status, 'unresolved');
  assert.equal(row.occurred_at, null);
  assert.equal(row.iso_year, null);
  assert.equal(row.iso_week, null);
  assert.equal(JSON.stringify(row.evidence.source_event), JSON.stringify(source));
  assert.equal(row.evidence.source_event.occurred_at, source.occurred_at);
  for (const [key, value] of Object.entries(source.evidence)) {
    assert.equal(JSON.stringify(row.evidence[key]), JSON.stringify(value));
  }
}

const lifecycleMove = (kind = 'reactivation') => event('movement:5', kind, location.starts_at,
  { revision: 4, original: { kept: true } }, { recorded_movement_id: 5, can_correct: true,
    actor: { first_name: 'Original', last_name: 'Author', email: 'author@example.test' }, notes: 'Original note' });
const lifecycleAudit = (id = 'lifecycle:6', kind = 'reactivation') => event(id, kind, location.starts_at,
  { lifecycle_pairing_reliable: true, paired_movement_id: 5, audit_id: id,
    recorded_at: '2026-07-08T12:00:00Z', original: { kept: true } },
  { provenance: 'lifecycle_audit', iso_year: 2026, iso_week: 28, notes: 'Audit note' });

for (const kind of ['reactivation', 'qualification']) {
  test(`${kind}: competing audit claims keep complete unresolved sources and the recorded movement`, () => {
    const move = lifecycleMove(kind);
    const audits = [lifecycleAudit('lifecycle:6', kind), lifecycleAudit('lifecycle:7', kind)];
    const input = freezeEvidence([move, ...audits]);
    const before = JSON.stringify(input);
    for (const events of [input, [...input].reverse()]) {
      const rows = history.historyTimelineEntries(events, [], []);
      assert.equal(rows.length, 3);
      assert.equal(rows.find(row => row.id === move.id), move);
      for (const audit of audits) assertUnresolvedAudit(rows.find(row => row.id === audit.id), audit);
      const plotted = project(events, []).events;
      assert.deepEqual(Array.from(plotted, row => row.id), kind === 'reactivation' ? [move.id] : []);
      if (kind === 'reactivation') {
        assert.equal(plotted[0].kind, 'reactivation');
        assert.equal(plotted[0].instant, move.occurred_at);
      }
    }
    assert.equal(JSON.stringify(input), before);
  });

  test(`${kind}: one audit matching multiple movement rows cannot certify or collapse either row`, () => {
    const move = lifecycleMove(kind), audit = lifecycleAudit('lifecycle:6', kind);
    const input = freezeEvidence([move, { ...move, id: 'movement:duplicate' }, audit]);
    const before = JSON.stringify(input);
    const rows = history.historyTimelineEntries(input, [], []);
    assert.equal(rows.length, 3);
    assertUnresolvedAudit(rows.find(row => row.id === audit.id), audit);
    for (const recorded of input.slice(0, 2)) assert.equal(rows.find(row => row.id === recorded.id), recorded);
    assert.deepEqual(Array.from(project(input, []).events, row => row.id),
      kind === 'reactivation' ? [move.id, 'movement:duplicate'] : []);
    assert.equal(JSON.stringify(input), before);
  });

  test(`${kind}: unique valid pair shares the recorded identity, time and complete evidence in both projections`, () => {
    const move = lifecycleMove(kind), audit = lifecycleAudit('lifecycle:6', kind);
    const input = freezeEvidence([audit, move]);
    const before = JSON.stringify(input);
    const rows = history.historyTimelineEntries(input, [], []);
    assert.equal(rows.length, 1);
    const { evidence: combinedEvidence, ...recorded } = rows[0];
    const { evidence: sourceEvidence, ...original } = move;
    assert.equal(JSON.stringify(recorded), JSON.stringify(original));
    assert.equal(JSON.stringify(combinedEvidence), JSON.stringify({ ...sourceEvidence, paired_lifecycle_events: [audit] }));
    const plotted = project(input, []).events;
    assert.deepEqual(Array.from(plotted, row => row.id), kind === 'reactivation' ? [move.id] : []);
    if (kind === 'reactivation') {
      assert.equal(plotted[0].kind, kind);
      assert.equal(plotted[0].instant, rows[0].occurred_at);
    }
    assert.equal(JSON.stringify(input), before);
  });
}

test('missing and contradictory references never certify lifecycle occurrences or use audit recording time', () => {
  const move = lifecycleMove(), audit = lifecycleAudit();
  const variants = [
    { ...audit, evidence: {} },
    ...[null, undefined, '5', 0, -5, 999].map(id => ({ ...audit, evidence: { ...audit.evidence, paired_movement_id: id } })),
    { ...audit, evidence: { ...audit.evidence, lifecycle_pairing_reliable: false } },
    { ...audit, occurred_at: location.ends_at },
    { ...audit, occurred_at: null, temporal_precision: 'unresolved' },
    { ...audit, occurred_at: '2026-07-06' },
    { ...audit, kind: 'qualification' },
    { ...audit, reconciliation_status: 'unresolved' },
  ];
  for (const unpaired of variants) {
    const input = freezeEvidence([move, unpaired]);
    const before = JSON.stringify(input);
    const rows = history.historyTimelineEntries(input, [], []);
    assert.equal(rows.length, 2);
    assert.equal(rows.find(row => row.id === move.id), move);
    assertUnresolvedAudit(rows.find(row => row.id === audit.id), unpaired);
    assert.deepEqual(Array.from(project(input, []).events, row => row.id), [move.id]);
    assert.equal(JSON.stringify(input), before);
  }
  const rows = history.historyTimelineEntries([audit], [], []);
  assertUnresolvedAudit(rows[0], audit);
  assert.equal(project([audit], []).events.length, 0);
});

test('a contradictory competing claim blocks a valid-looking pair before time or kind validation', () => {
  const move = lifecycleMove(), audit = lifecycleAudit();
  for (const rival of [
    { ...lifecycleAudit('lifecycle:7'), occurred_at: location.ends_at },
    { ...lifecycleAudit('lifecycle:7'), kind: 'qualification' },
    { ...lifecycleAudit('lifecycle:7'), evidence: { paired_movement_id: 5, lifecycle_pairing_reliable: false } },
    { ...lifecycleAudit('lifecycle:7'), temporal_precision: 'unresolved', occurred_at: null },
  ]) {
    const rows = history.historyTimelineEntries([move, audit, rival], [], []);
    assert.equal(rows.length, 3);
    assertUnresolvedAudit(rows.find(row => row.id === audit.id), audit);
    assertUnresolvedAudit(rows.find(row => row.id === rival.id), rival);
    assert.deepEqual(Array.from(project([move, audit, rival], []).events, row => row.id), [move.id]);
  }
});

test('ambiguous reactivation pairing vetoes otherwise reciprocal under-24h masking without losing bands or sources', () => {
  for (const rival of [
    { ...gap.events[1], id: 'lifecycle:competing' },
    { ...gap.events[1], id: 'lifecycle:contradictory', occurred_at: '2026-07-06T10:00:00Z' },
    { ...gap.events[2], id: 'movement:duplicate' },
  ]) {
    const input = freezeEvidence([...gap.events, rival]);
    const before = JSON.stringify({ input, locations: gap.locations });
    const rows = history.historyTimelineEntries(input, [], gap.locations);
    assert.equal(rows.length, 4);
    for (const audit of input.filter(row => row.provenance === 'lifecycle_audit' && row.kind === 'reactivation')) {
      assertUnresolvedAudit(rows.find(row => row.id === audit.id), audit);
    }
    const projected = gapProject(input);
    assert.equal(projected.events.filter(row => row.kind === 'deactivation').length, 1);
    assert.deepEqual(Array.from(projected.events.filter(row => row.kind === 'reactivation'), row => row.id),
      input.filter(row => row.provenance === 'box_movement').map(row => row.id));
    assert.equal(projected.locations.length, 2);
    assert.equal(projected.maskedLocationIds.size, 0);
    assert.equal(JSON.stringify({ input, locations: gap.locations }), before);
  }
});

const chart = load('components/BiologicalTrendChart.tsx', {
  react: {}, 'react/jsx-runtime': {}, 'd3-scale': scales, 'd3-shape': shapes,
  '../utils/userIdentity': load('utils/userIdentity.ts'), '../utils/dateFormat': load('utils/dateFormat.ts'),
  '../utils/chartBiology': load('utils/chartBiology.ts'), '../utils/chartLocations': load('utils/chartLocations.ts'),
});
test('week intervals overlap and clip at a chart window without changing their evidence date', () => {
  const events = project([event('historical:1', 'historical_reconstruction', null, {}, {
    temporal_precision: 'iso_week', iso_year: 2026, iso_week: 1, reconciliation_status: 'accepted',
  })], []).events;
  const geometry = chart.buildGeometry([], [], events, '2026-01-01', '2026-01-10', false);
  assert.equal(geometry.eventPoints.length, 1);
  assert.equal(geometry.eventPoints[0].x, geometry.xPosition('2026-01-01'));
  assert.equal(geometry.eventPoints[0].event.date, '2026-01-01');
  assert.equal(geometry.eventPoints[0].event.intervalStart, '2025-12-29');
  const partial = chart.buildGeometry([], [], events, '2025-12-29', '2025-12-30', false);
  assert.equal(partial.eventPoints.length, 1, 'week remains visible even when its midpoint is outside the window');
  assert.equal(partial.eventPoints[0].x, partial.xPosition('2025-12-30'));
  assert.equal(chart.formatEventDate(events[0]), 'Week 1 of 2026');
});

test('timestamp range and daily placement agree near local midnight', () => {
  const at = '2026-01-01T23:30:00-08:00';
  const day = history.localHistoryDay(at);
  const geometry = chart.buildGeometry([], [], [{ id: 1, kind: 'movement', date: at, instant: at, title: 'Movement' }],
    day, '2026-01-10', false);
  assert.equal(geometry.eventPoints[0].x, geometry.xPosition(day));
});

function hookHarness() {
  let slots = [], cursor = 0, pending = [], cleanups = [], props = { boxId: 7, key: {} };
  let value;
  const requests = [];
  const client = resourceClient({ fetch: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })) });
  const react = {
    useState(initial) { const i = cursor++; if (!(i in slots)) slots[i] = initial;
      return [slots[i], next => { slots[i] = typeof next === 'function' ? next(slots[i]) : next; }]; },
    useMemo(fn, deps) { const i = cursor++;
      if (!slots[i] || deps.some((dep, index) => dep !== slots[i].deps[index])) slots[i] = { deps, value: fn() };
      return slots[i].value; },
    useEffect(fn, deps) { const i = cursor++;
      if (!slots[i] || deps.some((dep, index) => dep !== slots[i][index])) {
        slots[i] = deps; pending.push(() => { cleanups[i]?.(); cleanups[i] = fn(); });
      } },
  };
  const { useBoxHistory } = load('hooks/useBoxHistory.ts', { react, '../api/client': client });
  const render = () => { cursor = 0; value = useBoxHistory(props.boxId, props.key); pending.splice(0).forEach(fn => fn()); return value; };
  return { requests, client, render, set: next => { props = { ...props, ...next }; return render(); }, get value() { return value; } };
}
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const respond = (request, events = movements, status = 200, candidates, assessments) => request.resolve({ ok: status === 200, status,
  text: async () => JSON.stringify({ events, candidates, assessments }) });

test('optional candidates and assessments are retained and cleared on scope changes, failure and legacy responses', async () => {
  const h = hookHarness();
  h.render();
  const candidates = [{ id: 'candidate', blockers: ['source_location_provenance_unresolved'], evidence: { physical_occurrence_proven: false } }];
  const assessments = [{ id: 'missing-week', box_id: 7, classification: 'insufficient_evidence',
    reconciliation_status: 'blocked', blockers: ['adjacent_measurement_evidence_missing'], evidence: {} }];
  respond(h.requests[0], [], 200, candidates, assessments); await tick();
  assert.equal(h.render().candidates[0].id, 'candidate');
  assert.equal(h.value.candidates[0].evidence.physical_occurrence_proven, false);
  assert.equal(h.value.assessments[0].classification, 'insufficient_evidence');
  h.set({ boxId: 8 });
  assert.equal(h.value.candidates.length, 0);
  assert.equal(h.value.assessments.length, 0);
  respond(h.requests[1], [], 503); await tick();
  assert.equal(h.render().candidates.length, 0);
  assert.equal(h.value.assessments.length, 0);
  h.value.retry(); h.render();
  respond(h.requests[2], []); await tick();
  assert.equal(h.render().candidates.length, 0);
  assert.equal(h.value.assessments.length, 0);
});

test('malformed assessments fail conservatively rather than silently discarding diagnostics', async () => {
  const h = hookHarness();
  h.render();
  respond(h.requests[0], [], 200, [], { invalid: true }); await tick();
  assert.equal(h.render().failed, true);
  assert.equal(h.value.assessments.length, 0);
});

test('partial history status includes missing-week assessments and pending candidates, not represented candidates or initial assignment alone', () => {
  const jsx = (type, props) => ({ type, props });
  const Status = load('components/BoxHistoryStatus.tsx', {
    'react/jsx-runtime': { jsx, jsxs: jsx },
    '../i18n': { createTranslator: language => key => (language === 'fr' ? fr : en)[key] },
  }).default;
  const state = { events: [], loading: false, failed: false, retry() {} };
  for (const language of ['fr', 'en']) {
    const status = Status({ language, history: { ...state, assessments: [{ classification: 'insufficient_evidence' }] } });
    assert.equal(status.props.role, 'status');
    assert.equal(Status({ language, history: { ...state, assessments: [{ classification: 'initial_assignment' }] } }), null);
    assert.equal(Status({ language, history: { ...state, candidates: [candidate('accepted', 'accepted')] } }), null);
    assert.ok(Status({ language, history: { ...state, candidates: [candidate('blocked', 'blocked')] } }));
  }
});

test('chart status omits partial evidence without reserving a panel and preserves loading, errors and retry', () => {
  const jsx = (type, props) => ({ type, props });
  const Status = load('components/BoxHistoryStatus.tsx', {
    'react/jsx-runtime': { jsx, jsxs: jsx },
    '../i18n': { createTranslator: language => key => (language === 'fr' ? fr : en)[key] },
  }).default;
  for (const language of ['fr', 'en']) {
    const catalog = language === 'fr' ? fr : en;
    let retries = 0;
    const state = { events: [event('unknown', 'location_evidence', null)],
      candidates: [candidate('blocked', 'blocked')], assessments: [{ classification: 'insufficient_evidence' }],
      loading: false, failed: false, retry() { retries++; } };
    const before = JSON.stringify(state);
    assert.equal(Status({ language, history: state, showPartial: false }), null);
    assert.equal(JSON.stringify(state), before, 'evidence remains available to movement history');
    assert.ok(Status({ language, history: state }), 'other history consumers retain their partial status');
    const loading = Status({ language, history: { ...state, loading: true }, showPartial: false });
    assert.equal(loading.props.children[0].props.children, catalog.historyLoading);
    const failed = Status({ language, history: { ...state, failed: true }, showPartial: false });
    assert.equal(failed.props.role, 'status');
    assert.equal(failed.props.children[0].props.children, catalog.historyFailed);
    const retry = failed.props.children[1];
    assert.equal(retry.props.children, catalog.reloadAction);
    assert.equal(retry.props.type, 'button');
    retry.props.onClick();
    assert.equal(retries, 1);
  }
});

test('history uses the organization client, scopes results, supports errors/retry and refresh after correction', async () => {
  const h = hookHarness();
  assert.equal(h.render().loading, true);
  assert.equal(h.requests[0].url, '/api/boxes/7/history/');
  assert.equal(h.requests[0].options.headers.get('X-Organization-Id'), '1');
  respond(h.requests[0]); await tick();
  assert.equal(h.render().events.length, 2);
  h.set({ key: {} });
  assert.equal(h.value.events.length, 0);
  respond(h.requests[1], [], 503); await tick();
  assert.equal(h.render().failed, true);
  h.value.retry(); h.render();
  respond(h.requests[2], []); await tick();
  assert.equal(h.render().failed, false);
  assert.equal(h.value.loading, false);
  h.set({ boxId: 8 });
  h.client.setActiveOrganizationContext(2);
  h.render();
  respond(h.requests[3], movements, 200, [], [{ id: 'stale-assessment' }]); await tick();
  assert.equal(h.render().events.length, 0);
  assert.equal(h.value.assessments.length, 0);
  respond(h.requests[4], [movements[0]], 200, [], [{ id: 'current-assessment' }]); await tick();
  assert.equal(h.render().events.length, 1);
  assert.equal(h.value.assessments[0].id, 'current-assessment');
  assert.equal(h.requests[4].options.headers.get('X-Organization-Id'), '2');
});

test('tracking and export preview consume the same projection and do not map all recorded kinds to movements', () => {
  for (const path of ['components/BoxTrackingChart.tsx', 'components/ExportsView.tsx']) {
    const source = read(path);
    assert.match(source, /projectBoxHistory\(/);
    assert.match(source, /useBoxHistory\(/);
    assert.doesNotMatch(source, /events=\{.*movements\.map/);
  }
});
