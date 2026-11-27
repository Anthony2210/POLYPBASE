import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  frontendDirectory,
  isInventoryPath,
  parseSections,
  readSections,
  sectionsDirectory,
  sha256,
  statusDirectory,
} from './i18n-sections.mjs';

// Product owner approval, 2026-11-27. Each fingerprint binds the exact EN
// source, previous JA, and reviewer-supplied JA; a later edit is not approved.
const approvedJapaneseRevisions = new Map([
  [
    'analysisTabLineage',
    '54a307bb613a0d10d97afb8179b1b19611a97009463e7d37a4348268551d56f2',
  ],
  [
    'lastMeasurement',
    'e6fb0a3a2a1aae3251490ddf33f5a56512a0d654df7573b1af313a6ff58c8e14',
  ],
  [
    'overviewByZone',
    '340b882dca7fc0f59cc8564b6e70657720f4ab4ca0c00eb85dc4f79c50c8aae0',
  ],
  [
    'overviewZoneUpToDate',
    '0bb2db5c6c89a479a7af8af7762fb67aa7e9b4a8ed05f3b05ed28a6f7c245efa',
  ],
  [
    'weeklyDueNow',
    'd6bc747eaf61098a070c46ae91a37b6fd067384b19702b5374a10207463bfc52',
  ],
]);

function isApprovedJapaneseRevision(key, before, after, status) {
  const fingerprint = approvedJapaneseRevisions.get(key);
  return (
    fingerprint !== undefined &&
    before.en === after.en &&
    status.reviewer === 'japanese-reviewer' &&
    status.source_sha256 === sha256(after.en) &&
    fingerprint === sha256(JSON.stringify([after.en, before.ja, after.ja]))
  );
}

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
      const status = after.statuses?.[language];
      if (!status) {
        errors.push(
          `${label}: changed validated value has no status record; set it to ai`,
        );
      } else if (
        status.status !== 'ai' &&
        !(
          language === 'ja' &&
          isApprovedJapaneseRevision(key, before.values, after.values, status)
        )
      ) {
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
