import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import ts from 'typescript';

const root = fileURLToPath(new URL('../', import.meta.url));
const fixturePath = resolve(root, 'src/__i18n_contract_fixture.ts');
const configPath = resolve(root, 'tsconfig.json');
const config = ts.readConfigFile(configPath, ts.sys.readFile);
assert.equal(config.error, undefined);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root);
assert.deepEqual(parsed.errors, []);

function diagnostics(source, overrides = new Map()) {
  const files = new Map([[fixturePath, source], ...overrides]);
  const host = ts.createCompilerHost(parsed.options);
  const read = host.readFile.bind(host);
  const exists = host.fileExists.bind(host);
  host.readFile = path => files.get(resolve(path)) ?? read(path);
  host.fileExists = path => files.has(resolve(path)) || exists(path);
  const program = ts.createProgram([fixturePath], parsed.options, host);
  return ts.getPreEmitDiagnostics(program);
}

const imports = `
import { createTranslator, type TranslationKey } from './i18n';
import AccountInvitationsPanel from './components/AccountInvitationsPanel';
import AdminView from './components/AdminView';
import OverviewView from './components/OverviewView';
import { ZonesView, ZoneDetailPage, ZoneBoxesPage } from './components/ZonesView';
import { getAuditMetadataKeyLabel, AUDIT_FAMILY_PRESENTATION } from './utils/auditPresentation';
import { getBiologicalTimelineLabels } from './utils/biologicalTimeline';
`;
const translators = [
  ['invitation', "Parameters<typeof AccountInvitationsPanel>[0]['t']"],
  ['admin', "Parameters<typeof AdminView>[0]['t']"],
  ['overview', "Parameters<typeof OverviewView>[0]['t']"],
  ['zones', "Parameters<typeof ZonesView>[0]['t']"],
  ['zoneDetail', "Parameters<typeof ZoneDetailPage>[0]['t']"],
  ['zoneBoxes', "Parameters<typeof ZoneBoxesPage>[0]['t']"],
  ['audit', 'Parameters<typeof getAuditMetadataKeyLabel>[1]'],
  ['biology', 'Parameters<typeof getBiologicalTimelineLabels>[0]'],
];

const positive = `${imports}
const t = createTranslator('en');
t('polyps');
const keys = { temperature: 'temperatureShort', salinity: 'salinityFull' } satisfies Record<string, TranslationKey>;
declare const variant: keyof typeof keys;
t(keys[variant]);
t(variant === 'temperature' ? 'temperatureShort' : 'salinityFull');
t(AUDIT_FAMILY_PRESENTATION.measurements.labelKey);
getAuditMetadataKeyLabel('unknown_backend_field', t);
getBiologicalTimelineLabels(t);
${translators.map(([name, type]) => `const ${name}: ${type} = t; ${name}(keys[variant]);`).join('\n')}
`;
const negative = [
  "t('__invalid_translation_key__');",
  ...translators.map(([name]) => `${name}('__invalid_translation_key__');`),
  'declare const unknownBackendKey: string;',
  't(unknownBackendKey);',
  ...translators.map(([name]) => `${name}(unknownBackendKey);`),
  "const invalidMap = { hidden: '__invalid_translation_key__' } satisfies Record<string, TranslationKey>;",
  "t(({ hidden: '__invalid_translation_key__' } as const).hidden);",
];

test('real translator paths accept literal/mapped variants and reject invalid or unknown strings', () => {
  const source = positive + '\n' + negative.join('\n');
  const expectedLines = negative.flatMap((line, index) => line.startsWith('declare ') ? [] : [positive.split('\n').length + index + 1]);
  const actual = diagnostics(source);
  const details = ts.formatDiagnosticsWithColorAndContext(actual, {
    getCanonicalFileName: path => path, getCurrentDirectory: () => root, getNewLine: () => '\n',
  });
  assert.ok(actual.every(item => item.file && resolve(item.file.fileName) === fixturePath), details);
  assert.ok(actual.every(item => [2322, 2345].includes(item.code)), details);
  const actualLines = actual.map(item => item.file.getLineAndCharacterOfPosition(item.start).line + 1);
  assert.deepEqual(actualLines, expectedLines, details);
});

test('invalid key hidden inside the actual audit literal map fails TypeScript', () => {
  const path = resolve(root, 'src/utils/auditPresentation.ts');
  const source = readFileSync(path, 'utf8');
  const mutated = source.replace("entry: 'auditActionEntry'", "entry: '__invalid_translation_key__'");
  assert.notEqual(mutated, source, 'The real action map must be exercised.');
  const actual = diagnostics(positive, new Map([[path, mutated]]));
  assert.equal(actual.length, 1, ts.formatDiagnostics(actual, {
    getCanonicalFileName: path => path, getCurrentDirectory: () => root, getNewLine: () => '\n',
  }));
  assert.equal(resolve(actual[0].file.fileName), path);
  assert.equal(actual[0].code, 2322);
  assert.match(ts.flattenDiagnosticMessageText(actual[0].messageText, '\n'), /__invalid_translation_key__/);
});
