import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

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
const obsolete = parseCsv(read('../../docs/translation-review/japanese/ja-obsolete-workbook-keys.csv'));
const register = parseCsv(read('../../docs/translation-review/application/register.csv').replace(/^﻿/, ''));
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
// Only these independently frozen thermal, lab and historical additions are excluded.
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
  const entries = rows.filter(row => row.baseline_git_head === FROZEN_BASELINE);
  const thermalLab = rows.slice(B13_START, LAB_START + LAB_ENTRY_IDS.length);
  assert.deepEqual(entries, [...thermalLab, ...historical], 'only frozen thermal, lab and historical events may use the frozen baseline');
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
  assert.deepEqual(react.map(row => row.identity), Object.keys(fr), 'one row per key in canonical FR order');
  const included = react.filter(row => ['REUSED', 'AI_REVIEWED'].includes(row.status));
  const expected = included.map(row => [row.identity,
    row.status === 'REUSED' ? row.ja_validated : row.ja_ai_reviewed]);
  assert.deepEqual(jaEntries, expected);
  for (const row of react.filter(row => !['REUSED', 'AI_REVIEWED'].includes(row.status))) {
    assert.match(row.status, /^FALLBACK_EN/);
    assert.equal(Object.hasOwn(ja, row.identity), false, `${row.identity}: ${row.reason_code} must use English`);
  }
  const counts = react.reduce((total, row) => ({ ...total, [row.status]: (total[row.status] ?? 0) + 1 }), {});
  // 143 original fallbacks, plus 7 thermal, 14 lab and 44 historical keys.
  assert.equal(postBaseline.size, 65);
  assert.deepEqual(counts, { REUSED: 930, FALLBACK_EN: 208, FALLBACK_EN_REVIEW: 36, AI_REVIEWED: 59 });
  assert.equal(counts.FALLBACK_EN, 143 + postBaseline.size);
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
  const edited = read('../src/i18n/en.ts').replace(/\r\n/g, '\n')
    .replace("  temperatureDailyMean: 'Daily mean',\n", "  temperatureDailyMean: 'Daily average',\n");
  let baseline = edited;
  for (const row of rewritten.slice(B13_START, HISTORICAL_START + 88).filter(row => row.language === 'EN')) {
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
  const correction = { ...b13[11], entry_id: 'T975', batch: 'B17-2026-11-05', initial_value: 'Daily mean',
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
    [...register, { ...register[858], entry_id: 'T975', batch: 'B17-2026-11-05' }],
    [...register, { ...register[858], entry_id: 'T975', baseline_git_head: '2'.repeat(40) }],
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
  for (const index of [0, B13_START, LAB_START, HISTORICAL_START]) {
    const extra = register.map(row => ({ ...row }));
    extra[index].unknown_column = 'unapproved';
    assert.throws(() => postBaselineReactKeys(extra));
    const missing = register.map(row => ({ ...row }));
    delete missing[index].sheet_value;
    assert.throws(() => postBaselineReactKeys(missing));
  }
});

test('registered post-baseline keys fall back to English without any Japanese', () => {
  assert.equal(postBaseline.size, 65);
  const react = new Map(manifest.filter(row => row.surface === 'react').map(row => [row.identity, row]));
  const translate = loadI18n().createTranslator('ja');
  for (const [key, values] of postBaseline) {
    assert.deepEqual([fr[key], en[key]], [values.FR, values.EN], key);
    const row = react.get(key);
    assert.deepEqual([row.current_fr, row.current_en], [values.FR, values.EN], key);
    assert.deepEqual([row.status, row.reason_code, row.sheet_row, row.ja_validated, row.ja_ai_reviewed],
      ['FALLBACK_EN', 'ABSENT_FROM_WORKBOOK', '', '', ''], key);
    assert.equal(Object.hasOwn(ja, key), false, key);
    assert.equal(translate(key), en[key], key);
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
  const ordered = Object.keys(fr).filter(key => byKey.has(key)).map(key => [key, byKey.get(key)]);
  assert.equal(ordered.length, 930);
  assert.equal(sha256(JSON.stringify(ordered)),
    'ab1705b630daaf621bcf152b1af3e6a7cdb098d342c5282fc2ec79ccb2330939');
  for (const row of manifest) {
    assert.ok(Object.hasOwn(row, 'ja_ai_reviewed'), 'manifest has a separate AI-reviewed column');
    if (row.status !== 'AI_REVIEWED') assert.equal(row.ja_ai_reviewed, '', row.identity);
  }
});

// Additions only: cbc513f thermal control, bb40685 Laboratory configuration and historical movements.
// Excluding these exact identities preserves the original AI-integration source hashes.
const LATER_FALLBACK_KEYS = [
  'temperatureRecentMean', 'temperatureNoRecent', 'temperatureRecentPeriod', 'temperatureRecentError',
  'temperatureDailyPeriod', 'temperatureDailyMean', 'temperatureDailyGap',
  'adminLabLocationsTitle', 'adminLabAddLocation', 'adminLabEditLocation', 'adminLabEditTitle',
  'adminLabActiveBoxes', 'adminLabMaxCapacity', 'adminLabTargetTemperatureField', 'adminLabReferenceSalinity',
  'adminLabInactive',
  'adminLabNoLocations', 'adminLabSaved', 'adminLabCapacityInvalid', 'adminLabTemperatureInvalid',
  'adminLabSalinityInvalid',
  ...historicalMovementKeys,
];

function historicalSource(source) {
  const lines = source.replace(/\r\n/g, '\n').split('\n');
  const prefixes = new Set(LATER_FALLBACK_KEYS.map(key => `  ${key}: '`));
  const isAdditive = line => prefixes.has(`${line.split(": '", 1)[0]}: '`);
  const additive = lines.filter(isAdditive);
  for (const key of LATER_FALLBACK_KEYS) {
    assert.equal(additive.filter(line => line.startsWith(`  ${key}: '`)).length, 1, `${key}: one additive line`);
  }
  return lines.filter(line => !isAdditive(line)).join('\n');
}

test('original FR and EN source content remains frozen regardless of Git line endings', () => {
  // Removing exact registered additions must restore the original frozen bytes.
  for (const [language, digest] of [
    ['fr', '1b3486aaea5eea3f8628e96bc88dac9835d3a89da88668ca020ea9119ee757dc'],
    ['en', '62989721d2528126ab48704fe783597dba0d484d2f24ce05a7391e0e9bdbfa27'],
  ]) {
    let source = read(`../src/i18n/${language}.ts`).replace(/\r\n/g, '\n');
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
  for (const key of LATER_FALLBACK_KEYS) {
    const rows = manifest.filter(row => row.identity === key);
    assert.equal(rows.length, 1, key);
    const row = rows[0];
    assert.deepEqual([row.surface, row.status, row.reason_code], ['react', 'FALLBACK_EN', 'ABSENT_FROM_WORKBOOK']);
    for (const field of ['ja_validated', 'ja_ai_reviewed', 'sheet_row', 'sheet_fr', 'sheet_en']) assert.equal(row[field], '', `${key}.${field}`);
    assert.equal(row.takeru_review, 'no', key);
    assert.equal(translate(key), en[key], key);
  }
});

test('source fingerprints accept the scoped additions but reject every historical value change', () => {
  for (const language of ['fr', 'en']) {
    const source = read(`../src/i18n/${language}.ts`).replace(/\r\n/g, '\n');
    const digest = sha256(historicalSource(source));
    for (const key of Object.keys(fr)) {
      const line = source.split('\n').find(line => line.trimStart().startsWith(`${key}: `));
      assert.ok(line, `${language}.${key}: source line exists`);
      const mutated = line.replace(/(: ['"])/, '$1changed ');
      assert.notEqual(mutated, line, `${language}.${key}: mutation must change the value`);
      const changed = source.replace(line, mutated);
      assert.equal(sha256(historicalSource(changed)) === digest, LATER_FALLBACK_KEYS.includes(key), `${language}.${key}`);
    }
    const unknown = source.replace('\n}', "\n  unapprovedAddition: 'Value',\n}");
    assert.notEqual(sha256(historicalSource(unknown)), digest, 'unknown additions are not exempt');

  }
});

test('all 44 historical movement additions have exact FR/EN provenance and English fallback', () => {
  assert.equal(historicalMovementKeys.length, 44);
  assert.equal(new Set(historicalMovementKeys).size, 44);
  const rows = manifest.filter(row => row.surface === 'react' && historicalMovementKeys.includes(row.identity));
  assert.deepEqual(rows.map(row => row.identity), historicalMovementKeys);
  const translate = loadI18n().createTranslator('ja');
  for (const row of rows) {
    const key = row.identity;
    assert.equal(row.current_fr, fr[key], `${key}: exact current FR`);
    assert.equal(row.current_en, en[key], `${key}: exact current EN`);
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

test('all 36 deferred React translations use current English at runtime', () => {
  const deferred = manifest.filter(row => row.surface === 'react' && row.status === 'FALLBACK_EN_REVIEW');
  assert.equal(deferred.length, 36);
  const translate = loadI18n().createTranslator('ja');
  for (const row of deferred) {
    assert.equal(row.ja_ai_reviewed, '', row.identity);
    assert.equal(Object.hasOwn(ja, row.identity), false, row.identity);
    assert.equal(translate(row.identity), en[row.identity], row.identity);
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
    assert.ok(row.ja_validated, `${key}: preserve Takeru's original text`);
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
