import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

// Deliberately scoped to reviewed keys, not a suffix or vocabulary heuristic.
const permissionKeys = [
  'boxActivateForbidden', 'createBoxForbidden', 'manageErrorMembershipAdminRequired',
  'manageErrorResponsableRequired', 'measurementForbidden', 'moveForbidden',
  'manageErrorActiveResponsableRequired', 'boxArchiveForbidden', 'subcultureForbidden',
];
const contactSentence = {
  fr: 'Merci de contacter un administrateur.',
  en: 'Please contact your administrator.',
};

// Advisory only: these need contextual review, not an automatic wording failure.
const advisoryExclusions = {
  shared401: 'Shared API authentication/session fallback, not a catalog permission refusal.',
  shared403: 'Shared API fallback can cover several causes; review separately from these catalog keys.',
  qrScannerPermission: 'Browser/device camera access, not an application administrator permission.',
  qrScannerSecureContext: 'HTTPS prerequisite, not an application permission.',
  csvErrorPermission: 'Mixed session/institution guidance; not in the approved sentence-only batch.',
  manualTemperatureForbidden: 'Deferred legacy temperature refusal; not in the approved permission-key batch.',
  movementErrorForbidden: 'Deferred shared 401/403 movement fallback; not in the approved permission-key batch.',
  manageErrorResponsableProtected: 'Manager-protection invariant, not a permission refusal.',
  manageErrorLastActiveResponsable: 'Last-active-manager invariant, not a permission refusal.',
  validations: 'Validation, conflicts, business rules and technical errors must not gain a permission invitation.',
};

// Application decisions from B01 take precedence over historical Sheet wording.
// No global ban on Organization, Subculture or Measurement: meaning is contextual.
const approved = {
  fr: {
    adminZoneTypeCabinet: 'Emplacement',
    historicalTemperaturePeriod: 'Mesuré pour la période :',
    organizationChoiceIntro: 'Choisissez la structure dans laquelle vous souhaitez travailler. Les données affichées et vos droits dépendent de la structure sélectionnée.',
  },
  en: {
    overviewEmpty: 'No living box to display.',
    movementKindUnclassified: 'Provenance not established',
    auditValuePendingReview: 'Needs review',
    adminZoneTypeCabinet: 'Rack',
    historicalTemperaturePeriod: 'Measured for the period:',
    moveDialogCurrentZone: 'Current location',
    loginIntro: 'Access your boxes and records.',
    recentAccess: 'Latest access',
    auditMetaResponsableStatus: 'Manager status',
    auditMetaCultureStatus: 'Box status',
    historicalTemperatureFailed: 'Temperature history unavailable.',
    manageRelinquishResponsableConfirmTitle: 'Step down as manager?',
    auditDescriptionManualTemperature: 'Manual temperature for {zone} recorded',
    manageDemoteToViewer: 'Demote to Read-only mode',
    subcultureForbidden: 'This account cannot create transplant events. Please contact your administrator.',
  },
};

function unwrap(node) {
  while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
    || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node))) node = node.expression;
  return node;
}

function parseCatalog(source, language) {
  const file = ts.createSourceFile(`${language}.ts`, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS);
  function fail(message, node = file) {
    const { line, character } = file.getLineAndCharacterOfPosition(node.getStart(file));
    throw new Error(`${language}.ts:${line + 1}:${character + 1}: ${message}`);
  }
  if (file.parseDiagnostics.length) {
    const diagnostic = file.parseDiagnostics[0];
    fail(`Invalid TypeScript: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, ' ')}`);
  }
  const declarations = file.statements.filter(ts.isVariableStatement)
    .filter(statement => statement.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword))
    .flatMap(statement => [...statement.declarationList.declarations]
      .filter(declaration => ts.isIdentifier(declaration.name) && declaration.name.text === language)
      .map(declaration => ({ statement, declaration })));
  if (declarations.length !== 1) fail(`Expected exactly one exported const ${language} object literal`);
  const { statement, declaration } = declarations[0];
  if (!(statement.declarationList.flags & ts.NodeFlags.Const)) fail('Catalog must be const', statement);
  const object = unwrap(declaration.initializer);
  if (!object || !ts.isObjectLiteralExpression(object)) fail('Catalog must be an object literal', declaration);
  const values = new Map();
  for (const property of object.properties) {
    if (!ts.isPropertyAssignment(property)) fail('Only literal property assignments are supported', property);
    const name = property.name;
    if (!ts.isIdentifier(name) && !ts.isStringLiteral(name)) fail('Keys must be identifiers or string literals', name);
    const key = name.text;
    if (!key.trim()) fail('Empty key', name);
    if (key !== key.trim()) fail('Key has surrounding whitespace', name);
    if (values.has(key)) fail(`Duplicate key ${JSON.stringify(key)}`, name);
    const value = unwrap(property.initializer);
    if (!ts.isStringLiteral(value) && !ts.isNoSubstitutionTemplateLiteral(value)) {
      fail(`Value for ${key} must be a string literal`, property);
    }
    if (!value.text.trim()) fail(`Empty value for ${key}`, value);
    if (value.text !== value.text.trim()) fail(`Surrounding whitespace in ${key}`, value);
    if (value.text.includes('·')) fail(`Middle-dot in ${key}`, value);
    values.set(key, value.text);
  }
  return values;
}

function placeholders(value) {
  const names = new Map();
  // The runtime supports simple {name} replacement only, not ICU or escaped braces.
  const remainder = value.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (_, name) => {
    names.set(name, (names.get(name) ?? 0) + 1);
    return '';
  });
  if (/[{}]/.test(remainder)) throw new Error('Malformed placeholder braces (expected {name})');
  return [...names].sort(([a], [b]) => a.localeCompare(b));
}

function checkPair(catalogs) {
  const errors = [];
  for (const language of ['fr', 'en']) {
    const other = language === 'fr' ? 'en' : 'fr';
    for (const [key, value] of catalogs[language]) {
      if (!catalogs[other].has(key)) errors.push(`${language}.${key}: missing in ${other}`);
      try { placeholders(value); } catch (error) { errors.push(`${language}.${key}: ${error.message}`); }
    }
  }
  for (const [key, value] of catalogs.fr) {
    if (!catalogs.en.has(key)) continue;
    try {
      if (JSON.stringify(placeholders(value)) !== JSON.stringify(placeholders(catalogs.en.get(key)))) {
        errors.push(`${key}: placeholder names or multiplicity differ`);
      }
    } catch { /* Malformed braces were reported with the language above. */ }
  }
  return errors;
}

function checkDecisions(catalogs) {
  const errors = [];
  for (const language of ['fr', 'en']) {
    for (const [key, expected] of Object.entries(approved[language])) {
      if (catalogs[language].get(key) !== expected) errors.push(`${language}.${key}: approved application wording changed`);
    }
    const sentence = contactSentence[language];
    for (const key of permissionKeys) {
      const value = catalogs[language].get(key);
      if (typeof value !== 'string' || value.split(sentence).length - 1 !== 1 || !value.endsWith(` ${sentence}`)) {
        errors.push(`${language}.${key}: must end with exactly one invitation: ${sentence}`);
      }
    }
  }
  return errors;
}

const source = (language, body) => `export const ${language} = { ${body} } as const;`;
const pair = (fr, en = fr) => ({ fr: parseCatalog(source('fr', fr), 'fr'), en: parseCatalog(source('en', en), 'en') });
const decisionFixture = () => Object.fromEntries(['fr', 'en'].map(language => [language, new Map([
  ...permissionKeys.map(key => [key, `Refusal. ${contactSentence[language]}`]),
  ...Object.entries(approved[language]),
]) ]));

// Synthetic tests are independent of the catalogs so unfinished parent edits do
// not hide regressions in the checker itself.
test('AST accepts current annotations, const assertions, quoted keys, comments and string zero', () => {
  const parsed = parseCatalog(`import type { TranslationKey } from './fr';
    export const en: Record<TranslationKey, string> = ({
      // This is data, not executable code.
      zero: '0', 'quoted-key': "Text", template: \`Literal\`,
      annotated: ('Value' as const),
    } as const) satisfies Record<string, string>;`, 'en');
  assert.equal(parsed.get('zero'), '0');
  assert.equal(parsed.get('quoted-key'), 'Text');
  assert.equal(parsed.get('template'), 'Literal');
  assert.equal(parsed.get('annotated'), 'Value');
});

for (const [label, input, message] of [
  ['syntax error', 'export const en = { key: ; };', /Invalid TypeScript/],
  ['missing export', 'const en = {};', /exactly one exported const/],
  ['wrong export', 'export const other = {};', /exactly one exported const/],
  ['duplicate export', 'export const en = {}; export const en = {};', /exactly one exported const/],
  ['mutable export', 'export let en = {};', /must be const/],
  ['indirect object', 'const values = {}; export const en = values;', /must be an object literal/],
  ['call expression', 'export const en = dangerous();', /must be an object literal/],
  ['spread', source('en', "...other, key: 'Value'"), /Only literal property/],
  ['shorthand', source('en', 'key'), /Only literal property/],
  ['method', source('en', "key() { return 'Value'; }"), /Only literal property/],
  ['getter', source('en', "get key() { return 'Value'; }"), /Only literal property/],
  ['computed key', source('en', "['key']: 'Value'"), /Keys must be/],
  ['numeric key', source('en', "0: 'Value'"), /Keys must be/],
  ['empty key', source('en', "'': 'Value'"), /Empty key/],
  ['blank key', source('en', "' ': 'Value'"), /Empty key/],
  ['padded key', source('en', "' key': 'Value'"), /Key has surrounding whitespace/],
  ['duplicate key', source('en', "key: 'One', 'key': 'Two'"), /Duplicate key/],
  ['escaped duplicate key', source('en', "key: 'One', 'k\\u0065y': 'Two'"), /Duplicate key/],
  ['empty value', source('en', "key: ''"), /Empty value/],
  ['blank value', source('en', "key: '  '"), /Empty value/],
  ['leading whitespace', source('en', "key: ' Value'"), /Surrounding whitespace/],
  ['trailing whitespace', source('en', "key: 'Value\\n'"), /Surrounding whitespace/],
  ['middle-dot', source('en', "key: 'Technicien·ne'"), /Middle-dot/],
  ['escaped middle-dot', source('en', "key: 'A\\u00b7B'"), /Middle-dot/],
  ...['0', 'null', 'false', '{}', '[]', 'other', "'A' + 'B'", '`Value ${other}`'].map(value =>
    [`nonliteral ${value}`, source('en', `key: ${value}`), /must be a string literal/]),
]) {
  test(`AST rejects ${label}`, () => assert.throws(() => parseCatalog(input, 'en'), message));
}

test('exact parity and placeholder multiset accept reordering, repeated names and zero', () => {
  assert.deepEqual(checkPair(pair("zero: '0', key: '{zone}: {count} {count}'", "key: '{count} {zone} {count}', zero: '0'")), []);
  assert.deepEqual(checkPair(pair("key: '{_name1} {name_2}'")), []);
});
for (const [label, fr, en, message] of [
  ['missing EN key', "one: 'One', two: 'Two'", "one: 'One'", /fr.two: missing in en/],
  ['extra EN key', "one: 'One'", "one: 'One', two: 'Two'", /en.two: missing in fr/],
  ['different equal-size key sets', "one: 'One'", "two: 'Two'", /missing in/],
  ['case-sensitive keys', "key: 'One'", "Key: 'One'", /missing in/],
  ['placeholder name', "key: '{zone}'", "key: '{code}'", /names or multiplicity/],
  ['placeholder case', "key: '{zone}'", "key: '{Zone}'", /names or multiplicity/],
  ['missing placeholder', "key: '{zone}'", "key: 'Zone'", /names or multiplicity/],
  ['extra placeholder', "key: 'Zone'", "key: '{zone}'", /names or multiplicity/],
  ['placeholder multiplicity', "key: '{zone} {zone}'", "key: '{zone}'", /names or multiplicity/],
]) {
  test(`pair rejects ${label}`, () => assert.match(checkPair(pair(fr, en)).join('\n'), message));
}
for (const malformed of ['{', '}', '{zone', 'zone}', '{}', '{{zone}}', '{zone-name}', '{ zone }', '{count, plural, one {box}}']) {
  for (const language of ['fr', 'en']) {
    test(`pair rejects malformed ${language} braces ${JSON.stringify(malformed)}`, () => {
      const catalogs = pair("key: 'Valid'");
      catalogs[language].set('key', malformed);
      assert.match(checkPair(catalogs).join('\n'), new RegExp(`${language}\\.key: Malformed placeholder`));
    });
  }
}

test('all approved application decisions and single permission invitations pass', () => {
  assert.deepEqual(checkDecisions(decisionFixture()), []);
});
for (const language of ['fr', 'en']) {
  for (const key of Object.keys(approved[language])) {
    for (const mutation of ['changed', 'missing']) {
      test(`protects ${language}.${key} against ${mutation} approved wording`, () => {
        const catalogs = decisionFixture();
        if (mutation === 'missing') catalogs[language].delete(key);
        else catalogs[language].set(key, 'Unapproved replacement');
        assert.ok(checkDecisions(catalogs).includes(`${language}.${key}: approved application wording changed`));
      });
    }
  }
  for (const key of permissionKeys) {
    for (const [label, value] of [
      ['missing key', undefined], ['missing invitation', 'Refusal.'],
      ['duplicate invitation', `Refusal. ${contactSentence[language]} ${contactSentence[language]}`],
      ['nonterminal invitation', `Refusal. ${contactSentence[language]} More text.`],
      ['invitation without separating space', `Refusal.${contactSentence[language]}`],
    ]) {
      test(`permission ${language}.${key} rejects ${label}`, () => {
        const catalogs = decisionFixture();
        if (value === undefined) catalogs[language].delete(key);
        else catalogs[language].set(key, value);
        assert.ok(checkDecisions(catalogs).some(error => error.startsWith(`${language}.${key}: must end`)));
      });
    }
  }
}

test('advisory exclusions and contextual vocabulary do not become global bans', () => {
  const catalogs = decisionFixture();
  for (const language of ['fr', 'en']) {
    for (const [key, reason] of Object.entries(advisoryExclusions)) {
      assert.ok(reason.length > 0);
      catalogs[language].set(key, 'Organization Subculture Measurement');
    }
  }
  assert.deepEqual(checkDecisions(catalogs), []);
  assert.deepEqual(checkPair(pair("key: 'Organization Subculture Measurement'")), []);
});

test('real FR/EN catalogs satisfy AST, parity, placeholders and reviewed wording', () => {
  const catalogs = Object.fromEntries(['fr', 'en'].map(language => [language,
    parseCatalog(readFileSync(new URL(`../src/i18n/${language}.ts`, import.meta.url), 'utf8'), language),
  ]));
  assert.deepEqual([...checkPair(catalogs), ...checkDecisions(catalogs)], []);
});
