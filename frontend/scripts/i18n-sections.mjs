import { createHash } from 'node:crypto';
import { readdirSync, readFileSync } from 'node:fs';
import { posix, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

export const frontendDirectory = fileURLToPath(new URL('../', import.meta.url));
export const sectionsDirectory = 'src/i18n/sections';
export const statusDirectory = 'src/i18n/status';
export const sha256 = (value) =>
  createHash('sha256').update(value, 'utf8').digest('hex');

// The static inventory is independent of the runtime aggregate. Consumers can
// compare inventory.entries against the runtime catalogs to catch omitted sections.
// entries: Map<key, { key, path, line, values: { fr, en, ja? }, statuses: { en, ja? } }>
// sections: Map<slug, { path, entries: Map<key, entry> }>
// Each sections/<slug>.ts has status/<slug>.json, keyed by translation key.
// Status records: { status, source_sha256, reviewer? }.
export function readSections(directory = frontendDirectory) {
  const files = new Map();
  for (const parent of [sectionsDirectory, statusDirectory]) {
    for (const entry of readdirSync(resolve(directory, parent), {
      withFileTypes: true,
    })) {
      const path = posix.join(parent, entry.name);
      if (!isInventoryPath(path)) continue;
      if (!entry.isFile()) throw new Error(`${path}: expected a regular file`);
      files.set(path, readFileSync(resolve(directory, path), 'utf8'));
    }
  }
  return parseSections(files);
}

const roles = { en: 'english-reviewer', ja: 'japanese-reviewer' };
const sourceLanguages = { en: 'fr', ja: 'en' };
const digestPattern = /^[0-9a-f]{64}$/;
export function isInventoryPath(path) {
  const parent = posix.dirname(path);
  return (
    (parent === sectionsDirectory &&
      path !== `${sectionsDirectory}/index.ts` &&
      path.endsWith('.ts')) ||
    (parent === statusDirectory && path.endsWith('.json'))
  );
}

function readProperties(node, fail) {
  if (!node || !ts.isObjectLiteralExpression(node))
    fail(node, 'expected an object literal');
  const properties = new Map();
  for (const property of node.properties) {
    if (
      !ts.isPropertyAssignment(property) ||
      (!ts.isIdentifier(property.name) && !ts.isStringLiteral(property.name))
    ) {
      fail(property, 'expected a static property assignment');
    }
    const name = property.name.text;
    if (!name || name === '__proto__')
      fail(property, `invalid property name ${JSON.stringify(name)}`);
    if (properties.has(name)) fail(property, `duplicate property ${name}`);
    properties.set(name, property);
  }
  return properties;
}

function placeholders(value, fail) {
  const names = [];
  const remainder = value.replace(
    /\{([A-Za-z_][A-Za-z0-9_]*)\}/g,
    (_, name) => {
      names.push(name);
      return '';
    },
  );
  if (/[{}]/.test(remainder))
    fail('malformed placeholder braces (expected {name})');
  return JSON.stringify(names.sort());
}

function parseSection(path, source, exportName) {
  const file = ts.createSourceFile(
    path,
    source,
    ts.ScriptTarget.Latest,
    true,
    ts.ScriptKind.TS,
  );
  const lineOf = (node) =>
    file.getLineAndCharacterOfPosition(node?.getStart(file) ?? 0).line + 1;
  function fail(node, message) {
    throw new Error(`${path}:${lineOf(node)}: ${message}`);
  }
  if (file.parseDiagnostics.length) {
    const diagnostic = file.parseDiagnostics[0];
    const line =
      file.getLineAndCharacterOfPosition(diagnostic.start ?? 0).line + 1;
    throw new Error(
      `${path}:${line}: invalid TypeScript: ${ts.flattenDiagnosticMessageText(diagnostic.messageText, ' ')}`,
    );
  }
  const [statement] = file.statements;
  const declaration =
    statement &&
    ts.isVariableStatement(statement) &&
    file.statements.length === 1 &&
    statement.modifiers?.length === 1 &&
    statement.modifiers[0].kind === ts.SyntaxKind.ExportKeyword &&
    (statement.declarationList.flags & ts.NodeFlags.BlockScoped) ===
      ts.NodeFlags.Const &&
    statement.declarationList.declarations.length === 1 &&
    statement.declarationList.declarations[0];
  if (
    !declaration ||
    !ts.isIdentifier(declaration.name) ||
    declaration.name.text !== exportName ||
    declaration.type
  ) {
    fail(statement, `expected only export const ${exportName} = { ... }`);
  }
  const entries = new Map();
  for (const [key, property] of readProperties(declaration.initializer, fail)) {
    const values = {};
    let expected;
    const languages = readProperties(property.initializer, fail);
    if (!languages.has('fr') || !languages.has('en'))
      fail(property, `${key}: fr and en are required`);
    for (const language of ['fr', 'en', 'ja']) {
      const translation = languages.get(language);
      if (!translation) continue;
      const value = translation.initializer;
      const invalid = (message) =>
        fail(translation, `${key}.${language}: ${message}`);
      if (!ts.isStringLiteral(value)) invalid('expected a string literal');
      if (!value.text) invalid('expected a nonempty string');
      if (value.text.includes('\u00b7'))
        invalid('U+00B7 middle dot is not allowed');
      const actual = placeholders(value.text, invalid);
      if (language === 'fr') expected = actual;
      else if (actual !== expected)
        invalid('placeholder names or multiplicity differ from fr');
      values[language] = value.text;
    }
    for (const [language, translation] of languages) {
      if (!Object.hasOwn(values, language))
        fail(translation, `${key}: unknown language ${language}`);
    }
    entries.set(key, { key, path, line: lineOf(property), values });
  }
  return { path, entries };
}

function readStatus(path, source) {
  let status;
  try {
    status = JSON.parse(source);
  } catch (error) {
    throw new Error(`${path}: invalid JSON: ${error.message}`, {
      cause: error,
    });
  }
  // JSON.parse discards duplicate keys. Walk the original syntax to reject them.
  const file = ts.parseJsonText(path, source);
  function visit(node) {
    if (ts.isObjectLiteralExpression(node))
      readProperties(node, (property, message) => {
        const { line } = file.getLineAndCharacterOfPosition(
          property.getStart(file),
        );
        throw new Error(`${path}:${line + 1}: ${message}`);
      });
    ts.forEachChild(node, visit);
  }
  visit(file);
  return status;
}

function requireFields(value, required, path) {
  if (!value || typeof value !== 'object' || Array.isArray(value))
    throw new Error(`${path}: expected an object`);
  for (const field of required) {
    if (!Object.hasOwn(value, field))
      throw new Error(`${path}: missing ${field}`);
  }
  for (const field of Object.keys(value)) {
    if (!required.includes(field))
      throw new Error(`${path}: unexpected ${field}`);
  }
}

export function isStaleReview(entry, language) {
  return (
    entry.statuses[language].source_sha256 !==
    sha256(entry.values[sourceLanguages[language]])
  );
}

function attachStatuses(section, path, source, warnings) {
  const records = readStatus(path, source);
  requireFields(records, [...section.entries.keys()], path);
  for (const entry of section.entries.values()) {
    const location = `${path}: ${entry.key}`;
    const languages = Object.hasOwn(entry.values, 'ja') ? ['en', 'ja'] : ['en'];
    entry.statuses = records[entry.key];
    requireFields(entry.statuses, languages, location);
    for (const language of languages) {
      const record = entry.statuses[language];
      const label = `${location}.${language}`;
      if (record?.status !== 'ai' && record?.status !== 'validated') {
        throw new Error(`${label}: status must be ai or validated`);
      }
      const fields = ['status', 'source_sha256'];
      if (record.status === 'validated') fields.push('reviewer');
      requireFields(record, fields, label);
      if (
        typeof record.source_sha256 !== 'string' ||
        !digestPattern.test(record.source_sha256)
      ) {
        throw new Error(
          `${label}: source_sha256 must be a lowercase SHA-256 hex digest`,
        );
      }
      if (
        record.status === 'validated' &&
        record.reviewer !== roles[language]
      ) {
        throw new Error(`${label}: expected reviewer ${roles[language]}`);
      }
      if (isStaleReview(entry, language))
        warnings.push(`${label}: stale source_sha256`);
    }
  }
}

/** Parse frontend-relative POSIX paths without executing any source modules. */
export function parseSections(files) {
  const sections = new Map();
  const entries = new Map();
  const warnings = [];
  const paths = [...files.keys()].filter(isInventoryPath).sort();
  for (const path of paths.filter((path) => path.endsWith('.ts'))) {
    const slug = posix.basename(path, '.ts');
    if (!/^[a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*$/.test(slug))
      throw new Error(`${path}: expected a kebab-case section slug`);
    const exportName = slug.replace(/-([a-z])/g, (_, letter) =>
      letter.toUpperCase(),
    );
    const section = parseSection(path, files.get(path), exportName);
    const statusPath = `${statusDirectory}/${slug}.json`;
    if (!files.has(statusPath))
      throw new Error(`${path}: missing sidecar ${statusPath}`);
    attachStatuses(section, statusPath, files.get(statusPath), warnings);
    for (const [key, entry] of section.entries) {
      const previous = entries.get(key);
      if (previous)
        throw new Error(
          `${path}:${entry.line}: duplicate key ${key}, already in ${previous.path}:${previous.line}`,
        );
      entries.set(key, entry);
    }
    sections.set(slug, section);
  }
  for (const path of paths.filter((path) => path.endsWith('.json'))) {
    if (!sections.has(posix.basename(path, '.json')))
      throw new Error(`${path}: sidecar without a section`);
  }
  if (!sections.size)
    throw new Error(`${sectionsDirectory}: no section files found`);
  return { entries, sections, warnings };
}
