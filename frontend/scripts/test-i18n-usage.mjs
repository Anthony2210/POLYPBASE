import assert from 'node:assert/strict';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import {
  analyzeUsage, catalogKeys, checkRepository, collectUsage, isUsageSource, usageExceptions,
} from './i18n-usage.mjs';

const catalogs = (...keys) => ({ fr: new Set(keys), en: new Set(keys) });
const analyze = (keys, source, exceptions = {}) => analyzeUsage(catalogs(...keys), new Map([['src/component.tsx', source]]), exceptions);

for (const [label, source] of [
  ['translator call', "t('used')"],
  ['escaped string', "t('u\\u0073ed')"],
  ['template literal', 't(`used`)'],
  ['literal union', "type Key = 'used' | 'other';"],
  ['conditional', "t(flag ? 'used' : 'other')"],
  ['dynamic literal map', "const keys = { code: 'used' } as const; t(keys[code]);"],
  ['dynamic tuple', "const rules = [['Legacy prefix', 'used']] as const; t(rules[index][1]);"],
  ['function return', "function key(code: string) { return code ? 'used' : 'other'; } t(key(code));"],
  ['catalog property alias', 'const text = translations[language]; text.used;'],
  ['optional property access', 'text?.used;'],
  ['catalog indexed access', "translations[language]['used'];"],
]) {
  test(`counts ${label}`, () => assert.deepEqual(analyze(['used'], source).errors, []));
}

test('source locations are actionable and stable', () => {
  const result = analyze(['used'], "\nt('used'); text.used;");
  assert.deepEqual(result.references.get('used'), ['src/component.tsx:2:3', 'src/component.tsx:2:17']);
});

for (const [label, source] of [
  ['comment', "// t('missing')"],
  ['block comment', "/* text.missing */"],
  ['ordinary identifier', 'const missing = 1;'],
  ['partial literal', "t('missingSuffix')"],
  ['case mismatch', "t('Missing')"],
  ['string concatenation', "t('miss' + 'ing')"],
  ['template construction', 't(`miss${suffix}`)'],
]) {
  test(`does not infer use from ${label}`, () => {
    const result = analyze(['missing'], source);
    assert.deepEqual(result.orphans, ['missing']);
    assert.match(result.errors.join('\n'), /missing: no exact literal or property-access reference/);
  });
}

for (const filename of [
  'scripts/test-usage.mjs', 'src/i18n/fr.ts', 'src/i18n/en.ts', 'src/i18n/index.ts',
  'src/__tests__/component.ts', 'src/components/__mocks__/component.ts', 'src/tests/component.ts',
  'src/test/component.ts', 'src/fixtures/example.ts', 'src/__fixtures__/example.ts',
  'src/components/a.test.tsx', 'src/components/a.spec.ts', 'src/test-component.ts',
  'src/test.component.ts', 'src/keys.d.ts', 'docs/example.ts',
]) {
  test(`excludes test/catalog/non-source evidence in ${filename}`, () => {
    assert.equal(isUsageSource(filename), false);
    const result = analyzeUsage(catalogs('unused'), new Map([
      ['src/production.ts', 'export {};'], [filename, "t('unused');"],
    ]), {});
    assert.deepEqual(result.orphans, ['unused']);
  });
}

test('includes unmounted source files, nested directories and Windows paths', () => {
  const sources = new Map([
    ['src/components/Unmounted.tsx', "t('retained');"],
    ['src/utils/nested/key.ts', "type Key = 'union';"],
    ['src\\legacy\\panel.jsx', 'text.direct;'],
  ]);
  assert.deepEqual(analyzeUsage(catalogs('retained', 'union', 'direct'), sources, {}).errors, []);
});

test('catalog parser reads identifiers, quoted keys and wrappers without executing source', () => {
  assert.deepEqual([...catalogKeys('fr.ts', "export const fr = ({ used: 'Value', 'quoted': dangerous() } as const) satisfies Catalog;", 'fr')], ['used', 'quoted']);
});
for (const source of [
  "export const fr = { ...other };", "export const fr = { ['key']: 'Value' };",
  "export const fr = { key: 'One', key: 'Two' };", 'export const fr = {};',
  'export const fr = values;', 'export const fr = { broken: ; };',
]) {
  test(`fails closed on unsupported catalog ${source}`, () => assert.throws(() => catalogKeys('fr.ts', source, 'fr'), /fr.ts:/));
}

test('fails on malformed production syntax but ignores malformed tests', () => {
  assert.throws(() => analyze(['key'], 'const value = ;'), /src\/component.tsx:1:\d+: invalid source/);
  assert.doesNotThrow(() => collectUsage(new Map([['src/a.test.ts', 'const value = ;']]), new Set()));
});

test('fails closed on no scanned sources and mismatched catalogs', () => {
  assert.match(analyzeUsage(catalogs('key'), new Map(), {}).errors.join('\n'), /No non-test src files/);
  const result = analyzeUsage({ fr: new Set(['key']), en: new Set() }, new Map([['src/a.ts', "t('key')"]]), {});
  assert.match(result.errors.join('\n'), /en.key: missing catalog key/);
});

test('accepts only exact, justified exceptions and still reports retained orphans', () => {
  const result = analyze(['retained'], 'export {};', { retained: 'Explicitly deferred pending product review.' });
  assert.deepEqual(result.errors, []);
  assert.deepEqual(result.orphans, ['retained']);
  assert.match(analyze(['retained'], 'export {};', { retained: ' ' }).errors.join('\n'), /requires a concrete retention reason/);
  assert.match(analyze(['csvUnused'], 'export {};', { 'csv*': 'Not a supported pattern.' }).errors.join('\n'), /csvUnused: no exact literal/);
});

test('rejects stale exceptions after deletion or a new production reference', () => {
  assert.match(analyze([], 'export {};', { old: 'Previously retained.' }).errors.join('\n'), /old: stale exception: key is absent/);
  assert.match(analyze(['old'], "t('old')", { old: 'Previously retained.' }).errors.join('\n'), /old: stale exception: source reference at src\/component.tsx:1:3/);
});

test('test-only references cannot make an exception stale or make an orphan used', () => {
  const result = analyzeUsage(catalogs('retained', 'orphan'), new Map([
    ['src/production.ts', 'export {};'], ['src/production.test.ts', "t('retained'); t('orphan');"],
  ]), { retained: 'Explicit retention decision.' });
  assert.equal(result.errors.length, 1);
  assert.match(result.errors[0], /^orphan: no exact literal/);
});

test('documented conservative limitation: unused maps and unrelated exact properties retain keys', () => {
  assert.deepEqual(analyze(['key'], "const neverRead = { label: 'key' };" ).errors, []);
  assert.deepEqual(analyze(['key'], 'unrelatedObject.key;').errors, []);
});

test('repository traversal includes unmounted components but not catalogs or tests', () => {
  const directory = mkdtempSync(join(tmpdir(), 'polypbase-i18n-usage-'));
  try {
    mkdirSync(join(directory, 'src', 'i18n'), { recursive: true });
    mkdirSync(join(directory, 'src', 'components'), { recursive: true });
    const keys = [...Object.keys(usageExceptions), 'live', 'testOnly'];
    for (const language of ['fr', 'en']) {
      writeFileSync(join(directory, 'src', 'i18n', `${language}.ts`),
        `export const ${language} = ${JSON.stringify(Object.fromEntries(keys.map(key => [key, key])))};`);
    }
    writeFileSync(join(directory, 'src', 'components', 'Unmounted.tsx'), "t('live');");
    writeFileSync(join(directory, 'src', 'components', 'Unmounted.test.tsx'), "t('testOnly');");
    const result = checkRepository(directory);
    assert.deepEqual(result.files, ['src/components/Unmounted.tsx']);
    assert.equal(result.errors.length, 1);
    assert.match(result.errors[0], /^testOnly: no exact literal/);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
});

test('real source has no unreviewed orphans or stale exceptions', () => {
  const result = checkRepository();
  assert.deepEqual(result.errors, [], result.errors.join('\n'));
  for (const filename of [
    'src/utils/auditPresentation.ts', 'src/utils/biologicalTimeline.ts',
    'src/components/AccountInvitationsPanel.tsx', 'src/components/AdminView.tsx',
    'src/components/OverviewView.tsx', 'src/components/ZonesView.tsx',
  ]) assert.ok(result.files.includes(filename), `Consumer must be scanned: ${filename}`);
  for (const key of Object.keys(usageExceptions)) {
    assert.ok(result.catalogs.fr.has(key) && result.catalogs.en.has(key), `Protected key missing: ${key}`);
  }
  const csvKeys = [...result.catalogs.fr].filter(key => key.startsWith('csv'));
  assert.ok(csvKeys.length >= 132, 'Preserve the 132 baseline CSV keys (no CSV exemption).');
  for (const key of csvKeys) assert.ok(result.references.has(key), `CSV key requires real source evidence: ${key}`);
});
