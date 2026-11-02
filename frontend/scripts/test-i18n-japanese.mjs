import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

// Japanese interface: validated subset, English fallback, unchanged FR/EN.
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');

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
const obsolete = parseCsv(read('../../docs/translation-review/japanese/ja-obsolete-workbook-keys.csv'));
const register = parseCsv(read('../../docs/translation-review/application/register.csv').replace(/^﻿/, ''));

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

test('ja catalog is exactly the REUSED rows of the key-level manifest', () => {
  const react = manifest.filter(row => row.surface === 'react');
  assert.equal(react.length, Object.keys(fr).length);
  const reused = Object.fromEntries(react.filter(row => row.status === 'REUSED').map(row => [row.identity, row.ja_validated]));
  assert.deepEqual(Object.fromEntries(jaEntries), reused);
  for (const row of react.filter(row => row.status !== 'REUSED')) {
    assert.match(row.status, /^FALLBACK_EN/);
    assert.equal(Object.hasOwn(ja, row.identity), false, `${row.identity}: ${row.reason_code} must use English`);
  }
  const counts = react.reduce((total, row) => ({ ...total, [row.status]: (total[row.status] ?? 0) + 1 }), {});
  assert.deepEqual(counts, { REUSED: 927, FALLBACK_EN: 202, FALLBACK_EN_REVIEW: 39 });
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
    assert.notEqual(value, en[key], `${key}: English copied as Japanese`);
    assert.notEqual(value, fr[key], `${key}: French copied as Japanese`);
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
  assert.equal(translators.ja('createBoxTitle'), 'New box');
  assert.equal(translators.ja('boxActivateForbidden'), en.boxActivateForbidden);
  assert.equal(translators.ja('back'), '戻る');
  assert.equal(translators.ja('missingKeyForTest'), 'missingKeyForTest');
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
    const source = read(`../${location}`);
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
