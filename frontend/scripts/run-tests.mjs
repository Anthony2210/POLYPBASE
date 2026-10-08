import { spawnSync } from 'node:child_process';
import { readdirSync, readFileSync } from 'node:fs';
import { basename, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

export function discoverTestEntries(directory) {
  const files = readdirSync(directory, { withFileTypes: true })
    .filter(entry => entry.isFile() && /^test-.*\.mjs$/.test(entry.name))
    .map(entry => entry.name).sort();
  if (!files.length) throw new Error(`No test modules found in ${directory}`);

  const imports = new Map();
  const imported = new Set();
  for (const file of files) {
    const source = ts.createSourceFile(file, readFileSync(resolve(directory, file), 'utf8'),
      ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
    const dependencies = new Set();
    for (const statement of source.statements) {
      if (!ts.isImportDeclaration(statement) && !ts.isExportDeclaration(statement)) continue;
      const specifier = statement.moduleSpecifier;
      if (!specifier || !ts.isStringLiteral(specifier)) continue;
      const name = basename(specifier.text);
      if (!/^test-.*\.mjs$/.test(name)) continue;
      if (specifier.text !== `./${name}` || !files.includes(name)) {
        throw new Error(`${file}: test import must name a discovered sibling module: ${specifier.text}`);
      }
      dependencies.add(name);
      imported.add(name);
    }
    imports.set(file, dependencies);
  }

  // Node isolates entry points in separate processes. Imported tests must stay
  // in their existing bundle, or those tests would register in multiple workers.
  const entries = files.filter(file => !imported.has(file));
  const owners = new Map();
  for (const entry of entries) {
    const visited = new Set();
    function visit(file) {
      if (visited.has(file)) return;
      visited.add(file);
      if (owners.has(file)) {
        throw new Error(`${file} would execute twice through ${owners.get(file)} and ${entry}`);
      }
      owners.set(file, entry);
      for (const dependency of imports.get(file)) visit(dependency);
    }
    visit(entry);
  }
  const uncovered = files.filter(file => !owners.has(file));
  if (uncovered.length) throw new Error(`Test modules have no entry point (import cycle): ${uncovered.join(', ')}`);
  return { files, entries };
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try {
    const directory = fileURLToPath(new URL('./', import.meta.url));
    const { files, entries } = discoverTestEntries(directory);
    console.log(`Discovered ${files.length} test modules; running ${entries.length} isolated entry points.`);
    const result = spawnSync(process.execPath, [
      '--test', '--test-concurrency=2', '--test-reporter=tap',
      ...entries.map(file => resolve(directory, file)),
    ], { cwd: fileURLToPath(new URL('../', import.meta.url)), stdio: 'inherit' });
    if (result.error) throw result.error;
    if (result.signal) console.error(`Frontend tests terminated by ${result.signal}`);
    process.exitCode = result.status ?? 1;
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}
