import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  frontendDirectory,
  isInventoryPath,
  parseSections,
  readSections,
  sectionsDirectory,
  statusDirectory,
} from './i18n-sections.mjs';

/** Compare values, not source layout or metadata alone, with the reviewed baseline. */
export function validatedChanges(previous, current) {
  const errors = [];
  for (const [key, before] of previous.entries) {
    const after = current.entries.get(key);
    for (const [language, record] of Object.entries(before.statuses)) {
      if (record.status !== 'validated') continue;
      if (before.values[language] === after?.values[language]) continue;
      const location = after ?? before;
      const label = `${location.path}:${location.line}: ${key}.${language}`;
      if (after?.values[language] === undefined) {
        errors.push(`${label}: previously validated value was removed`);
        continue;
      }
      if (after.statuses[language].status !== 'ai') {
        errors.push(`${label}: changed validated value must be set to ai`);
      }
    }
  }
  return errors;
}

export function readMergeBase(directory = frontendDirectory) {
  function git(...args) {
    return execFileSync('git', ['--no-pager', ...args], {
      cwd: directory,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    });
  }
  let base;
  try {
    base = git('merge-base', 'HEAD', 'origin/main').trim();
  } catch (error) {
    throw new Error(
      'Could not resolve the merge base of HEAD and origin/main; fetch origin/main and its history before running the delta check.',
      { cause: error },
    );
  }
  const filenames = git(
    'ls-tree',
    '-r',
    '-z',
    '--name-only',
    base,
    '--',
    sectionsDirectory,
    statusDirectory,
  )
    .split('\0')
    .filter(isInventoryPath);
  const files = new Map(
    filenames.map((path) => [path, git('show', `${base}:./${path}`)]),
  );
  // The initial sections migration has no sidecar approvals on its legacy base.
  return {
    base,
    inventory: files.size ? parseSections(files) : { entries: new Map() },
  };
}

if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  try {
    const args = process.argv.slice(2);
    if (args.length > 1 || (args.length && args[0] !== '--delta'))
      throw new Error('Usage: check-i18n-sections.mjs [--delta]');
    const inventory = readSections();
    for (const warning of inventory.warnings)
      console.warn(`Warning: ${warning}`);
    if (args.length) {
      const { base, inventory: previous } = readMergeBase();
      const errors = validatedChanges(previous, inventory);
      if (errors.length) throw new Error(errors.join('\n'));
      console.log(`Checked validated changes against merge base ${base}.`);
    }
    console.log(
      `i18n sections: ${inventory.sections.size} sections, ${inventory.entries.size} keys, ${inventory.warnings.length} stale reviews.`,
    );
  } catch (error) {
    console.error(`i18n sections check failed: ${error.message}`);
    process.exitCode = 1;
  }
}
