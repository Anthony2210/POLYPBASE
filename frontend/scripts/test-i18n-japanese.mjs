import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';
import { catalogKeys } from './i18n-usage.mjs';

// Japanese interface: historical and AI-reviewed subsets, English fallback, unchanged FR/EN.
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const sha256 = value => createHash('sha256').update(value).digest('hex');

function load(path, imports = {}, globals = {}) {
  const { outputText } = ts.transpileModule(read(path), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports, Intl, Date, ...globals,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), `Unexpected dependency: ${name}`);
      return imports[name];
    },
  });
  return exports;
}

function parseCsv(text) {
  text = text.replace(/^\uFEFF/, '');
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    if (quoted) {
      if (char === '"' && text[index + 1] === '"') { field += '"'; index += 1; }
      else if (char === '"') quoted = false;
      else field += char;
    } else if (char === '"') quoted = true;
    else if (char === ',') { row.push(field); field = ''; }
    else if (char === '\n') { row.push(field); rows.push(row); row = []; field = ''; }
    else if (char !== '\r') field += char;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  const [header, ...body] = rows;
  return body.map(values => Object.fromEntries(header.map((name, index) => [name, values[index]])));
}

function catalogEntries(path, name) {
  const file = ts.createSourceFile(path, read(path), ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  const declarations = file.statements.filter(ts.isVariableStatement)
    .flatMap(statement => [...statement.declarationList.declarations])
    .filter(declaration => ts.isIdentifier(declaration.name) && declaration.name.text === name);
  assert.equal(declarations.length, 1, `${path}: one exported ${name} catalog`);
  const object = declarations[0].initializer;
  assert.ok(ts.isObjectLiteralExpression(object), `${path}: object literal catalog`);
  return object.properties.map(property => {
    assert.ok(ts.isPropertyAssignment(property) && ts.isIdentifier(property.name), 'literal properties only');
    assert.ok(ts.isStringLiteral(property.initializer), `${property.name.text}: string literal value`);
    return [property.name.text, property.initializer.text];
  });
}

const fr = load('../src/i18n/fr.ts').fr;
const en = load('../src/i18n/en.ts').en;
const jaModule = load('../src/i18n/ja.ts');
const ja = jaModule.ja;
const jaEntries = catalogEntries('../src/i18n/ja.ts', 'ja');
const manifest = parseCsv(read('../../docs/translation-review/japanese/ja-manifest.csv'));
const historicalMovementKeys = JSON.parse(read('../../docs/translation-review/japanese/historical-movement-keys.json'));
const zoneChartKeys = JSON.parse(read('../../docs/translation-review/japanese/zone-chart-keys.json'));
// B26: compact probe chart period control, English fallbacks without Japanese.
const thermalPeriodKeys = JSON.parse(read('../../docs/translation-review/japanese/thermal-period-keys.json'));
// B27: inter-probe average curve, English fallbacks without Japanese.
const probeAverageKeys = JSON.parse(read('../../docs/translation-review/japanese/probe-average-keys.json'));
const potentialMovementKeys = [
  'historyPotentialMovement', 'historyPotentialAfterRecord', 'historyPotentialUnordered',
  'historyHistoricalMovement', 'historyBetweenRecords', 'historyBetweenRecordsAcrossYears',
  'historyEnvironmentOnly', 'historyTransitionReview', 'zoneMovementHistoricalEntry',
  'zoneMovementHistoricalExit', 'zoneMovementInterval', 'zoneMovementIntervalAcrossYears',
  'zoneMovementHistoricalTotals',
];
const obsolete = parseCsv(read('../../docs/translation-review/japanese/ja-obsolete-workbook-keys.csv'));
const register = parseCsv(read('../../docs/translation-review/application/register.csv').replace(/^﻿/, ''));
const adminRemoval = JSON.parse(read('../../docs/translation-review/application/admin-removal-test-fixture.json'));
const retired = new Set(Object.keys(adminRemoval.retired_line_indices));
const currentCatalogSource = language => read(`../src/i18n/${language}.ts`).replace(/\r\n/g, '\n');

// Restore the pre-removal layout for the older byte-fingerprint checks. Values
// live only in the register (FR/EN) and frozen Japanese manifest, not the fixture.
function beforeAdminRemoval(source, language) {
  const index = ['fr', 'en', 'ja'].indexOf(language);
  const values = language === 'ja'
    ? new Map(manifest.filter(row => row.surface === 'react' && row.status === 'REUSED').map(row => [row.identity, row.ja_validated]))
    : new Map(register.filter(row => row.batch === adminRemoval.batch && row.language === language.toUpperCase())
      .map(row => [row.key_or_msgid, row.initial_value]));
  const lines = source.split('\n');
  for (const [key, indices] of Object.entries(adminRemoval.retired_line_indices)
    .filter(([, indices]) => indices[index] !== null).sort((a, b) => a[1][index] - b[1][index])) {
    const value = values.get(key);
    assert.ok(value, key);
    const escaped = value.replaceAll('\\', '\\\\').replaceAll("'", "\\'");
    lines.splice(indices[index], 0, `  ${key}: '${escaped}',`);
  }
  return lines.join('\n');
}
const baselineCatalogSource = language => beforeAdminRemoval(currentCatalogSource(language), language);
const baselineFrKeys = [...catalogKeys('fr.ts', baselineCatalogSource('fr'), 'fr')];

test('B50 retires only the recorded keys and preserves the exact pre-removal catalogs', () => {
  assert.equal(retired.size, 83);
  const retiredJa = manifest.filter(row => row.surface === 'react' && retired.has(row.identity));
  assert.deepEqual(Object.fromEntries(['REUSED', 'FALLBACK_EN', 'FALLBACK_EN_REVIEW', 'AI_REVIEWED']
    .map(status => [status, retiredJa.filter(row => row.status === status).length])),
  { REUSED: 78, FALLBACK_EN: 4, FALLBACK_EN_REVIEW: 1, AI_REVIEWED: 0 });
  const runtime = loadI18n().translations;
  for (const language of ['fr', 'en', 'ja']) {
    for (const key of retired) assert.equal(Object.hasOwn(runtime[language], key), false, `${language}.${key}`);
    assert.equal(sha256(baselineCatalogSource(language)), adminRemoval.catalog_sha256[language], language);
  }
});
const speciesEvidenceText = read('../../docs/translation-review/application/species-strains-integration.json').replace(/\r\n/g, '\n');
assert.equal(sha256(speciesEvidenceText), '1323838b5e1ac8cfa94a0f42f5e9cf40417b3a85f2a22be7b55409404b434985');
const speciesEvidence = JSON.parse(speciesEvidenceText);
const speciesIntegrationKeys = speciesEvidence.catalog_additions.fr.map(line => line.trim().split(':', 1)[0]);

// B49 (worms-dna-forms, appended after demo B48): New box optional DNA
// evidence label, optional state moved into two strain labels, and four helper
// texts removed. English fallbacks only; no Japanese. Reconstruction applies it
// first, so every earlier layer still sees its own frozen catalog.
const B49 = 'B49-2026-11-18';
const B49_FIRST_ID = 1851;
const B49_ROWS_DIGEST = '1ec1bfa14f7e53b9e3b0372000f5b807ced9880024a639ba977bf1fee758db76';
const B49_NEXT_KEYS = {
  strainGpsHint: 'strainGpsInvalid',
  strainNameHint: 'speciesCodeNameNotDerivable',
  speciesSearchHint: 'speciesSearchSelected',
  speciesCreateWormsTruncated: 'speciesCreateAboveSpeciesRank',
};
const B49_APPLIED_KEYS = ['strainGps', 'strainName'];
const B49_REMOVED_KEYS = ['strainGpsHint', 'strainNameHint', 'speciesSearchHint', 'speciesCreateWormsTruncated'];
const B49_ADDED_KEYS = ['createBoxDnaEvidence'];
const B49_EXPECTED = [
  ...B49_APPLIED_KEYS.flatMap(key => [[key, 'FR', 'APPLIED'], [key, 'EN', 'APPLIED']]),
  ...B49_REMOVED_KEYS.flatMap(key => [[key, 'FR', 'REMOVED'], [key, 'EN', 'REMOVED']]),
  ...B49_ADDED_KEYS.flatMap(key => [[key, 'FR', 'ALREADY_MATCHING'], [key, 'EN', 'ALREADY_MATCHING']]),
];
// Species keys still present after B49.
const currentSpeciesKeys = speciesIntegrationKeys.filter(key => !B49_REMOVED_KEYS.includes(key));

function b49Events(rows = register) {
  const entryIds = Array.from({ length: B49_EXPECTED.length }, (_, index) => `T${B49_FIRST_ID + index}`);
  const protectedIds = new Set(entryIds);
  const events = rows.filter(row => row.batch === B49 || protectedIds.has(row.entry_id));
  assert.deepEqual(events, rows.slice(B49_FIRST_ID - 1, B49_FIRST_ID - 1 + entryIds.length));
  assert.deepEqual(events.map(row => row.entry_id), entryIds);
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language, row.status]), B49_EXPECTED);
  assert.equal(registerRowsDigest(events), B49_ROWS_DIGEST);
  for (const entry of events) {
    assert.equal(entry.surface, 'react');
    assert.equal(entry.source_file, `frontend/src/i18n/${entry.language.toLowerCase()}.ts`);
    if (entry.status === 'REMOVED') assert.ok(entry.initial_value && entry.final_value === '', entry.entry_id);
    else if (entry.status === 'APPLIED') assert.ok(entry.initial_value && entry.final_value && entry.initial_value !== entry.final_value, entry.entry_id);
    else assert.ok(entry.final_value && entry.initial_value === entry.final_value, entry.entry_id);
  }
  return events;
}

function beforeB49(source, language, rows = register) {
  const opening = ' = {\n';
  assert.ok(source.indexOf(opening) > 0, 'catalog object start');
  for (const entry of b49Events(rows).filter(row => row.language === language.toUpperCase())) {
    const key = entry.key_or_msgid;
    const present = source.split('\n').filter(line => line.startsWith(`  ${key}: `));
    if (entry.status === 'REMOVED') {
      // A removed key must be absent; its exact prior line is restored for older layers.
      assert.deepEqual(present, [], key);
      const anchor = `  ${B49_NEXT_KEYS[key]}: `;
      const lines = source.split('\n');
      assert.equal(lines.filter(line => line.startsWith(anchor)).length, 1, anchor);
      lines.splice(lines.findIndex(line => line.startsWith(anchor)), 0, `  ${key}: '${entry.initial_value}',`);
      source = lines.join('\n');
      continue;
    }
    const line = `  ${key}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, key);
    source = source.replace(line, entry.status === 'APPLIED' ? `  ${key}: '${entry.initial_value}',\n` : '');
  }
  return source;
}

// Species events were reallocated after main's thermal B32 (T1460-T1469): T1470-T1741, B33-B43.
function speciesOriginalProjection(rows) {
  const { entry_id_offset: idOffset, batch_number_offset: batchOffset } = speciesEvidence.reallocation;
  return rows.map(row => ({ ...row,
    entry_id: `T${Number(row.entry_id.slice(1)) - idOffset}`,
    batch: `B${Number(row.batch.slice(1, 3)) - batchOffset}${row.batch.slice(3)}` }));
}

function beforeSpeciesIntegration(source, language, rows = register) {
  source = beforeB49(source, language, rows);
  assert.equal(registerRowsDigest(rows.slice(0, 1469)), speciesEvidence.thermal_main_register_rows_sha256);
  const tail = rows.slice(1469, 1741);
  assert.equal(registerRowsDigest(tail), speciesEvidence.reallocated_register_rows_sha256);
  assert.equal(registerRowsDigest(speciesOriginalProjection(tail)), speciesEvidence.integrated_register_rows_sha256);
  for (const line of speciesEvidence.catalog_additions[language]) {
    assert.equal(source.split(line).length - 1, 1, line);
    source = source.replace(line, '');
  }
  for (const change of speciesEvidence.catalog_changes[language]) {
    assert.equal(source.split(change.after).length - 1, 1, change.key);
    source = source.replace(change.after, change.before);
  }
  return source;
}
const sharingKeys = Object.keys(fr).filter(key => /^share[A-Z]/.test(key));
const reviewedPath = '../../docs/translation-review/japanese/POLYPBASE_JA_59_candidates_revisees.csv';
const blockedPath = '../../docs/translation-review/japanese/POLYPBASE_JA_6_blocked.csv';
const reviewed = parseCsv(read(reviewedPath));
const blocked = parseCsv(read(blockedPath));

// Commit whose FR/EN bytes and manifest counts the historical checks freeze.
const FROZEN_BASELINE = 'c857a592e170ae6933969098c90fed95d0eb3ea5';
// Immutable thermal-control batch B13 (T845-T858): same canonical digest as
// B13_ROWS_SHA256 in docs/translation-review/application/test_check_register.py,
// computed from the original rows of commit cbc513f. Corrections are appended later.
const REGISTER_COLUMNS = ['entry_id', 'batch', 'surface', 'key_or_msgid', 'language', 'source_file',
  'initial_value', 'final_value', 'sheet_value', 'decision_ref', 'status', 'reason', 'baseline_git_head',
  'baseline_sheet_sha256'];
const B13 = 'B13-2026-11-04';
const B13_START = 844;
const B13_ENTRY_IDS = Array.from({ length: 14 }, (_, index) => `T${845 + index}`);
const B13_ROWS_SHA256 = 'ab96099e1e94d0b1c17ddf7547605960a6c46d6c3c6e44e2184d6c6cae49452b';
// Full lab provenance from 5fcc2ea, independently of the original thermal freeze.
const LAB_START = 858;
const LAB_ENTRY_IDS = Array.from({ length: 28 }, (_, index) => `T${859 + index}`);
const LAB_ROWS_SHA256 = '12474e0f6376a9e3da896e68022f45a954401cce630aff263385c4fc06e5365b';
// Explicit historical additions as renumbered in 7c8ce24, frozen separately.
const HISTORICAL_START = 886;
const HISTORICAL_ENTRY_IDS = Array.from({ length: 88 }, (_, index) => `T${887 + index}`);
const HISTORICAL_ROWS_SHA256 = 'df3d1228fa718616598ea8bf5c87f2a600007cc94a7514db773abc150960b5c7';
// Incoming 8f7cf04 events: only IDs and batch are renumbered after main history.
const LAB_REFINEMENT_START = 974;
const LAB_REFINEMENT_ENTRY_IDS = Array.from({ length: 32 }, (_, index) => `T${975 + index}`);
const LAB_REFINEMENT_BATCH = 'B17-2026-11-05';
const LAB_REFINEMENT_ROWS_SHA256 = 'c9e9c5d39cb1f27fc2bceddd7e40436cc2d79fdc31125ceb17f06d9d48b11134';
// Python json.dumps(..., ensure_ascii=False) over the rows' column values.
const registerRowsDigest = rows => sha256(`[${rows.map(row =>
  `[${REGISTER_COLUMNS.map(name => JSON.stringify(row[name])).join(', ')}]`).join(', ')}]`);
function b13IntegrityErrors(rows) {
  const b13 = rows.filter(row => row.batch === B13 && !LAB_ENTRY_IDS.includes(row.entry_id));
  const errors = [];
  const slice = rows.slice(B13_START, B13_START + B13_ENTRY_IDS.length);
  if (slice.length !== b13.length || slice.some((row, index) => row !== b13[index])) {
    errors.push('B13 rows are not exactly the contiguous register rows 845-858');
  }
  if (JSON.stringify(b13.map(row => row.entry_id)) !== JSON.stringify(B13_ENTRY_IDS)) {
    errors.push('B13 entry IDs are not exactly T845-T858 in order');
  }
  if (registerRowsDigest(b13) !== B13_ROWS_SHA256) errors.push('B13 history was modified');
  return errors;
}
function labIntegrityErrors(rows) {
  const lab = rows.filter(row => LAB_ENTRY_IDS.includes(row.entry_id));
  const errors = [];
  const slice = rows.slice(LAB_START, LAB_START + LAB_ENTRY_IDS.length);
  if (slice.length !== lab.length || slice.some((row, index) => row !== lab[index])
    || JSON.stringify(lab.map(row => row.entry_id)) !== JSON.stringify(LAB_ENTRY_IDS)) {
    errors.push('Lab events are not exactly the contiguous register rows 859-886');
  }
  if (registerRowsDigest(lab) !== LAB_ROWS_SHA256) errors.push('Lab history was modified');
  return errors;
}
// Only these independently frozen thermal, lab, historical and refinement additions are excluded.
// DEFERRED lab wording is not an approval, despite sharing B13 and its baseline.
function postBaselineReactKeys(rows = register) {
  for (const row of rows) assert.deepEqual(Object.keys(row), REGISTER_COLUMNS);
  assert.deepEqual(b13IntegrityErrors(rows), []);
  assert.deepEqual(labIntegrityErrors(rows), []);
  const historical = rows.filter(row => HISTORICAL_ENTRY_IDS.includes(row.entry_id));
  assert.deepEqual(historical, rows.slice(HISTORICAL_START, HISTORICAL_START + HISTORICAL_ENTRY_IDS.length));
  assert.deepEqual(historical.map(row => row.entry_id), HISTORICAL_ENTRY_IDS);
  assert.equal(registerRowsDigest(historical), HISTORICAL_ROWS_SHA256, 'historical movement history was modified');
  assert.equal(historicalMovementKeys.length, 44);
  assert.deepEqual([...new Set(historical.map(row => row.key_or_msgid))].sort(), [...historicalMovementKeys].sort());
  const refinement = rows.filter(row => LAB_REFINEMENT_ENTRY_IDS.includes(row.entry_id));
  assert.deepEqual(refinement, rows.slice(LAB_REFINEMENT_START, LAB_REFINEMENT_START + LAB_REFINEMENT_ENTRY_IDS.length));
  assert.deepEqual(refinement.map(row => row.entry_id), LAB_REFINEMENT_ENTRY_IDS);
  assert.equal(registerRowsDigest(refinement), LAB_REFINEMENT_ROWS_SHA256, 'lab refinement history was modified');
  assert.deepEqual(rows.filter(row => row.batch === LAB_REFINEMENT_BATCH), refinement);
  const entries = rows.filter(row => row.baseline_git_head === FROZEN_BASELINE);
  const thermalLab = rows.slice(B13_START, LAB_START + LAB_ENTRY_IDS.length);
  assert.deepEqual(entries, [...thermalLab, ...historical, ...refinement], 'only frozen thermal, lab, historical and refinement events may use the frozen baseline');
  assert.deepEqual(rows.filter(row => row.batch === B13), thermalLab, 'only frozen thermal and lab events may use B13');
  const added = new Map();
  for (const row of entries) {
    const thermal = B13_ENTRY_IDS.includes(row.entry_id);
    const movement = HISTORICAL_ENTRY_IDS.includes(row.entry_id);
    const status = thermal || movement || row.key_or_msgid === 'adminLabReferenceSalinity' ? 'ALREADY_MATCHING' : 'DEFERRED';
    const reason = movement ? 'New FR/EN' : thermal ? 'New key added' : 'New Administration > Laboratory configuration key';
    assert.ok(row.surface === 'react' && row.status === status
      && row.initial_value === row.final_value && row.reason.includes(reason),
    `${row.entry_id}: not a registered new React key`);
    const values = added.get(row.key_or_msgid) ?? {};
    assert.equal(Object.hasOwn(values, row.language), false, `${row.key_or_msgid}: duplicate ${row.language}`);
    added.set(row.key_or_msgid, { ...values, [row.language]: row.final_value });
  }
  for (const [key, values] of added) assert.deepEqual(Object.keys(values).sort(), ['EN', 'FR'], key);
  return added;
}
const postBaseline = postBaselineReactKeys();

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem: key => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
  };
}

function loadI18n(window = { localStorage: storage() }) {
  return load('../src/i18n/index.ts', { './fr': { fr }, './en': { en }, './ja': jaModule }, { window });
}

const placeholders = value => (value.match(/\{[A-Za-z_][A-Za-z0-9_]*\}/g) ?? []).sort();
const japaneseCharacter = /[぀-ヿ㐀-鿿＀-￯]/;
// Validated values that are language-neutral tokens (identical or without kana/kanji).
const NEUTRAL_VALUES = new Set(['auditMetaIdentifier', 'auditValueCsv', 'auditRelationTransfer', 'taxonomyAphiaId']);
const ADMIN_INVITATION = /管理者に(?:お問い合わせください|連絡してください)/;
const PERMISSION_KEYS = [
  'boxActivateForbidden', 'createBoxForbidden', 'manageErrorMembershipAdminRequired',
  'manageErrorResponsableRequired', 'measurementForbidden', 'moveForbidden',
  'manageErrorActiveResponsableRequired', 'boxArchiveForbidden', 'subcultureForbidden',
];

test('ja catalog is a duplicate-free subset of the current canonical FR/EN keys', () => {
  const keys = jaEntries.map(([key]) => key);
  assert.equal(new Set(keys).size, keys.length);
  assert.deepEqual(Object.keys(fr).sort(), Object.keys(en).sort());
  for (const key of keys) assert.ok(Object.hasOwn(fr, key), `${key}: not a current key`);
  assert.ok(keys.length < Object.keys(fr).length, 'Japanese is partial by design');
  const order = Object.keys(fr).filter(key => Object.hasOwn(ja, key));
  assert.deepEqual(keys, order, 'ja follows the canonical FR order');
});

test('deleted and obsolete keys are never restored in Japanese', () => {
  const removed = new Set(register.filter(row => row.surface === 'react' && row.status === 'REMOVED')
    .map(row => row.key_or_msgid));
  assert.ok(removed.size >= 219);
  const obsoleteReact = obsolete.filter(row => row.surface === 'react').map(row => row.identity);
  assert.equal(obsoleteReact.length, 217);
  for (const key of [...removed, ...obsoleteReact]) {
    assert.equal(Object.hasOwn(ja, key), false, `${key}: deleted or obsolete key in ja`);
    assert.equal(Object.hasOwn(fr, key), false, `${key}: obsolete key still in fr`);
  }
});

test('ja catalog is exactly the historical and AI-reviewed manifest union', () => {
  const react = manifest.filter(row => row.surface === 'react');
  const unmanifested = new Set([...B44_ADDED_KEYS, ...B45_ADDED_KEYS, ...B47_ADDED_KEYS, DEMO_BANNER_KEY]);
  assert.deepEqual(react.map(row => row.identity), baselineFrKeys.filter(key => !unmanifested.has(key)),
    'one row per historical key in canonical FR order; B44/B45/B47/B48 have no fabricated manifest provenance');
  const included = react.filter(row => ['REUSED', 'AI_REVIEWED'].includes(row.status));
  const expected = included.filter(row => !retired.has(row.identity)).map(row => [row.identity,
    row.status === 'REUSED' ? row.ja_validated : row.ja_ai_reviewed]);
  assert.deepEqual(jaEntries, expected);
  for (const row of react.filter(row => !['REUSED', 'AI_REVIEWED'].includes(row.status))) {
    assert.match(row.status, /^FALLBACK_EN/);
    assert.equal(Object.hasOwn(ja, row.identity), false, `${row.identity}: ${row.reason_code} must use English`);
  }
  const counts = react.reduce((total, row) => ({ ...total, [row.status]: (total[row.status] ?? 0) + 1 }), {});
  // Original fallbacks, movements, thermal and DNA additions, with B23 retirements and B23/B24 additions.
  assert.equal(postBaseline.size, 81);
  assert.deepEqual(counts, { REUSED: 930, FALLBACK_EN: 257 + sharingKeys.length + DNA_KEYS.length + B32_KEYS.length + currentSpeciesKeys.length + B49_ADDED_KEYS.length, FALLBACK_EN_REVIEW: 36, AI_REVIEWED: 59 });
  assert.equal(counts.FALLBACK_EN, 143 + postBaseline.size + potentialMovementKeys.length
    - B23_RETIRED_KEYS.length + B23_ADDED_KEYS.length + B24_ADDED_KEYS.length + zoneChartKeys.length
    + thermalPeriodKeys.length + probeAverageKeys.length + sharingKeys.length + DNA_KEYS.length + B32_KEYS.length
    + currentSpeciesKeys.length + B49_ADDED_KEYS.length);
});

test('B13 thermal register events are immutable, even under a coordinated catalog edit', () => {
  assert.deepEqual(b13IntegrityErrors(register), []);
  // Reported scenario: rewrite T856 (EN temperatureDailyMean) together with the EN catalog.
  const rewritten = register.map(row => ({ ...row }));
  const t856 = rewritten.find(row => row.entry_id === 'T856');
  assert.deepEqual([t856.key_or_msgid, t856.language, t856.final_value], ['temperatureDailyMean', 'EN', 'Daily mean']);
  Object.assign(t856, { initial_value: 'Daily average', final_value: 'Daily average' });
  // Without the B13 guard the reconstruction would accept it: removing the edited
  // line from an equally edited catalog still restores the frozen EN bytes.
  const edited = baselineCatalogSource('en')
    .replace("  temperatureDailyMean: 'Daily mean',\n", "  temperatureDailyMean: 'Daily average',\n");
  let baseline = beforeMovementLabelChange(edited, 'en');
  for (const row of rewritten.slice(B13_START, LAB_REFINEMENT_START + 32).filter(row => row.language === 'EN')) {
    baseline = baseline.replace(`  ${row.key_or_msgid}: '${row.final_value}',\n`, '');
  }
  assert.equal(sha256(baseline), '62989721d2528126ab48704fe783597dba0d484d2f24ce05a7391e0e9bdbfa27');
  assert.ok(b13IntegrityErrors(rewritten).includes('B13 history was modified'));
  assert.throws(() => postBaselineReactKeys(rewritten));

  for (let index = B13_START; index < B13_START + 14; index += 1) {
    for (const name of REGISTER_COLUMNS) {
      const rows = register.map(row => ({ ...row }));
      rows[index][name] += 'x';
      assert.ok(b13IntegrityErrors(rows).length, `${rows[index].entry_id}.${name}`);
    }
  }
  const b13 = register.slice(B13_START, B13_START + 14);
  const before = register.slice(0, B13_START);
  const later = { ...b13[13], entry_id: 'T859', batch: 'B14-2026-11-05' };
  for (const [label, rows] of Object.entries({
    deleted: [...before, ...b13.slice(1)],
    'inserted inside': [...before, b13[0], { ...b13[0], entry_id: 'T845b' }, ...b13.slice(1)],
    'row before B13': [...before, later, ...b13],
    replaced: [...before, ...b13.slice(0, 3), { ...b13[3], key_or_msgid: 'temperatureOther' }, ...b13.slice(4)],
    reordered: [...before, b13[1], b13[0], ...b13.slice(2)],
  })) assert.ok(b13IntegrityErrors(rows).length, label);
  // A later correction is a new appended event and leaves B13 intact.
  const correction = { ...b13[11], entry_id: 'T1007', batch: 'B18-2026-11-05', initial_value: 'Daily mean',
    final_value: 'Daily average', status: 'APPLIED', baseline_git_head: '2'.repeat(40) };
  assert.deepEqual(b13IntegrityErrors([...register, correction]), []);
  assert.deepEqual(postBaselineReactKeys([...register, correction]), postBaseline);
});

test('lab provenance rejects omissions, field edits, fake approvals and baseline extensions', () => {
  assert.deepEqual(labIntegrityErrors(register), []);
  assert.deepEqual(register.slice(LAB_START, LAB_START + 28).reduce((counts, row) =>
    ({ ...counts, [row.status]: (counts[row.status] ?? 0) + 1 }), {}), { DEFERRED: 26, ALREADY_MATCHING: 2 });
  for (let index = LAB_START; index < LAB_START + 28; index += 1) {
    for (const field of REGISTER_COLUMNS) {
      const changed = register.map(row => ({ ...row }));
      changed[index][field] += 'x';
      assert.ok(labIntegrityErrors(changed).length, `${register[index].entry_id}.${field}`);
      assert.throws(() => postBaselineReactKeys(changed));
    }
  }
  const approved = register.map(row => ({ ...row }));
  approved[LAB_START].status = 'ALREADY_MATCHING';
  for (const rows of [
    register.slice(0, LAB_START),
    [...register.slice(0, LAB_START), ...register.slice(LAB_START + 1)],
    [...register.slice(0, LAB_START), register[859], register[858], ...register.slice(860)],
    [...register, { ...register[858] }], approved,
    [...register, { ...register[858], entry_id: 'T1007', batch: 'B18-2026-11-05' }],
    [...register, { ...register[858], entry_id: 'T1007', baseline_git_head: '2'.repeat(40) }],
  ]) assert.throws(() => postBaselineReactKeys(rows));
});

test('historical register events and full schemas cannot redefine the reconstruction', () => {
  for (let index = HISTORICAL_START; index < HISTORICAL_START + 88; index += 1) {
    for (const field of REGISTER_COLUMNS) {
      const changed = register.map(row => ({ ...row }));
      changed[index][field] += 'x';
      assert.throws(() => postBaselineReactKeys(changed), `${register[index].entry_id}.${field}`);
    }
  }
  for (const rows of [
    register.slice(0, HISTORICAL_START),
    [...register.slice(0, 887), ...register.slice(888)],
    [...register.slice(0, 886), register[887], register[886], ...register.slice(888)],
    [...register, { ...register[973] }],
  ]) assert.throws(() => postBaselineReactKeys(rows));
  for (const index of [0, B13_START, LAB_START, HISTORICAL_START, LAB_REFINEMENT_START]) {
    const extra = register.map(row => ({ ...row }));
    extra[index].unknown_column = 'unapproved';
    assert.throws(() => postBaselineReactKeys(extra));
    const missing = register.map(row => ({ ...row }));
    delete missing[index].sheet_value;
    assert.throws(() => postBaselineReactKeys(missing));
  }
});

test('lab refinement provenance rejects field edits, omissions, reordering and extensions', () => {
  for (let index = LAB_REFINEMENT_START; index < LAB_REFINEMENT_START + 32; index += 1) {
    for (const field of REGISTER_COLUMNS) {
      const changed = register.map(row => ({ ...row }));
      changed[index][field] += 'x';
      assert.throws(() => postBaselineReactKeys(changed), `${register[index].entry_id}.${field}`);
    }
  }
  for (const rows of [
    register.slice(0, LAB_REFINEMENT_START),
    [...register.slice(0, 975), ...register.slice(976)],
    [...register.slice(0, 974), register[975], register[974], ...register.slice(976)],
    [...register, { ...register[1005] }],
    [...register, { ...register[1005], entry_id: 'T1007' }],
  ]) assert.throws(() => postBaselineReactKeys(rows));
});

test('registered post-baseline keys fall back to English without any Japanese', () => {
  assert.equal(postBaseline.size, 81);
  const react = new Map(manifest.filter(row => row.surface === 'react').map(row => [row.identity, row]));
  const translate = loadI18n().createTranslator('ja');
  for (const [key, values] of postBaseline) {
    if (B23_RETIRED_KEYS.includes(key)) {
      for (const catalog of [fr, en, ja]) assert.equal(Object.hasOwn(catalog, key), false, key);
      assert.equal(react.has(key), false, key);
      continue;
    }
    const current = key === 'historyAcceptedReconstruction'
      ? ['T1009', 'T1010'].map(id => register.find(entry => entry.entry_id === id).final_value)
      : [values.FR, values.EN];
    assert.deepEqual([fr[key], en[key]], current, key);
    const row = react.get(key);
    assert.deepEqual([row.current_fr, row.current_en], [values.FR, values.EN], key);
    assert.deepEqual([row.status, row.reason_code, row.sheet_row, row.ja_validated, row.ja_ai_reviewed],
      ['FALLBACK_EN', 'ABSENT_FROM_WORKBOOK', '', '', ''], key);
    assert.equal(Object.hasOwn(ja, key), false, key);
    assert.equal(translate(key), en[key], key);
  }

});

test('Species additions keep frozen source values and documentary English fallback only', () => {
  assert.equal(speciesIntegrationKeys.length, 266);
  assert.equal(new Set(speciesIntegrationKeys).size, 266);
  const translate = loadI18n().createTranslator('ja');
  const byKey = new Map(manifest.filter(row => row.surface === 'react').map(row => [row.identity, row]));
  for (const key of B49_REMOVED_KEYS) {
    assert.equal(byKey.has(key), false, key);
    assert.equal(Object.hasOwn(fr, key) || Object.hasOwn(en, key) || Object.hasOwn(ja, key), false, key);
  }
  for (const key of currentSpeciesKeys) {
    const row = byKey.get(key);
    const values = retired.has(key)
      ? register.filter(event => event.batch === adminRemoval.batch && event.key_or_msgid === key).map(event => event.initial_value)
      : [fr[key], en[key]];
    assert.deepEqual([row.current_fr, row.current_en], values, key);
    assert.deepEqual([row.status, row.reason_code, row.takeru_review], ['FALLBACK_EN', 'ABSENT_FROM_WORKBOOK', 'no'], key);
    for (const field of ['sheet_row', 'sheet_section', 'sheet_fr', 'sheet_en', 'ja_validated', 'ja_ai_reviewed']) assert.equal(row[field], '', `${key}.${field}`);
    assert.equal(Object.hasOwn(ja, key), false, key);
    if (!retired.has(key)) assert.equal(translate(key), en[key], key);
  }
  for (const language of ['fr', 'en']) {
    const source = baselineCatalogSource(language);
    const restored = beforeSpeciesIntegration(source, language);
    const extra = "  unknownSpeciesKey: 'Unapproved',\n";
    assert.equal(beforeSpeciesIntegration(source + extra, language), restored + extra);
    for (const line of speciesEvidence.catalog_additions[language].slice(0, 3)) {
      for (const replacement of ['', line + line, line.replace(": '", ": 'changed ")]) {
        assert.throws(() => beforeSpeciesIntegration(source.replace(line, replacement), language));
      }
    }
    for (const change of speciesEvidence.catalog_changes[language]) {
      assert.throws(() => beforeSpeciesIntegration(source.replace(change.after, change.before), language));
    }
  }
});

test('original review inputs retain their byte-exact SHA256 hashes', () => {
  for (const [path, digest] of [
    [reviewedPath, 'b82de81f74ce4e7171c5a54e948ee3b9b809f3dceac3de08a95d1406f830b4b7'],
    [blockedPath, 'b118377aa744faf4d04b4aced2436b347c20594d66dea66d0edf55c7c7ad8cd6'],
  ]) {
    assert.equal(sha256(readFileSync(new URL(path, import.meta.url))), digest, path);
  }
});

test('AI-reviewed runtime membership and provenance match exactly the 59 reviewed CSV rows', () => {
  assert.equal(reviewed.length, 59);
  assert.equal(new Set(reviewed.map(row => row.key)).size, 59, 'unique reviewed keys');
  const aiRows = manifest.filter(row => row.status === 'AI_REVIEWED');
  assert.deepEqual(aiRows.map(row => row.identity).sort(), reviewed.map(row => row.key).sort());
  const byKey = new Map(aiRows.map(row => [row.identity, row]));
  const translate = loadI18n().createTranslator('ja');
  for (const candidate of reviewed) {
    const key = candidate.key;
    const row = byKey.get(key);
    assert.equal(candidate.human_approved, 'NO', `${key}: AI review is not human approval`);
    assert.ok(['ACCEPT', 'REVISE'].includes(candidate.review_decision), key);
    assert.equal(candidate.fr_current, fr[key], `${key}: current FR source`);
    assert.equal(candidate.en_current, en[key], `${key}: current EN source`);
    assert.equal(row.surface, 'react', key);
    assert.equal(row.reason_code, `AI_${candidate.review_decision}`, key);
    assert.equal(row.current_fr, candidate.fr_current, key);
    assert.equal(row.current_en, candidate.en_current, key);
    assert.equal(row.ja_validated, '', `${key}: never promoted to human-validated Japanese`);
    assert.equal(row.ja_ai_reviewed, candidate.ja_reviewed_candidate, key);
    assert.ok(candidate.ja_reviewed_candidate.trim(), `${key}: non-empty reviewed value`);
    assert.equal(ja[key], candidate.ja_reviewed_candidate, `${key}: exact catalog value`);
    assert.equal(translate(key), candidate.ja_reviewed_candidate, `${key}: exact runtime value`);
    if (candidate.review_decision === 'ACCEPT') {
      assert.equal(candidate.ja_reviewed_candidate, candidate.ja_first_proposal, `${key}: accepted draft`);
    }
  }
});

test('all seven REVISE corrections are exact reviewed values, never the original drafts', () => {
  const corrections = {
    createBoxActiveBox: '個の有効な容器',
    createBoxActiveBoxes: '個の有効な容器',
    boxLifecycleQualifySubmit: '状態の設定を確定',
    temperatureCombinedSources: '過去データのインポートおよびPolypbase',
    temperatureHistoricalSource: '過去データのインポート：',
    probeSilentBadge: '測定値未受信',
    probeAggregateAverage: '集計対象の測定値の平均',
  };
  const revised = reviewed.filter(row => row.review_decision === 'REVISE');
  assert.equal(revised.length, 7);
  assert.deepEqual(revised.map(row => row.key).sort(), Object.keys(corrections).sort());
  const translate = loadI18n().createTranslator('ja');
  for (const row of revised) {
    const expected = corrections[row.key];
    assert.equal(row.ja_reviewed_candidate, expected, row.key);
    assert.equal(ja[row.key], expected, row.key);
    assert.equal(translate(row.key), expected, row.key);
    assert.notEqual(row.ja_first_proposal, expected, `${row.key}: correction differs from draft`);
    assert.notEqual(translate(row.key), row.ja_first_proposal, `${row.key}: draft must not ship`);
  }
});

test('all six BLOCK rows remain absent from Japanese and use English at runtime', () => {
  assert.equal(blocked.length, 6);
  assert.equal(new Set(blocked.map(row => row.key)).size, 6, 'unique blocked keys');
  assert.deepEqual(blocked.map(row => row.key).sort(), [
    'createBoxConnectionPending', 'createBoxRecoveryUnavailable', 'createBoxNotCreated',
    'probeSilentDescription', 'probeSilentNoReadingDescription', 'probeNoCollectedProbe',
  ].sort());
  const react = new Map(manifest.filter(row => row.surface === 'react').map(row => [row.identity, row]));
  const reviewedKeys = new Set(reviewed.map(row => row.key));
  const translate = loadI18n().createTranslator('ja');
  for (const candidate of blocked) {
    const key = candidate.key;
    assert.equal(candidate.review_decision, 'BLOCK', key);
    assert.equal(candidate.human_approved, 'NO', key);
    assert.equal(candidate.ja_reviewed_candidate, '', key);
    assert.equal(candidate.fr_current, fr[key], key);
    assert.equal(candidate.en_current, en[key], key);
    assert.equal(reviewedKeys.has(key), false, `${key}: blocked and reviewed sets are disjoint`);
    assert.equal(react.get(key)?.status, 'FALLBACK_EN', key);
    assert.equal(react.get(key)?.ja_validated, '', key);
    assert.equal(react.get(key)?.ja_ai_reviewed, '', key);
    assert.equal(Object.hasOwn(ja, key), false, `${key}: rejected draft must not ship`);
    assert.equal(translate(key), en[key], `${key}: current English fallback`);
    assert.notEqual(translate(key), candidate.ja_first_proposal, key);
  }
});

test('historical human translations are immutable and separate from AI-reviewed values', () => {
  const historical = manifest.filter(row => row.surface === 'react' && row.status === 'REUSED');
  const byKey = new Map(historical.map(row => [row.identity, row.ja_validated]));
  const ordered = baselineFrKeys.filter(key => byKey.has(key)).map(key => [key, byKey.get(key)]);
  assert.equal(ordered.length, 930);
  assert.equal(sha256(JSON.stringify(ordered)),
    'ab1705b630daaf621bcf152b1af3e6a7cdb098d342c5282fc2ec79ccb2330939');
  for (const row of manifest) {
    assert.ok(Object.hasOwn(row, 'ja_ai_reviewed'), 'manifest has a separate AI-reviewed column');
    if (row.status !== 'AI_REVIEWED') assert.equal(row.ja_ai_reviewed, '', row.identity);
  }
});

// Additions only: thermal control, Laboratory configuration, historical movements
// and the Laboratory inline-actions refinement. The original source hashes stay frozen.
const LATER_FALLBACK_KEYS = [
  'temperatureRecentMean', 'temperatureNoRecent', 'temperatureRecentPeriod', 'temperatureRecentError',
  'temperatureDailyPeriod', 'temperatureDailyMean', 'temperatureDailyGap',
  'adminLabLocationsTitle', 'adminLabAddLocation', 'adminLabEditLocation', 'adminLabEditTitle',
  'adminLabActiveBoxes', 'adminLabMaxCapacity', 'adminLabTargetTemperatureField', 'adminLabReferenceSalinity',
  'adminLabInactive',
  'adminLabNoLocations', 'adminLabSaved', 'adminLabCapacityInvalid', 'adminLabTemperatureInvalid',
  'adminLabSalinityInvalid',
  ...historicalMovementKeys,
  'adminLabDeleteLocation', 'adminLabDeleteTitle', 'adminLabDeleteNoBoxes',
  'adminLabDeleteOneBox', 'adminLabDeleteBoxCount',
  'adminLabDeleteChecking', 'adminLabDeleteCheckError', 'adminLabDeleteNeverUsed',
  'adminLabDeleteOccupied', 'adminLabDeleteHistory', 'adminLabDeleteChanged',
  'adminLabDeleting', 'adminLabDeleted', 'auditActionDeletion',
  'auditDescriptionZoneDeleted', 'auditSummaryLocationDeleted',
];

const B23_RETIRED_KEYS = ['historyEnvironmentOnly', 'historyTransitionReview', 'historyProvenance', 'historyReconciliation'];
const B23_ADDED_KEYS = ['historyRecordedViaPolypbase', 'historyStartingLocation'];
const B24_ADDED_KEYS = ['historyReactivation', 'historyLocationExit', 'historyLocationEntry'];

const B26_ROWS_DIGEST = 'bb061a0b21466b02a2db8ab09453d98c3571c607c7236d28678f0aaa68080955';
const B27_ROWS_DIGEST = '988d1d95a042150c7c58f1e55e88c687e864b20b41623af9e69e50ebcea7a8ef';
// Feature T1402-T1471/B30-B31 is appended as T1742-T1811/B44-B45.
// Preserve the original feature digests by projecting only the reallocated fields.
function donationOriginalProjection(rows) {
  return rows.map(row => ({ ...row,
    entry_id: `T${Number(row.entry_id.slice(1)) - 340}`,
    batch: `B${Number(row.batch.slice(1, 3)) - 14}${row.batch.slice(3)}` }));
}

const B44_ADDED_KEYS = [
  'donationIncoming', 'donationSend', 'donationRefresh', 'donationDestination', 'donationSource',
  'donationType', 'donationTypeDon', 'donationDate', 'donationState', 'donationPending',
  'donationCancelled', 'donationCancelledAt', 'donationAllTypes', 'donationAllStates', 'donationSearch',
  'donationDetails', 'donationReference', 'donationEmpty', 'donationNoMatch', 'donationForbidden',
  'donationNotFound', 'donationReadError', 'donationSendUnavailable', 'donationAcceptanceUnavailable',
];
// Freeze all 14 columns, including values, decision, baseline and the unused refresh exception.
const B44_ROWS_DIGEST = 'cd8e6091a166c53d3a746e0f129d2ef2fc413a468892b854a60a15db80fe6469';

function b44Events(rows = register) {
  const events = rows.filter(row => row.batch === 'B44-2026-11-18');
  assert.deepEqual(events, rows.slice(1741, 1789));
  assert.deepEqual(events.map(row => row.entry_id), Array.from({ length: 48 }, (_, index) => `T${1742 + index}`));
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language]),
    B44_ADDED_KEYS.flatMap(key => [[key, 'FR'], [key, 'EN']]));
  for (const entry of events) {
    assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
    assert.deepEqual([entry.surface, entry.source_file, entry.status, entry.initial_value],
      ['react', `frontend/src/i18n/${entry.language.toLowerCase()}.ts`, 'ALREADY_MATCHING', entry.final_value]);
  }
  assert.equal(registerRowsDigest(donationOriginalProjection(events)), B44_ROWS_DIGEST);
  return events;
}

const B45_ADDED_KEYS = [
  'exchangePreviewTitle', 'exchangePreviewNotice', 'exchangePreviewType', 'exchangePreviewBox',
  'exchangePreviewInstitution', 'exchangePreviewBoxPlaceholder', 'exchangePreviewInstitutionPlaceholder',
  'exchangePreviewLive', 'exchangePreviewFrozen', 'exchangePreviewEmail',
];
const B45_ROWS_DIGEST = 'ce4c9f91ebdd92c896516483d152efde1959fcd727dd1ad902bc2cf1b83193fc';

function b45Events(rows = register) {
  const ids = Array.from({ length: 22 }, (_, index) => `T${1790 + index}`);
  const events = rows.filter(row => row.batch === 'B45-2026-11-18' || ids.includes(row.entry_id));
  assert.deepEqual(events, rows.slice(1789, 1811));
  assert.deepEqual(events.map(row => row.entry_id), ids);
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language]),
    ['donationSendUnavailable', ...B45_ADDED_KEYS].flatMap(key => [[key, 'FR'], [key, 'EN']]));
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.equal(registerRowsDigest(donationOriginalProjection(events)), B45_ROWS_DIGEST);
  return events;
}

const B47_BATCH = 'B47-2026-11-18';
const B47_START = 1812; // T1813 follows the B46 Django event T1812.
const B47_ENTRY_IDS = Array.from({ length: 36 }, (_, index) => `T${1813 + index}`);
const B47_ADDED_KEYS = [
  'donationCountryUnspecified', 'donationChoicesError', 'donationHistoryChanged', 'donationAlreadyPending',
  'donationDestinationUnavailable', 'donationBoxIneligible', 'donationPreflightError', 'donationSendError',
  'donationSendUncertain', 'donationOnlyExchange', 'donationChooseBox', 'donationChooseDestination',
  'donationNoDestination', 'donationOpenHistory', 'donationConfirmHistory', 'donationSubmitProposal',
  'donationCheckEligibility', 'donationProposalSent',
];
const B47_ROWS_DIGEST = '58491e3b80254b31ea00c2105d4738dbdb01ee57b5f4e4b494853482c614b5be';

function b47Events(rows = register) {
  const ids = new Set(B47_ENTRY_IDS);
  const events = rows.filter(row => row.batch === B47_BATCH || ids.has(row.entry_id));
  assert.deepEqual(events, rows.slice(B47_START, B47_START + B47_ENTRY_IDS.length));
  assert.deepEqual(events.map(row => row.entry_id), B47_ENTRY_IDS);
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language]),
    B47_ADDED_KEYS.flatMap(key => [[key, 'FR'], [key, 'EN']]));
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.equal(registerRowsDigest(events), B47_ROWS_DIGEST);
  return events;
}

function beforeB47(source, language, rows = register) {
  const block = b47Events(rows).filter(row => row.language === language.toUpperCase())
    .map(row => `  ${row.key_or_msgid}: '${row.final_value}',\n`).join('');
  const parts = source.split(block);
  assert.equal(parts.length, 2, `${language}: one exact B47 block`);
  // B47 inserted its rows after the B44 warning, not after its separator.
  assert.match(parts[0], /\n  donationAcceptanceUnavailable: [^\n]+\n$/);
  return parts.join('');
}

test('B49 form events reconstruct the exact main catalogs before B47 is reversed', () => {
  const events = b49Events();
  for (const [language, digest] of [
    ['fr', 'a98d5640e17baebbd122f06998487ae2bd05cf40f423fb3d805c0a20d6266ea4'],
    ['en', 'da1b9f55106992e86b32eba66eea317797ead064ba933a7ed452dff670584153'],
  ]) {
    const source = baselineCatalogSource(language);
    assert.equal(sha256(beforeB49(source, language)), digest, language);
  }
  for (const entry of events) {
    assert.throws(() => b49Events(register.filter(row => row !== entry)), entry.entry_id);
    for (const field of REGISTER_COLUMNS) {
      assert.throws(() => b49Events(register.map(row => row === entry
        ? { ...row, [field]: `${row[field]} changed` } : row)), `${entry.entry_id}.${field}`);
    }
  }
});

test('B47 send form events reconstruct the pre-B47 catalogs and use English without Japanese provenance', () => {
  const events = b47Events();
  for (const entry of events) {
    assert.throws(() => b47Events(register.filter(row => row !== entry)), `${entry.entry_id}: omitted`);
    for (const field of [...REGISTER_COLUMNS, 'unknown_column']) {
      assert.throws(() => b47Events(register.map(row => row === entry
        ? { ...row, [field]: `${row[field] ?? ''} changed` } : row)), `${entry.entry_id}.${field}`);
    }
    const missing = { ...entry };
    delete missing.sheet_value;
    assert.throws(() => b47Events(register.map(row => row === entry ? missing : row)));
  }
  const reordered = register.slice();
  [reordered[B47_START], reordered[B47_START + 1]] = [reordered[B47_START + 1], reordered[B47_START]];
  for (const rows of [reordered, [...register, { ...events[0], entry_id: 'T1863' }],
    [...register, { ...events[0], batch: 'later' }]]) assert.throws(() => b47Events(rows));

  // The FR/EN catalog bytes at B47's registered baseline, cab5dde.
  for (const [language, catalog, digest] of [
    ['fr', fr, '173c15469c7419d872bcbec8a6f737eb7543dc9a05eb1288cb7e29e85296dcb6'],
    ['en', en, 'ddb203170962693876577c64e9238daabe81ef90515b6de5b35cdbac095e44d4'],
  ]) {
    const current = baselineCatalogSource(language);
    const source = beforeB48(beforeB49(current, language), language);
    const restored = beforeB47(source, language);
    assert.equal(sha256(restored), digest, language);
    const extra = "  donationUnregistered: 'Unapproved',\n";
    assert.equal(beforeB47(source + extra, language), restored + extra);
    for (const entry of events.filter(row => row.language === language.toUpperCase())) {
      assert.equal(catalog[entry.key_or_msgid], entry.final_value, entry.entry_id);
      const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
      const changed = line.replace(": '", ": 'changed ");
      for (const replacement of ['', changed]) {
        assert.throws(() => beforeB47(source.replace(line, replacement), language), entry.entry_id);
      }
      // A duplicate can leave the exact block intact, but it must survive reversal and change the hash.
      assert.notEqual(sha256(beforeB47(source + line, language)), digest, entry.entry_id);
      const coordinated = register.map(row => row === entry
        ? { ...row, initial_value: `changed ${row.initial_value}`, final_value: `changed ${row.final_value}` } : row);
      assert.throws(() => beforeB47(source.replace(line, changed), language, coordinated), entry.entry_id);
    }
  }
  const translate = loadI18n().createTranslator('ja');
  for (const key of B47_ADDED_KEYS) {
    assert.equal(translate(key), en[key], `${key}: English runtime fallback`);
    assert.equal(Object.hasOwn(ja, key), false, key);
    assert.equal(postBaseline.has(key), false, key);
    assert.equal(manifest.some(row => row.identity === key), false, key);
    assert.equal([...reviewed, ...blocked].some(row => row.identity === key || row.key === key), false, key);
  }
});

function beforeB45(source, language, rows = register) {
  const events = b45Events(rows).filter(row => row.language === language.toUpperCase());
  for (const entry of events.filter(row => row.status === 'ALREADY_MATCHING')) {
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, entry.entry_id);
    source = source.replace(line, '');
  }
  const removed = events.find(row => row.status === 'REMOVED');
  assert.equal(source.split('\n').some(line => line.startsWith(`  ${removed.key_or_msgid}: `)), false);
  const anchor = '  donationAcceptanceUnavailable: ';
  assert.equal(source.split(anchor).length - 1, 1);
  return source.replace(anchor, `  ${removed.key_or_msgid}: '${removed.initial_value}',\n${anchor}`);
}

test('B45 preview events reconstruct exact HEAD catalogs and reject history or source mutations', () => {
  const events = b45Events();
  for (const entry of events) {
    assert.throws(() => b45Events(register.filter(row => row !== entry)));
    for (const field of [...REGISTER_COLUMNS, 'unknown_column']) {
      assert.throws(() => b45Events(register.map(row => row === entry
        ? { ...row, [field]: `${row[field] ?? ''} changed` } : row)), `${entry.entry_id}.${field}`);
    }
    const missing = { ...entry };
    delete missing.sheet_value;
    assert.throws(() => b45Events(register.map(row => row === entry ? missing : row)));
  }
  const reordered = register.slice();
  [reordered[1789], reordered[1790]] = [reordered[1790], reordered[1789]];
  for (const rows of [reordered, [...register, { ...events[0], entry_id: 'T1812' }],
    [{ ...events[0], batch: 'earlier' }, ...register]]) assert.throws(() => b45Events(rows));
  assert.deepEqual(b45Events([...register, { ...events[2], entry_id: 'T1812', batch: 'later' }]), events);
  const translate = loadI18n().createTranslator('ja');
  for (const [language, digest] of [
    ['fr', 'b987f50afd2504ac6b3de347ccf3a5bca4aa4abfbe8e6a2f80dc040c7e5cfb22'],
    ['en', 'f51265056ec7e3f84057b4e8405410292465240beb720bbc2e5d4e01ebf25a77'],
  ]) {
    const source = beforeB47(beforeB48(baselineCatalogSource(language), language), language);
    const layout = beforeDonationIntegrationLayout(source, language);
    assert.equal(source.split('\n').length - layout.split('\n').length, 3);
    for (const key of ['dnaMissing', 'exchangePreviewTitle', 'shareWorkspace']) {
      const line = `${source.split('\n').find(line => line.startsWith(`  ${key}: `))}\n`;
      assert.throws(() => beforeDonationIntegrationLayout(source.replace(`\n${line}`, line), language), key);
    }
    // Unrelated whitespace must remain visible to the historical fingerprint.
    const unknownSpace = '\n  shareWorkspaceHelp: ';
    const changedSpace = source.replace(unknownSpace, `\n${unknownSpace}`);
    assert.notEqual(changedSpace, source);
    assert.equal(beforeDonationIntegrationLayout(changedSpace, language), layout.replace(unknownSpace, `\n${unknownSpace}`));
    const restored = beforeB45(source, language);
    // Reverse main-only layers before comparing with the original feature B30 source.
    const featureSource = beforeB32(beforeB30(restored, language), language);
    assert.equal(sha256(featureSource), digest);
    const extra = "  unregisteredKey: 'Unapproved',\n";
    assert.equal(beforeB45(source + extra, language), restored + extra);
    for (const entry of events.filter(row => row.language === language.toUpperCase())) {
      const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
      if (entry.status === 'REMOVED') {
        assert.throws(() => beforeB45(source + `  ${entry.key_or_msgid}: '${entry.initial_value}',\n`, language));
        continue;
      }
      for (const replacement of ['', line + line, line.replace(": '", ": 'changed ")]) {
        assert.throws(() => beforeB45(source.replace(line, replacement), language));
      }
      const coordinated = register.map(row => row === entry
        ? { ...row, initial_value: `changed ${row.initial_value}`, final_value: `changed ${row.final_value}` } : row);
      assert.throws(() => beforeB45(source.replace(line, line.replace(": '", ": 'changed ")), language, coordinated));
      assert.equal(translate(entry.key_or_msgid), en[entry.key_or_msgid]);
      assert.equal(Object.hasOwn(ja, entry.key_or_msgid), false);
      assert.equal(manifest.some(row => row.identity === entry.key_or_msgid), false);
      assert.equal(reviewed.some(row => row.identity === entry.key_or_msgid || row.key === entry.key_or_msgid), false);
      assert.equal(postBaseline.has(entry.key_or_msgid), false);
    }
  }
});

function beforeB44(source, language, rows = register) {
  for (const entry of b44Events(rows).filter(row => row.language === language.toUpperCase())) {
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, entry.key_or_msgid);
    source = source.replace(line, '');
  }
  return source;
}

test('B44 donation additions require exact append-only events, schema, metadata and source values', () => {
  const events = b44Events();
  for (const entry of events) {
    assert.throws(() => b44Events(register.filter(row => row !== entry)), `${entry.entry_id}: omitted`);
    for (const field of REGISTER_COLUMNS) {
      const changed = register.map(row => row === entry ? { ...row, [field]: `${row[field]} changed` } : row);
      assert.throws(() => b44Events(changed), `${entry.entry_id}.${field}`);
    }
    for (const field of ['unknown_column', 'sheet_value']) {
      const changed = { ...entry };
      if (field === 'unknown_column') changed[field] = 'unapproved';
      else delete changed[field];
      assert.throws(() => b44Events(register.map(row => row === entry ? changed : row)));
    }
  }
  const reordered = register.slice();
  [reordered[1741], reordered[1742]] = [reordered[1742], reordered[1741]];
  assert.throws(() => b44Events(reordered));
  assert.throws(() => b44Events([...register, { ...events[0], entry_id: 'T1812' }]));
  assert.throws(() => b44Events([{ ...events[0], batch: 'earlier' }, ...register]));
  const correction = { ...events[0], entry_id: 'T1812', batch: 'later', status: 'APPLIED', final_value: 'Later correction' };
  assert.deepEqual(b44Events([...register, correction]), events, 'later events do not rewrite B44');

  for (const language of ['fr', 'en']) {
    const source = baselineCatalogSource(language);
    const b44Source = beforeB45(beforeB47(beforeB48(source, language), language), language);
    assert.doesNotThrow(() => beforeB44(b44Source, language));
    for (const entry of events.filter(row => row.language === language.toUpperCase())) {
      const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
      for (const replacement of ['', line + line, line.replace(": '", ": 'changed ")]) {
        assert.throws(() => beforeB44(b44Source.replace(line, replacement), language));
      }
      const coordinated = register.map(row => row === entry
        ? { ...row, initial_value: `changed ${row.initial_value}`, final_value: `changed ${row.final_value}` } : row);
      assert.throws(() => beforeB44(b44Source.replace(line, line.replace(": '", ": 'changed ")), language, coordinated));
    }
    const digest = sha256(historicalSource(source, true, language));
    for (const key of ['donationUnapproved', 'unapprovedAddition']) {
      const unknown = source.replace('\n}', `\n  ${key}: 'Value',\n}`);
      assert.notEqual(sha256(historicalSource(unknown, true, language)), digest, key);
    }
    const line = source.split('\n').find(line => line.startsWith('  overviewEmpty: '));
    assert.ok(line);
    const changed = source.replace(line, line.replace(": '", ": 'changed "));
    assert.notEqual(sha256(historicalSource(changed, true, language)), digest, 'historical values remain frozen');
  }
});

test('B44 donation keys use English runtime fallback without Japanese approval or fabricated provenance', () => {
  const translate = loadI18n().createTranslator('ja');
  for (const [language, catalog] of [['FR', fr], ['EN', en]]) {
    assert.deepEqual(Object.keys(catalog).filter(key => /^donation/.test(key)),
      [...B44_ADDED_KEYS.filter(key => key !== 'donationSendUnavailable'), ...B47_ADDED_KEYS]);
    for (const entry of b44Events().filter(row => row.language === language)) {
      if (entry.key_or_msgid === 'donationSendUnavailable') assert.equal(Object.hasOwn(catalog, entry.key_or_msgid), false);
      else assert.equal(catalog[entry.key_or_msgid], entry.final_value, entry.entry_id);
    }
  }
  for (const key of B44_ADDED_KEYS.filter(key => key !== 'donationSendUnavailable')) {
    assert.equal(Object.hasOwn(ja, key), false, key);
    assert.equal(postBaseline.has(key), false, key);
    assert.equal(manifest.some(row => row.identity === key), false, `${key}: no invented manifest row`);
    assert.equal(reviewed.some(row => row.identity === key || row.key === key), false, key);
    assert.equal(translate(key), en[key], `${key}: English runtime fallback`);
  }
});

function b24Events(rows = register) {
  const events = rows.filter(row => row.batch === 'B24-2026-11-10');
  assert.deepEqual(events, rows.slice(1054, 1060));
  assert.deepEqual(events.map(row => row.entry_id), Array.from({ length: 6 }, (_, i) => `T${1055 + i}`));
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.equal(registerRowsDigest(events), '0f7e33f3574c1c177669ecadf5a708b66b2c2a789b0c53c72f71eced5819864c');
  return events;
}

function b25Events(rows = register) {
  const events = rows.filter(row => row.batch === 'B25-2026-11-10');
  assert.deepEqual(events, rows.slice(1060, 1086));
  assert.deepEqual(events.map(row => row.entry_id), Array.from({ length: 26 }, (_, i) => `T${1061 + i}`));
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  // Incoming b8237be thermal rows: only entry IDs and batch are projected after main history.
  assert.equal(registerRowsDigest(events), '0d5243d50bebfc735a5627c768e4973d426b97843038b0e71ca80c98ab92e241');
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language]),
    zoneChartKeys.flatMap(key => [[key, 'FR'], [key, 'EN']]));
  return events;
}

function b26Events(rows = register) {
  const events = rows.filter(row => row.batch === 'B26-2026-11-10');
  assert.deepEqual(events, rows.slice(1086, 1094));
  assert.deepEqual(events.map(row => row.entry_id), Array.from({ length: 8 }, (_, i) => `T${1087 + i}`));
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.equal(registerRowsDigest(events), B26_ROWS_DIGEST);
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language]),
    thermalPeriodKeys.flatMap(key => [[key, 'FR'], [key, 'EN']]));
  return events;
}

function beforeB26(source, language, rows = register) {
  for (const entry of b26Events(rows).filter(row => row.language === language.toUpperCase())) {
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',
`;
    assert.equal(source.split(line).length - 1, 1, entry.key_or_msgid);
    source = source.replace(line, '');
  }
  return source;
}

function b27Events(rows = register) {
  const events = rows.filter(row => row.batch === 'B27-2026-11-11');
  assert.deepEqual(events, rows.slice(1094, 1098));
  assert.deepEqual(events.map(row => row.entry_id), Array.from({ length: 4 }, (_, i) => `T${1095 + i}`));
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.equal(registerRowsDigest(events), B27_ROWS_DIGEST);
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language]),
    probeAverageKeys.flatMap(key => [[key, 'FR'], [key, 'EN']]));
  return events;
}

// B28: legend header reworded in place (APPLIED), restored to its B25 value before older batches.
function b28Events(rows = register) {
  const events = rows.filter(row => row.batch === 'B28-2026-11-11');
  assert.deepEqual(events, rows.slice(1098, 1100));
  assert.deepEqual(events.map(row => row.entry_id), ['T1099', 'T1100']);
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language, row.status]),
    [['probeLatestInterval', 'FR', 'APPLIED'], ['probeLatestInterval', 'EN', 'APPLIED']]);
  return events;
}

const DEMO_BANNER_KEY = 'demoModeBanner';
// Demo events from 6a0e9d9 after changing only their batch and entry IDs.
const B48_ROWS_DIGEST = '56334a053e10d59c5cd09944c2a0da46de55bb416fa52739ff2fdd6926e43865';

function b48Events(rows = register) {
  const ids = ['T1849', 'T1850'];
  const events = rows.filter(row => row.batch === 'B48-2026-11-18' || ids.includes(row.entry_id));
  assert.deepEqual(events, rows.slice(1848, 1850));
  assert.deepEqual(events.map(row => [row.entry_id, row.language, row.final_value]), [
    ['T1849', 'FR', 'Mode démonstration'], ['T1850', 'EN', 'Demonstration mode'],
  ]);
  for (const row of events) {
    assert.deepEqual(Object.keys(row), REGISTER_COLUMNS);
    assert.deepEqual([row.batch, row.surface, row.key_or_msgid, row.source_file, row.status, row.initial_value],
      ['B48-2026-11-18', 'react', DEMO_BANNER_KEY, `frontend/src/i18n/${row.language.toLowerCase()}.ts`, 'ALREADY_MATCHING', row.final_value]);
  }
  assert.equal(registerRowsDigest(events), B48_ROWS_DIGEST);
  return events;
}

function beforeB48(source, language, rows = register) {
  const entry = b48Events(rows).find(row => row.language === language.toUpperCase());
  assert.equal(source.match(/^[ \t]*(?:demoModeBanner|['"]demoModeBanner['"])[ \t]*:/gm)?.length, 1,
    'demoModeBanner: exactly one entry');
  const line = `  demoModeBanner: '${entry.final_value}',\n`;
  assert.equal(source.split(line).length - 1, 1, `${entry.entry_id}: exact registered line`);
  return source.replace(line, '');
}

test('B48 preserves register values and allows only the exact demo banner addition', () => {
  const translate = loadI18n().createTranslator('ja');
  const events = b48Events();
  assert.throws(() => b48Events([...register.slice(0, 1848), events[1], events[0], ...register.slice(1850)]));
  // Exact catalog bytes from main (671e1ba), before integrating the demo banner.
  const mainDigests = {
    fr: '3f17dfa4efcab9d687012909ceb35f13a0cf611714e1b1bbf63469c6b38e8af0',
    en: 'ec84984264a6f7eec043d05ae4b72e14e97b78112e81ca29f32aa545fdbac750',
  };
  for (const entry of events) {
    const language = entry.language.toLowerCase();
    const source = beforeB49(baselineCatalogSource(language), language);
    const line = `  demoModeBanner: '${entry.final_value}',\n`;
    const altered = line.replace(": '", ": 'changed ");
    const restored = beforeB48(source, language);
    assert.equal(restored, source.replace(line, ''));
    assert.equal(sha256(restored), mainDigests[language]);
    for (const replacement of ['', line + line, line + altered, altered]) {
      assert.throws(() => beforeB48(source.replace(line, replacement), language));
    }
    assert.throws(() => beforeB48(source, language, register.filter(row => row !== entry)));
    assert.throws(() => beforeB48(source, language, [...register, entry]));
    const changed = `changed ${entry.final_value}`;
    const coordinated = register.map(row => row === entry
      ? { ...row, initial_value: changed, final_value: changed } : row);
    assert.throws(() => beforeB48(source.replace(line, altered), language, coordinated));
  }
  assert.equal(Object.hasOwn(ja, DEMO_BANNER_KEY), false);
  assert.equal(postBaseline.has(DEMO_BANNER_KEY), false);
  assert.equal(manifest.some(row => row.identity === DEMO_BANNER_KEY), false);
  assert.equal([...reviewed, ...blocked].some(row => row.identity === DEMO_BANNER_KEY || row.key === DEMO_BANNER_KEY), false);
  assert.equal(translate(DEMO_BANNER_KEY), en[DEMO_BANNER_KEY]);
});

function beforeB29(source, language, rows = register) {
  source = beforeB44(beforeB45(source, language, rows), language, rows);
  const batch = rows.filter(row => row.batch === 'B29-2026-11-12');
  const rename = batch.find(entry => entry.key_or_msgid === 'adminTabTransfers');
  assert.deepEqual([rename.entry_id, rename.key_or_msgid, rename.language, rename.status, rename.initial_value, rename.final_value],
    ['T1399', 'adminTabTransfers', 'EN', 'APPLIED', 'Shipments and receipts', 'Sending and receiving']);
  if (language === 'en') {
    const line = `  adminTabTransfers: '${rename.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1);
    source = source.replace(line, `  adminTabTransfers: '${rename.initial_value}',\n`);
  }
  const events = batch.filter(entry => entry !== rename);
  assert.equal(sharingKeys.length, 150);
  assert.deepEqual(events.map(row => row.entry_id), [...Array.from({ length: 298 }, (_, index) => `T${1101 + index}`), 'T1400', 'T1401']);
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language]), sharingKeys.flatMap(key => [[key, 'FR'], [key, 'EN']]));
  for (const entry of events) {
    assert.deepEqual([entry.surface, entry.status, entry.initial_value], ['react', 'ALREADY_MATCHING', entry.final_value]);
    assert.equal(entry.baseline_git_head, '380235543d7be7d715b5de2b81fade1f38e90936');
    assert.equal(Object.hasOwn(ja, entry.key_or_msgid), false);
    if (entry.language !== language.toUpperCase()) continue;
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, entry.key_or_msgid);
    source = source.replace(line, '');
  }
  return source;
}

function beforeB28(source, language, rows = register) {
  source = beforeB29(source, language, rows);
  for (const entry of b28Events(rows).filter(row => row.language === language.toUpperCase())) {
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',
`;
    assert.equal(source.split(line).length - 1, 1, entry.key_or_msgid);
    source = source.replace(line, `  ${entry.key_or_msgid}: '${entry.initial_value}',
`);
  }
  return source;
}

function beforeB27(source, language, rows = register) {
  for (const entry of b27Events(rows).filter(row => row.language === language.toUpperCase())) {
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, entry.key_or_msgid);
    source = source.replace(line, '');
  }
  return source;
}

// B30: DNA evidence badge copy, English fallbacks without Japanese.
// B31: records the removal of dnaStateValidated in FR and EN.
// Same canonical digests as B30_ROWS_SHA256 / B31_ROWS_SHA256 in
// docs/translation-review/application/test_check_register.py, computed from the
// committed register blob at 630302f, not from the current working tree.
const B30 = 'B30-2026-11-16';
const B31 = 'B31-2026-11-16';
const B30_ROWS_DIGEST = '076b5eb663c1824c81bab89c3af034ca6a65286f6982b4206161aa370cfe2e6f';
const B31_ROWS_DIGEST = 'c702ed9c9f5450b11066edef261c4ea045be866bbd0ff6aabb01c973e3af1a57';
const DNA_KEYS = ['dnaMissing', 'dnaValidated', 'dnaTitle', 'dnaAdd', 'dnaSave', 'dnaEdit', 'dnaRevoke',
  'dnaRevokeMessage', 'dnaEvidenceLabel', 'dnaEvidenceUrl', 'dnaAddedByLabel', 'dnaDateLabel', 'dnaInvalidUrl',
  'dnaConflict', 'dnaReload', 'dnaReloadError', 'dnaSaveError', 'dnaForbidden', 'dnaAuditAdded',
  'dnaAuditUpdated', 'dnaAuditRevoked'];

// B32: four-hour thermal monitoring labels, English fallbacks without Japanese.
// Same canonical digest as B32_ROWS_SHA256 in
// docs/translation-review/application/test_check_register.py.
const B32 = 'B32-2026-11-17';
const B32_ROWS_DIGEST = '6d7a1eaf48f628124aba324eea02ed971a4f4bd0063875624592398fc83a2243';
const B32_KEYS = ['thermalMonitoringPeriod', 'thermalMonitoringDeviation', 'thermalMonitoringExtrema',
  'thermalMonitoringEmpty', 'thermalMonitoringKeyboard'];

function b32Events(rows = register) {
  const entryIds = Array.from({ length: 10 }, (_, index) => `T${1460 + index}`);
  const protectedIds = new Set(entryIds);
  const events = rows.filter(row => row.batch === B32 || protectedIds.has(row.entry_id));
  assert.deepEqual(events, rows.slice(1459, 1469));
  assert.deepEqual(events.map(row => row.entry_id), entryIds);
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.equal(registerRowsDigest(events), B32_ROWS_DIGEST);
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language]),
    B32_KEYS.flatMap(key => [[key, 'FR'], [key, 'EN']]));
  return events;
}

function beforeB32(source, language, rows = register) {
  for (const entry of b32Events(rows).filter(row => row.language === language.toUpperCase())) {
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, entry.key_or_msgid);
    source = source.replace(line, '');
  }
  return source;
}

function b30Events(rows = register) {
  const entryIds = Array.from({ length: 56 }, (_, index) => `T${1402 + index}`);
  const protectedIds = new Set(entryIds);
  const events = rows.filter(row => row.batch === B30 || protectedIds.has(row.entry_id));
  assert.deepEqual(events, rows.slice(1401, 1457));
  assert.deepEqual(events.map(row => row.entry_id), entryIds);
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.equal(registerRowsDigest(events), B30_ROWS_DIGEST);
  return events;
}

function b31Events(rows = register) {
  const entryIds = ['T1458', 'T1459'];
  const protectedIds = new Set(entryIds);
  const events = rows.filter(row => row.batch === B31 || protectedIds.has(row.entry_id));
  assert.deepEqual(events, rows.slice(1457, 1459));
  assert.deepEqual(events.map(row => row.entry_id), entryIds);
  for (const entry of events) assert.deepEqual(Object.keys(entry), REGISTER_COLUMNS);
  assert.equal(registerRowsDigest(events), B31_ROWS_DIGEST);
  assert.deepEqual(events.map(row => [row.key_or_msgid, row.language, row.status]),
    [['dnaStateValidated', 'FR', 'REMOVED'], ['dnaStateValidated', 'EN', 'REMOVED']]);
  return events;
}

function beforeDonationIntegrationLayout(source, language, rows = register) {
  // Merge 90a1065 added exactly these three separators; do not normalize other whitespace.
  const dna = Object.fromEntries(b30Events(rows).filter(row => row.language === language.toUpperCase())
    .map(row => [row.key_or_msgid, row.final_value]));
  const donation = Object.fromEntries(b44Events(rows).filter(row => row.language === language.toUpperCase())
    .map(row => [row.key_or_msgid, row.final_value]));
  const header = language === 'fr' ? 'export const fr = {\n'
    : 'export const en: Record<TranslationKey, string> = {\n';
  const first = `  dnaMissing: '${dna.dnaMissing}',\n`;
  const last = `  dnaAuditRevoked: '${dna.dnaAuditRevoked}',\n`;
  const warning = `  donationAcceptanceUnavailable: '${donation.donationAcceptanceUnavailable}',\n`;
  for (const [current, previous] of [[`${header}\n${first}`, header + first],
    [`${last}\n`, last], [`${warning}\n`, warning]]) {
    assert.equal(source.split(current).length - 1, 1, 'Donation integration separator changed');
    source = source.replace(current, previous);
  }
  return source;
}

function beforeB30(source, language, rows = register) {
  source = beforeDonationIntegrationLayout(source, language, rows);
  source = beforeSpeciesIntegration(source, language, rows);
  // A key removed by B31 must already be absent: an absent removed key is a
  // recorded removal, not an unrecorded missing translation.
  const removed = new Set(b31Events(rows).map(entry => entry.key_or_msgid));
  for (const entry of b30Events(rows).filter(row => row.language === language.toUpperCase())) {
    if (entry.surface !== 'react') continue;
    if (removed.has(entry.key_or_msgid)) {
      assert.equal(source.split('\n').some(line => line.startsWith(`  ${entry.key_or_msgid}: `)), false,
        entry.key_or_msgid);
      continue;
    }
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, entry.key_or_msgid);
    source = source.replace(line, '');
  }
  return source;
}

function beforeB25(source, language, rows = register) {
  for (const entry of b25Events(rows).filter(row => row.language === language.toUpperCase())) {
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, entry.key_or_msgid);
    source = source.replace(line, '');
  }
  return source;
}

test('B25 thermal additions require exact append-only events and source values', () => {
  for (const language of ['fr', 'en']) {
    // B25 lines are checked at their registered value, before the B28 in-place rewording.
    const source = beforeB28(baselineCatalogSource(language), language);
    assert.doesNotThrow(() => beforeB25(source, language));
    for (const entry of b25Events()) {
      assert.throws(() => beforeB25(source, language, register.filter(row => row !== entry)));
      for (const field of REGISTER_COLUMNS) {
        const changed = register.map(row => row === entry ? { ...row, [field]: `${row[field]} changed` } : row);
        assert.throws(() => beforeB25(source, language, changed), `${entry.entry_id}.${field}`);
      }
      if (entry.language !== language.toUpperCase()) continue;
      const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
      for (const replacement of ['', line + line, line.replace(": '", ": 'changed ")]) {
        assert.throws(() => beforeB25(source.replace(line, replacement), language));
      }
    }
    const reordered = register.slice();
    [reordered[1060], reordered[1061]] = [reordered[1061], reordered[1060]];
    assert.throws(() => beforeB25(source, language, reordered));
    assert.throws(() => beforeB25(source, language, [...register, { ...register[1060], entry_id: 'T1087' }]));
    for (const field of ['unknown_column', 'sheet_value']) {
      const changed = register.map(row => ({ ...row }));
      if (field === 'unknown_column') changed[1060][field] = 'unapproved';
      else delete changed[1060][field];
      assert.throws(() => beforeB25(source, language, changed));
    }
  }
  const correction = { ...register[1060], entry_id: 'T1087', batch: 'B26-2026-11-10', status: 'APPLIED',
    initial_value: register[1060].final_value, final_value: 'Later correction' };
  assert.deepEqual(b25Events([...register, correction]), b25Events());
});

function beforeB24(source, language, rows = register) {
  for (const entry of b24Events(rows).filter(row => row.language === language.toUpperCase())) {
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, entry.key_or_msgid);
    source = source.replace(line, '');
  }
  return source;
}

test('B24 additions require exact append-only events and source values', () => {
  assert.deepEqual([...new Set(b24Events().map(row => row.key_or_msgid))], B24_ADDED_KEYS);
  for (const language of ['fr', 'en']) {
    const source = baselineCatalogSource(language);
    assert.doesNotThrow(() => beforeB24(source, language));
    for (const entry of b24Events()) {
      assert.throws(() => beforeB24(source, language, register.filter(row => row !== entry)));
      for (const field of REGISTER_COLUMNS) {
        const changed = register.map(row => row === entry ? { ...row, [field]: `${row[field]} changed` } : row);
        assert.throws(() => beforeB24(source, language, changed), `${entry.entry_id}.${field}`);
      }
      if (entry.language !== language.toUpperCase()) continue;
      const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
      for (const replacement of ['', line + line, line.replace(": '", ": 'changed ")]) {
        assert.throws(() => beforeB24(source.replace(line, replacement), language));
      }
    }
    const reordered = register.slice();
    [reordered[1054], reordered[1055]] = [reordered[1055], reordered[1054]];
    assert.throws(() => beforeB24(source, language, reordered));
    assert.throws(() => beforeB24(source, language, [...register, { ...register[1054], entry_id: 'T1061' }]));
    for (const field of ['unknown_column', 'sheet_value']) {
      const changed = register.map(row => ({ ...row }));
      if (field === 'unknown_column') changed[1054][field] = 'unapproved';
      else delete changed[1054][field];
      assert.throws(() => beforeB24(source, language, changed));
    }
  }
});

test('B30/B31 DNA additions require exact append-only events, source values and removal', () => {
  assert.deepEqual(DNA_KEYS, Object.keys(fr).slice(0, DNA_KEYS.length));
  assert.equal(DNA_KEYS.length, 21);
  assert.deepEqual(b31Events().map(row => [row.key_or_msgid, row.language, row.status]),
    [['dnaStateValidated', 'FR', 'REMOVED'], ['dnaStateValidated', 'EN', 'REMOVED']]);
  for (const language of ['fr', 'en']) {
    const source = beforeB47(beforeB48(baselineCatalogSource(language), language), language);
    assert.doesNotThrow(() => beforeB30(source, language));
    for (const entry of [...b30Events(), ...b31Events()]) {
      const events = entry.batch === B30 ? b30Events : b31Events;
      assert.throws(() => events(register.filter(row => row !== entry)));
      for (const field of REGISTER_COLUMNS) {
        const changed = register.map(row => row === entry ? { ...row, [field]: `${row[field]} changed` } : row);
        assert.throws(() => events(changed), `${entry.entry_id}.${field}`);
      }
      if (entry.surface !== 'react' || entry.language !== language.toUpperCase()) continue;
      if (entry.key_or_msgid === 'dnaStateValidated') {
        // B31 removed this key: re-adding it to the catalog is rejected.
        assert.throws(() => beforeB30(source + `  ${entry.key_or_msgid}: '${entry.initial_value}',\n`, language));
        continue;
      }
      const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
      for (const replacement of ['', line + line, line.replace(": '", ": 'changed ")]) {
        assert.throws(() => beforeB30(source.replace(line, replacement), language));
      }
    }
    const reordered = register.slice();
    [reordered[1401], reordered[1402]] = [reordered[1402], reordered[1401]];
    assert.throws(() => beforeB30(source, language, reordered));
    assert.throws(() => beforeB30(source, language, [...register, { ...register[1401], entry_id: 'T1460' }]));
    for (const field of ['unknown_column', 'sheet_value']) {
      const changed = register.map(row => ({ ...row }));
      if (field === 'unknown_column') changed[1401][field] = 'unapproved';
      else delete changed[1401][field];
      assert.throws(() => beforeB30(source, language, changed));
    }
  }
});

test('B24 labels preserve exact FR/EN values and zone interpolation in Japanese fallback', () => {
  const translate = loadI18n().createTranslator('ja');
  const expected = {
    historyReactivation: ['Réactivation', 'Reactivation'],
    historyLocationExit: ['Sortie de {zone}', 'Exit from {zone}'],
    historyLocationEntry: ['Entrée en {zone}', 'Entry into {zone}'],
  };
  for (const key of B24_ADDED_KEYS) {
    assert.deepEqual([fr[key], en[key]], expected[key]);
    assert.equal(translate(key), en[key]);
    assert.equal(translate(key).replace('{zone}', 'Zone 0'), en[key].replace('{zone}', 'Zone 0'));
    const row = manifest.find(row => row.identity === key);
    assert.deepEqual([row.current_fr, row.current_en], expected[key]);
  }
});

function beforeB23(source, language, rows = register) {
  const events = rows.filter(row => row.batch === 'B23-2026-11-10');
  assert.deepEqual(events, rows.slice(1042, 1054));
  assert.deepEqual(events.map(row => row.entry_id), Array.from({ length: 12 }, (_, i) => `T${1043 + i}`));
  assert.equal(registerRowsDigest(events), 'e923b831b624a2367b92bcfd9ba3b55eec575f1ec9241333d632920dc14b8423');
  const entries = events.filter(row => row.language === language.toUpperCase());
  for (const entry of entries) {
    if (entry.status === 'REMOVED') {
      assert.equal(source.split('\n').some(line => line.startsWith(`  ${entry.key_or_msgid}: `)), false, entry.key_or_msgid);
    } else {
      const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
      assert.equal(source.split(line).length - 1, 1, line);
      source = source.replace(line, '');
    }
  }
  // Restore exact original positions before applying the older frozen-source checks.
  for (const [keys, anchor] of [
    [B23_RETIRED_KEYS.slice(0, 2), 'historyReconstructionUnresolved'],
    [B23_RETIRED_KEYS.slice(2), 'historyLoading'],
  ]) {
    const prefix = `  ${anchor}: `;
    assert.equal(source.split(prefix).length - 1, 1, anchor);
    const restored = keys.map(key => {
      const entry = entries.find(row => row.key_or_msgid === key);
      return `  ${key}: '${entry.initial_value}',\n`;
    }).join('');
    source = source.replace(prefix, restored + prefix);
  }
  return source;
}

test('B23 reconstruction requires exact append-only events, additions and retirements', () => {
  for (const language of ['fr', 'en']) {
    const source = baselineCatalogSource(language);
    assert.doesNotThrow(() => beforeB23(source, language));
    for (const key of B23_RETIRED_KEYS) {
      assert.throws(() => beforeB23(source.replace('\n}', `\n  ${key}: 'Restored',\n}`), language));
    }
    for (const key of B23_ADDED_KEYS) {
      const line = source.split('\n').find(line => line.startsWith(`  ${key}: `));
      assert.throws(() => beforeB23(source.replace(line, ''), language));
      assert.throws(() => beforeB23(source.replace(line, line.replace(": '", ": 'changed ")), language));
    }
    for (const entry of register.filter(row => row.batch === 'B23-2026-11-10')) {
      assert.throws(() => beforeB23(source, language, register.filter(row => row !== entry)));
      for (const field of REGISTER_COLUMNS) {
        const changed = register.map(row => row === entry ? { ...row, [field]: `${row[field]} changed` } : row);
        assert.throws(() => beforeB23(source, language, changed), `${entry.entry_id}.${field}`);
      }
    }
    const reordered = register.slice();
    [reordered[1042], reordered[1043]] = [reordered[1043], reordered[1042]];
    assert.throws(() => beforeB23(source, language, reordered));
    assert.throws(() => beforeB23(source, language, [...register, { ...register[1042], entry_id: 'T1055' }]));
  }
});

function historicalSource(source, current = false, language) {
  if (current) {
    source = beforeB30(beforeB47(beforeB48(source, language), language), language);
    source = beforeB24(beforeB25(beforeB26(beforeB27(beforeB28(source, language), language), language), language), language);
  }
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  const keys = current
    ? [...LATER_FALLBACK_KEYS.filter(key => !B23_RETIRED_KEYS.includes(key)), ...B23_ADDED_KEYS]
    : LATER_FALLBACK_KEYS;
  if (current) for (const key of B23_RETIRED_KEYS) {
    assert.equal(lines.some(line => line.startsWith(`  ${key}: `)), false, key);
  }
  const prefixes = new Set(keys.map(key => `  ${key}: '`));
  const isAdditive = line => prefixes.has(`${line.split(": '", 1)[0]}: '`);
  const additive = lines.filter(isAdditive);
  for (const key of keys) {
    assert.equal(additive.filter(line => line.startsWith(`  ${key}: '`)).length, 1, `${key}: one additive line`);
  }
  return lines.filter(line => !isAdditive(line)).join('\n');
}

function beforeMovementLabelChange(source, language) {
  source = beforeB47(beforeB48(source, language), language);
  source = beforeB32(source, language);
  source = beforeB30(source, language);
  source = beforeB23(beforeB24(beforeB25(beforeB26(beforeB27(beforeB28(source, language), language), language), language), language), language);
  // B22 only rewords the reconstructed historical movement title and interval, registered as APPLIED.
  const renamed = register.filter(entry => entry.batch === 'B22-2026-11-10' && entry.language === language.toUpperCase());
  assert.deepEqual(renamed.map(entry => [entry.key_or_msgid, entry.status]), [['historyHistoricalMovement', 'APPLIED'],
    ['historyBetweenRecords', 'APPLIED'], ['historyBetweenRecordsAcrossYears', 'APPLIED']]);
  for (const entry of renamed) {
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, line);
    source = source.replace(line, `  ${entry.key_or_msgid}: '${entry.initial_value}',\n`);
  }
  const [initial, final, entryId] = language === 'fr'
    ? ['Déplacement ordinaire', 'Déplacement', 'T1007']
    : ['Ordinary movement', 'Movement', 'T1008'];
  const row = register.find(entry => entry.entry_id === entryId);
  assert.deepEqual([row?.key_or_msgid, row?.language, row?.initial_value, row?.final_value, row?.status],
    ['movementKindOrdinary', language.toUpperCase(), initial, final, 'APPLIED']);
  const line = `  movementKindOrdinary: '${final}',\n`;
  assert.equal(source.split(line).length - 1, 1, 'only the explicitly approved movement label changes');
  source = source.replace(line, `  movementKindOrdinary: '${initial}',\n`);
  const reconstruction = register.find(entry => entry.entry_id === (language === 'fr' ? 'T1009' : 'T1010'));
  assert.equal(reconstruction?.key_or_msgid, 'historyAcceptedReconstruction');
  assert.equal(reconstruction?.status, 'APPLIED');
  const original = postBaseline.get('historyAcceptedReconstruction')[language.toUpperCase()];
  assert.equal(reconstruction.initial_value, original);
  const reconstructionLine = `  historyAcceptedReconstruction: '${reconstruction.final_value}',\n`;
  assert.equal(source.split(reconstructionLine).length - 1, 1);
  source = source.replace(reconstructionLine, `  historyAcceptedReconstruction: '${original}',\n`);
  // B20 and B21 only add these exact registered lines; every older line stays frozen.
  const potential = register.filter(entry => ['B20-2026-11-09', 'B21-2026-11-09'].includes(entry.batch) && entry.language === language.toUpperCase());
  assert.deepEqual(potential.map(entry => entry.key_or_msgid), potentialMovementKeys);
  for (const entry of potential) {
    assert.deepEqual([entry.surface, entry.status, entry.initial_value], ['react', 'ALREADY_MATCHING', entry.final_value], entry.entry_id);
    const line = `  ${entry.key_or_msgid}: '${entry.final_value}',\n`;
    assert.equal(source.split(line).length - 1, 1, line);
    source = source.replace(line, '');
  }
  return source;
}

test('B20/B21 movement and B25 thermal keys are exact disjoint additions, not frozen baseline keys', () => {
  assert.equal(zoneChartKeys.length, 13);
  assert.equal(new Set(zoneChartKeys).size, 13);
  assert.equal(potentialMovementKeys.length, 13);
  assert.equal(new Set([...potentialMovementKeys, ...zoneChartKeys]).size, 26);
  for (const [batches, keys, start] of [
    [['B20-2026-11-09', 'B21-2026-11-09'], potentialMovementKeys, 1011],
    [['B25-2026-11-10'], zoneChartKeys, 1061],
  ]) {
    const events = register.filter(row => batches.includes(row.batch));
    assert.deepEqual(events.map(row => row.entry_id), Array.from({ length: 26 }, (_, index) => `T${start + index}`));
    assert.deepEqual(events.map(row => [row.key_or_msgid, row.language]), keys.flatMap(key => [[key, 'FR'], [key, 'EN']]));
    for (const key of keys) assert.equal(postBaseline.has(key), false, key);
  }
});

test('historical source reconstruction rejects missing or duplicate movement and thermal additions', () => {
  for (const language of ['fr', 'en']) {
    const source = baselineCatalogSource(language);
    for (const key of [...potentialMovementKeys.filter(key => !B23_RETIRED_KEYS.includes(key)), ...zoneChartKeys]) {
      const value = (language === 'fr' ? fr : en)[key];
      const line = `  ${key}: '${value}',\n`;
      assert.throws(() => beforeMovementLabelChange(source.replace(line, ''), language), undefined, `${language}.${key}: missing`);
      assert.throws(() => beforeMovementLabelChange(source.replace(line, line + line), language), undefined, `${language}.${key}: duplicate`);
    }
  }
});

test('original FR and EN source content remains frozen apart from the registered movement label change', () => {
  // Removing exact registered additions must restore the original frozen bytes.
  for (const [language, digest] of [
    ['fr', '1b3486aaea5eea3f8628e96bc88dac9835d3a89da88668ca020ea9119ee757dc'],
    ['en', '62989721d2528126ab48704fe783597dba0d484d2f24ce05a7391e0e9bdbfa27'],
  ]) {
    let source = beforeMovementLabelChange(baselineCatalogSource(language), language);
    assert.deepEqual([...postBaseline.keys()].sort(), [...LATER_FALLBACK_KEYS].sort());
    assert.equal(sha256(historicalSource(source)), digest, language);
    for (const [key, values] of postBaseline) {
      const line = `  ${key}: '${values[language.toUpperCase()]}',\n`;
      assert.equal(source.split(line).length - 1, 1, line);
      source = source.replace(line, '');
    }
    assert.equal(sha256(source), digest, language);
  }
});

test('additive keys stay English fallbacks without Japanese approval or provenance', () => {
  const translate = loadI18n().createTranslator('ja');
  for (const key of [...LATER_FALLBACK_KEYS, ...potentialMovementKeys, ...B23_ADDED_KEYS, ...B24_ADDED_KEYS,
    ...zoneChartKeys, ...thermalPeriodKeys, ...probeAverageKeys, ...DNA_KEYS, ...B32_KEYS, ...currentSpeciesKeys, ...B49_ADDED_KEYS]
    .filter(key => !B23_RETIRED_KEYS.includes(key))) {
    const rows = manifest.filter(row => row.identity === key);
    assert.equal(rows.length, 1, key);
    const row = rows[0];
    assert.deepEqual([row.surface, row.status, row.reason_code], ['react', 'FALLBACK_EN', 'ABSENT_FROM_WORKBOOK']);
    for (const field of ['ja_validated', 'ja_ai_reviewed', 'sheet_row', 'sheet_section', 'sheet_fr', 'sheet_en']) assert.equal(row[field], '', `${key}.${field}`);
    assert.equal(row.takeru_review, 'no', key);
    if (!retired.has(key)) assert.equal(translate(key), en[key], key);
  }
});

test('source fingerprints accept the scoped additions but reject every historical value change', () => {
  for (const language of ['fr', 'en']) {
    const source = baselineCatalogSource(language);
    const digest = sha256(historicalSource(source, true, language));
    for (const key of baselineFrKeys) {
      const line = source.split('\n').find(line => line.trimStart().startsWith(`${key}: `));
      assert.ok(line, `${language}.${key}: source line exists`);
      const mutated = line.replace(/(: ['"])/, '$1changed ');
      assert.notEqual(mutated, line, `${language}.${key}: mutation must change the value`);
      const changed = source.replace(line, mutated);
      if (B24_ADDED_KEYS.includes(key) || zoneChartKeys.includes(key) || thermalPeriodKeys.includes(key)
          || probeAverageKeys.includes(key) || sharingKeys.includes(key) || B44_ADDED_KEYS.includes(key) || B45_ADDED_KEYS.includes(key) || DNA_KEYS.includes(key)
          || B47_ADDED_KEYS.includes(key) || key === DEMO_BANNER_KEY || B49_ADDED_KEYS.includes(key)
                    || speciesIntegrationKeys.includes(key) || (key === 'firstMeasurementOn' && language === 'en')
                    || (key === 'adminTabTransfers' && language === 'en')) {
        assert.throws(() => historicalSource(changed, true, language), `${language}.${key}`);
        continue;
      }
      assert.equal(sha256(historicalSource(changed, true, language)) === digest, [...LATER_FALLBACK_KEYS, ...B23_ADDED_KEYS].includes(key), `${language}.${key}`);
    }
    const unknown = source.replace('\n}', "\n  unapprovedAddition: 'Value',\n}");
    assert.notEqual(sha256(historicalSource(unknown, true, language)), digest, 'unknown additions are not exempt');

  }
});

test('all 44 historical movement additions have exact FR/EN provenance and English fallback', () => {
  assert.equal(historicalMovementKeys.length, 44);
  assert.equal(new Set(historicalMovementKeys).size, 44);
  const rows = manifest.filter(row => row.surface === 'react' && historicalMovementKeys.includes(row.identity));
  assert.deepEqual(rows.map(row => row.identity), historicalMovementKeys.filter(key => !B23_RETIRED_KEYS.includes(key)));
  const translate = loadI18n().createTranslator('ja');
  for (const row of rows) {
    const key = row.identity;
    const original = key === 'historyAcceptedReconstruction' ? postBaseline.get(key) : { FR: fr[key], EN: en[key] };
    assert.equal(row.current_fr, original.FR, `${key}: original FR provenance`);
    assert.equal(row.current_en, original.EN, `${key}: original EN provenance`);
    assert.equal(row.status, 'FALLBACK_EN', key);
    assert.equal(row.reason_code, 'ABSENT_FROM_WORKBOOK', key);
    assert.equal(row.takeru_review, 'no', key);
    for (const column of ['sheet_row', 'sheet_section', 'sheet_fr', 'sheet_en', 'ja_validated', 'ja_ai_reviewed']) {
      assert.equal(row[column], '', `${key}: no historical or reviewed provenance`);
    }
    assert.equal(Object.hasOwn(ja, key), false, key);
    assert.equal(translate(key), en[key], `${key}: English runtime fallback`);
  }
});

test('the 36 historical deferred React rows use English only when still present', () => {
  const deferred = manifest.filter(row => row.surface === 'react' && row.status === 'FALLBACK_EN_REVIEW');
  assert.equal(deferred.length, 36);
  const translate = loadI18n().createTranslator('ja');
  for (const row of deferred) {
    assert.equal(row.ja_ai_reviewed, '', row.identity);
    assert.equal(Object.hasOwn(ja, row.identity), false, row.identity);
    if (retired.has(row.identity)) assert.equal(Object.hasOwn(en, row.identity), false, row.identity);
    else assert.equal(translate(row.identity), en[row.identity], row.identity);
  }
});

test('all eight open Japanese questions use current English until validation', () => {
  const keys = ['auditActionTransfer', 'auditObjectOrganization', 'auditMetaMembershipId',
    'boxInventoryLastKnownLocation', 'boxLifecycleLastKnownLocation', 'adminTransferText',
    'movementErrorProvenance', 'zoneMovementExits'];
  const translate = loadI18n().createTranslator('ja');
  for (const key of keys) {
    const row = manifest.find(row => row.identity === key);
    assert.equal(row.status, 'FALLBACK_EN_REVIEW', key);
    assert.equal(row.reason_code, 'OPEN_QUESTION', key);
    assert.ok(row.ja_validated, `${key}: preserve the Japanese reviewer's original text`);
    assert.equal(Object.hasOwn(ja, key), false, key);
    assert.equal(translate(key), en[key], key);
  }
});

test('ja values are real, non-empty Japanese with the same placeholders', () => {
  for (const [key, value] of jaEntries) {
    assert.ok(value.trim(), `${key}: empty`);
    assert.equal(value, value.trim(), `${key}: edge whitespace`);
    assert.equal(value.includes('·'), false, `${key}: middle dot`);
    assert.deepEqual(placeholders(value), placeholders(fr[key]), `${key}: FR placeholders`);
    assert.deepEqual(placeholders(value), placeholders(en[key]), `${key}: EN placeholders`);
    if (NEUTRAL_VALUES.has(key)) continue;
    // These reviewed Japanese units can match a source while still containing Japanese text.
    if (!['probeElapsedMinutes', 'probeElapsedHours', 'probeElapsedDays'].includes(key)) {
      assert.notEqual(value, en[key], `${key}: English copied as Japanese`);
      assert.notEqual(value, fr[key], `${key}: French copied as Japanese`);
    }
    assert.match(value, japaneseCharacter, `${key}: no Japanese text`);
  }
});

test('glossary-sensitive Japanese terms are never displayed against the reference', () => {
  for (const [key, value] of jaEntries) {
    assert.equal(value.includes('継代'), false, `${key}: G18 transplant is 移植`);
    if (/repiqu/i.test(fr[key])) assert.ok(value.includes('移植'), `${key}: G18 移植`);
    if (/\bRead-only mode\b/.test(en[key])) assert.ok(value.includes('閲覧専用'), `${key}: G36 閲覧専用`);
    if (/\bManager\b/.test(en[key])) assert.ok(value.includes('責任者'), `${key}: G33 責任者`);
    if (/salinity measurement/i.test(en[key])) assert.equal(value.includes('測定記録'), false, `${key}: Measurement`);
  }
  for (const key of ['auditActionSubculture', 'manageDemoteToViewer', 'organizationMenuTitle', 'overviewEmpty',
    'movementKindUnclassified', 'auditValuePendingReview', 'zoneSalinityCreateAction']) {
    assert.equal(Object.hasOwn(ja, key), false, `${key}: deferred for review`);
  }
  for (const key of PERMISSION_KEYS.filter(key => Object.hasOwn(ja, key))) {
    assert.match(ja[key], ADMIN_INVITATION, `${key}: permission refusal invites contacting an administrator`);
  }
});

test('FR and EN translators are unchanged and Japanese falls back to English per key', () => {
  const i18n = loadI18n();
  const translators = Object.fromEntries(['fr', 'en', 'ja'].map(language => [language, i18n.createTranslator(language)]));
  assert.deepEqual(Object.keys(i18n.translations.ja).sort(), Object.keys(fr).sort());
  for (const key of Object.keys(fr)) {
    assert.equal(translators.fr(key), fr[key]);
    assert.equal(translators.en(key), en[key]);
    assert.equal(translators.ja(key), Object.hasOwn(ja, key) ? ja[key] : en[key]);
    assert.equal(i18n.translations.ja[key], translators.ja(key));
  }
  assert.equal(i18n.translations.fr, fr);
  assert.equal(i18n.translations.en, en);
  assert.equal(translators.ja('createBoxTitle'), '容器を作成');
  assert.equal(translators.ja('boxActivateForbidden'), en.boxActivateForbidden);
  assert.equal(translators.ja('back'), '戻る');
  assert.equal(translators.ja('missingKeyForTest'), 'missingKeyForTest');
});

test('Japanese create-box count composition preserves zero and singular/plural selection', () => {
  const translate = loadI18n().createTranslator('ja');
  for (const count of [0, 1, 2, 12]) {
    const key = count === 1 ? 'createBoxActiveBox' : 'createBoxActiveBoxes';
    assert.ok(Object.hasOwn(ja, key), `${key}: reviewed Japanese count label`);
    const details = `${count} ${translate(key)}`;
    assert.equal(details, `${count} 個の有効な容器`);
    assert.match(details, new RegExp(`^${count} `));
    assert.match(details, japaneseCharacter);
    assert.equal(details.includes('{count}'), false, 'count is composed by StrainPicker, not a label placeholder');
  }
});

test('Japanese probe interpolation preserves zero, durations and scientific units', () => {
  const translate = loadI18n().createTranslator('ja');
  for (const [key, unit] of [
    ['probeElapsedMinutes', '分'], ['probeElapsedHours', '時間'], ['probeElapsedDays', '日'],
  ]) {
    assert.ok(Object.hasOwn(ja, key), key);
    for (const count of [0, 1, 24]) {
      const duration = translate(key).replace('{count}', String(count));
      assert.ok(duration.includes(String(count)), `${key}: preserve ${count}`);
      assert.ok(duration.includes(unit), `${key}: preserve the Japanese unit`);
      assert.deepEqual(placeholders(duration), []);
      const readingAgo = translate('probeReadingAgo').replace('{duration}', duration);
      assert.equal(readingAgo, `${duration}前`, 'preserve the full Japanese duration including zero');
      assert.deepEqual(placeholders(readingAgo), []);
      assert.equal(Object.hasOwn(ja, 'probeSilentDescription'), false, 'blocked description stays English');
      const silent = translate('probeSilentDescription').replace('{duration}', duration);
      assert.equal(silent, en.probeSilentDescription.replace('{duration}', duration));
      assert.ok(silent.includes(duration), 'English fallback preserves the interpolated Japanese duration');
      assert.deepEqual(placeholders(silent), []);
    }
  }
  assert.ok(Object.hasOwn(ja, 'probeChartSummary'));
  for (const [minimum, maximum] of [['0 °C', '24.5 °C'], ['-1.25 °C', '0 °C']]) {
    const summary = translate('probeChartSummary').replace('{min}', minimum).replace('{max}', maximum);
    assert.ok(summary.includes(minimum), 'preserve minimum and its unit');
    assert.ok(summary.includes(maximum), 'preserve maximum and its unit');
    assert.equal(summary.match(/°C/g)?.length, 2, 'both scientific units survive interpolation');
    assert.deepEqual(placeholders(summary), []);
    assert.match(summary, japaneseCharacter);
  }
});

test('Japanese is selectable and persisted; unknown values keep the French default', () => {
  const localStorage = storage();
  const i18n = loadI18n({ localStorage });
  for (const [input, expected] of [['ja', 'ja'], ['ja-JP', 'ja'], [' JA ', 'ja'], ['en-GB', 'en'], ['fr', 'fr'],
    ['de', 'fr'], ['', 'fr'], [null, 'fr'], [undefined, 'fr'], ['constructor', 'fr'], ['toString', 'fr']]) {
    assert.equal(i18n.resolveLanguage(input), expected, String(input));
  }
  assert.equal(i18n.getStoredInterfaceLanguage(), null);
  assert.equal(i18n.setStoredInterfaceLanguage('ja'), 'ja');
  assert.equal(localStorage.values.get('polypbase.interfaceLanguage'), 'ja');
  assert.equal(loadI18n({ localStorage }).getStoredInterfaceLanguage(), 'ja', 'survives a reload');
  assert.equal(i18n.setStoredInterfaceLanguage('xx'), 'fr');
  const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };
  const restricted = loadI18n({ localStorage: broken });
  assert.equal(restricted.getStoredInterfaceLanguage(), null);
  assert.equal(restricted.setStoredInterfaceLanguage('ja'), 'ja');
});

test('local presentation tables keep FR/EN and use validated Japanese with English fallback', () => {
  const i18n = loadI18n();
  const copy = { fr: { a: 'A fr', b: 'B fr' }, en: { a: 'A en', b: 'B en' }, ja: { a: 'A ja' } };
  assert.equal(i18n.selectLocalCopy('fr', copy), copy.fr);
  assert.equal(i18n.selectLocalCopy('en', copy), copy.en);
  assert.deepEqual({ ...i18n.selectLocalCopy('ja', copy) }, { a: 'A ja', b: 'B en' });
  assert.deepEqual({ ...i18n.selectLocalCopy('ja', { fr: copy.fr, en: copy.en }) }, copy.en);

  const local = manifest.filter(row => row.surface === 'local');
  assert.deepEqual(local.reduce((total, row) => ({ ...total, [row.status]: (total[row.status] ?? 0) + 1 }), {}),
    { REUSED: 67, FALLBACK_EN_REVIEW: 1 });
  for (const row of local) {
    const [location, member] = row.identity.slice('local:frontend/'.length).split('#');
    const property = member.split('.')[1];
    const source = read(`../${location}`).replace(/\r\n/g, '\n');
    const block = source.slice(source.search(/\n {2}ja: \{\n|\nconst japaneseStatusLabels/));
    const entry = new RegExp(`\\n {2,4}${property}: '([^'\\n]*)',`).exec(block);
    if (row.status === 'REUSED') assert.equal(entry?.[1], row.ja_validated, row.identity);
    else assert.equal(entry, null, `${row.identity}: deferred local text must use English`);
  }
  for (const row of manifest.filter(row => row.surface === 'inline')) {
    assert.equal(row.status, 'FALLBACK_EN_INLINE');
  }
});

test('box status labels are validated Japanese with unchanged tones and fallbacks', () => {
  const { getBoxStatusPresentation } = load('../src/boxStatus.ts');
  for (const [status, label] of [['pending_review', '確認待ち'], ['active', '有効'], ['inactive', '無効']]) {
    const english = getBoxStatusPresentation(status, 'en');
    assert.deepEqual({ ...getBoxStatusPresentation(status, 'ja') }, { label, tone: english.tone });
  }
  assert.deepEqual({ ...getBoxStatusPresentation('pending_review', 'fr') }, { label: 'À vérifier', tone: 'warning' });
  assert.deepEqual({ ...getBoxStatusPresentation('pending_review', 'en') }, { label: 'Pending review', tone: 'warning' });
  for (const language of ['fr', 'en', 'ja']) {
    assert.deepEqual({ ...getBoxStatusPresentation('legacy_state', language) }, { label: 'legacy state', tone: 'neutral' });
  }
});

test('Japanese dates use the ja-JP locale while FR/EN keep their locales', () => {
  const i18n = loadI18n();
  assert.deepEqual(['fr', 'en', 'ja'].map(i18n.getLanguageLocale), ['fr-FR', 'en-GB', 'ja-JP']);
  for (const lang of ['ja', 'ja-JP']) {
    const dateFormat = load('../src/utils/dateFormat.ts', {}, { document: { documentElement: { lang } } });
    assert.equal(dateFormat.getDocumentLocale(), 'ja-JP');
  }
  for (const [lang, locale] of [['fr', 'fr-FR'], ['en', 'en-GB']]) {
    const dateFormat = load('../src/utils/dateFormat.ts', {}, { document: { documentElement: { lang } } });
    assert.equal(dateFormat.getDocumentLocale(), locale);
  }
  class ApiError extends Error {}
  const movement = load('../src/utils/boxMovementCorrection.ts', { '../api/client': { ApiError } });
  const day = new Date('2026-11-02T00:00:00Z');
  for (const [language, locale] of [['fr', 'fr-FR'], ['en', 'en-GB'], ['ja', 'ja-JP']]) {
    assert.equal(movement.formatMovementTimestamp('2026-11-02', language),
      new Intl.DateTimeFormat(locale, { timeZone: 'UTC', dateStyle: 'medium' }).format(day));
  }
  assert.match(movement.formatMovementTimestamp('2026-11-02T13:05:00Z', 'ja'), /2026/);
});

test('the error screen follows the stored interface language, then the browser', () => {
  const jsx = (type, props) => ({ type, props });
  class Component { constructor(props) { this.props = props; } }
  function render({ stored, browser }) {
    const window = { localStorage: storage(stored ? { 'polypbase.interfaceLanguage': stored } : {}) };
    const i18n = loadI18n(window);
    const ErrorBoundary = load('../src/components/ErrorBoundary.tsx', {
      react: { Component }, 'react/jsx-runtime': { jsx, jsxs: jsx }, '../i18n': i18n,
    }, { navigator: { language: browser }, window, console }).default;
    const boundary = new ErrorBoundary({ children: 'content' });
    boundary.state = { error: new Error('boom') };
    const tree = boundary.render();
    return tree.props.children.props.children[0].props.children;
  }
  assert.equal(render({ stored: 'ja', browser: 'fr-FR' }), 'エラーが発生しました');
  assert.equal(render({ stored: null, browser: 'ja-JP' }), 'エラーが発生しました');
  assert.equal(render({ stored: null, browser: 'en-US' }), 'Something went wrong');
  assert.equal(render({ stored: null, browser: 'de-DE' }), 'Une erreur est survenue');
  assert.equal(render({ stored: 'en', browser: 'fr-FR' }), 'Something went wrong');
  assert.equal(render({ stored: 'fr', browser: 'en-US' }), 'Une erreur est survenue');
});
