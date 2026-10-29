import { readdirSync, readFileSync } from 'node:fs';
import { posix, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

// Exact keys only. These are retention decisions, not evidence of source use.
export const usageExceptions = Object.freeze({
  auditSummaryAlertResolved: 'B03/B06 preservation decision: retain the legacy alert summary; current audit rendering uses auditDescriptionAlertResolved.',
  adminTransferText: 'B01 deferred transfer introduction (register T012); B03/B06 explicitly preserve it pending a transfer-screen decision.',
  manualTemperatureForbidden: 'B02 deferred legacy temperature refusal with no source usage; B03/B06 explicitly preserve it pending permission-copy review.',
});

export function isUsageSource(filename) {
  const path = filename.replaceAll('\\', '/');
  const parts = path.split('/');
  return parts[0] === 'src' && /\.(?:[cm]?[jt]sx?)$/.test(path)
    && !/\.d\.[cm]?ts$/.test(path)
    && parts[1] !== 'i18n'
    && !parts.some(part => /^(?:__tests__|__mocks__|__fixtures__|tests?|fixtures?)$/i.test(part))
    && !/^(?:test-|test\.)|\.(?:test|spec)\.[cm]?[jt]sx?$/i.test(parts.at(-1));
}

function parse(filename, source) {
  const file = ts.createSourceFile(filename, source, ts.ScriptTarget.Latest, true);
  if (file.parseDiagnostics.length) {
    const diagnostic = file.parseDiagnostics[0];
    const { line, character } = file.getLineAndCharacterOfPosition(diagnostic.start ?? 0);
    throw new Error(`${filename}:${line + 1}:${character + 1}: invalid source: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, ' ')}`);
  }
  return file;
}

function unwrap(node) {
  while (node && (ts.isParenthesizedExpression(node) || ts.isAsExpression(node)
    || ts.isTypeAssertionExpression(node) || ts.isSatisfiesExpression(node))) node = node.expression;
  return node;
}

export function catalogKeys(filename, source, language) {
  const file = parse(filename, source);
  const declarations = file.statements.filter(ts.isVariableStatement)
    .filter(statement => statement.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)
      && (statement.declarationList.flags & ts.NodeFlags.Const))
    .flatMap(statement => [...statement.declarationList.declarations])
    .filter(node => ts.isIdentifier(node.name) && node.name.text === language);
  const object = declarations.length === 1 && unwrap(declarations[0].initializer);
  if (!object || !ts.isObjectLiteralExpression(object)) {
    throw new Error(`${filename}: expected one exported const ${language} object literal; update the detector for a new catalog shape`);
  }
  const keys = new Set();
  for (const property of object.properties) {
    if (!ts.isPropertyAssignment(property)
      || (!ts.isIdentifier(property.name) && !ts.isStringLiteral(property.name))) {
      throw new Error(`${filename}: unsupported catalog property; cannot safely inventory keys`);
    }
    const key = property.name.text;
    if (keys.has(key)) throw new Error(`${filename}: duplicate catalog key ${key}`);
    keys.add(key);
  }
  if (!keys.size) throw new Error(`${filename}: empty catalog; refusing an empty inventory`);
  return keys;
}

// Intentionally no binding/data-flow inference: exact literals cover maps,
// conditional expressions and literal unions; property accesses cover catalog aliases.
export function collectUsage(sources, keys) {
  const references = new Map();
  const files = [];
  for (const [filename, source] of sources) {
    if (!isUsageSource(filename)) continue;
    files.push(filename);
    const file = parse(filename, source);
    function record(key, node) {
      if (!keys.has(key)) return;
      const { line, character } = file.getLineAndCharacterOfPosition(node.getStart(file));
      const locations = references.get(key) ?? [];
      locations.push(`${filename}:${line + 1}:${character + 1}`);
      references.set(key, locations);
    }
    function visit(node) {
      if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) record(node.text, node);
      if (ts.isPropertyAccessExpression(node)) record(node.name.text, node.name);
      ts.forEachChild(node, visit);
    }
    visit(file);
  }
  return { references, files: files.sort() };
}

export function analyzeUsage(catalogs, sources, exceptions = usageExceptions) {
  const keys = new Set([...catalogs.fr, ...catalogs.en]);
  const { references, files } = collectUsage(sources, keys);
  const orphans = [...keys].filter(key => !references.has(key)).sort();
  const errors = [];
  if (!files.length) errors.push('No non-test src files scanned; check the source root and exclusions.');
  for (const key of [...keys].sort()) {
    for (const language of ['fr', 'en']) {
      if (!catalogs[language].has(key)) errors.push(`${language}.${key}: missing catalog key; restore FR/EN parity.`);
    }
  }
  for (const [key, reason] of Object.entries(exceptions)) {
    if (typeof reason !== 'string' || !reason.trim()) errors.push(`${key}: exception requires a concrete retention reason.`);
    if (!keys.has(key)) errors.push(`${key}: stale exception: key is absent from catalogs; remove the exception.`);
    else if (references.has(key)) errors.push(`${key}: stale exception: source reference at ${references.get(key)[0]}; remove the exception.`);
  }
  for (const key of orphans) {
    if (!Object.hasOwn(exceptions, key)) errors.push(`${key}: no exact literal or property-access reference in non-test src. Review dynamic consumers before removal; retain only with an explicit, justified exact-key exception. Never delete catalogs automatically.`);
  }
  return { errors, orphans, references, files, counts: { fr: catalogs.fr.size, en: catalogs.en.size } };
}

export function checkRepository(frontendDirectory = fileURLToPath(new URL('../', import.meta.url))) {
  const catalogs = Object.fromEntries(['fr', 'en'].map(language => [language,
    catalogKeys(`src/i18n/${language}.ts`, readFileSync(resolve(frontendDirectory, 'src', 'i18n', `${language}.ts`), 'utf8'), language),
  ]));
  const sources = new Map();
  function walk(directory) {
    for (const entry of readdirSync(resolve(frontendDirectory, directory), { withFileTypes: true })) {
      const filename = posix.join(directory, entry.name);
      if (entry.isDirectory()) {
        if (filename !== 'src/i18n') walk(filename);
      } else if (entry.isFile() && isUsageSource(filename)) {
        sources.set(filename, readFileSync(resolve(frontendDirectory, filename), 'utf8'));
      }
    }
  }
  walk('src');
  return { ...analyzeUsage(catalogs, sources), catalogs };
}
