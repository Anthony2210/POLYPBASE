import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { readMergeBase, validatedChanges } from './check-i18n-sections.mjs';
import { findTranslations, formatTranslation } from './i18n-find.mjs';
import {
  parseSections,
  readSections,
  sectionsDirectory,
  sha256,
  statusDirectory,
} from './i18n-sections.mjs';

const pathOf = (name) => `${sectionsDirectory}/${name}`;
const statusPath = (slug) => `${statusDirectory}/${slug}.json`;
const values = { fr: 'Café {x}', en: 'Coffee {x}', ja: 'コーヒー {x}' };
const statuses = (copy, en = 'validated', ja = 'validated') => {
  const record = (source, status, reviewer) => ({
    status,
    source_sha256: sha256(copy[source]),
    ...(status === 'validated' ? { reviewer } : {}),
  });
  return {
    en: record('fr', en, 'english-reviewer'),
    ...(Object.hasOwn(copy, 'ja')
      ? { ja: record('en', ja, 'japanese-reviewer') }
      : {}),
  };
};
const section = (
  slug,
  entries,
  reviews = Object.fromEntries(
    Object.entries(entries).map(([key, copy]) => [key, statuses(copy)]),
  ),
  exportName = slug,
) =>
  new Map([
    [
      pathOf(`${slug}.ts`),
      `export const ${exportName} = ${JSON.stringify(entries, null, 2)};\n`,
    ],
    [statusPath(slug), JSON.stringify(reviews)],
  ]);
const inventory = (copy = values, reviews = statuses(copy), slug = 'labels') =>
  parseSections(section(slug, { coffee: copy }, { coffee: reviews }));

function withTemporaryDirectory(callback) {
  const directory = mkdtempSync(join(tmpdir(), 'polypbase-i18n-sections-'));
  try {
    return callback(directory);
  } finally {
    rmSync(directory, { recursive: true, force: true });
  }
}

function writeSectionFiles(directory, files) {
  mkdirSync(join(directory, sectionsDirectory), { recursive: true });
  mkdirSync(join(directory, statusDirectory), { recursive: true });
  for (const [file, source] of files)
    writeFileSync(join(directory, file), source);
}

test('discovers all sections, even if index.ts omits one, and reports the real declaration line', () =>
  withTemporaryDirectory((directory) => {
    const files = new Map([
      ...section('labels', { coffee: values }),
      ...section('common', {}),
      ...section(
        'table-headers',
        { name: { fr: 'Nom', en: 'Name' } },
        undefined,
        'tableHeaders',
      ),
      [
        pathOf('index.ts'),
        "export { labels } from './labels'; throw new Error('Do not execute');",
      ],
    ]);
    files.set(
      pathOf('labels.ts'),
      "// Layout can change\n\nexport const labels = {\n  coffee:\n    { en: 'Coffee {x}', ja: 'コーヒー {x}', fr: 'Caf\\u00e9 {x}' },\n};\n",
    );
    writeSectionFiles(directory, files);
    const result = readSections(directory);
    assert.deepEqual(
      [...result.sections.keys()],
      ['common', 'labels', 'table-headers'],
    );
    assert.equal(result.sections.get('common').entries.size, 0);
    assert.deepEqual(result.entries.get('coffee'), {
      key: 'coffee',
      path: pathOf('labels.ts'),
      line: 4,
      values,
      statuses: statuses(values),
    });
    assert.deepEqual(result.warnings, []);
  }));

test('finds an exact key or a literal localized fragment and prints missing Japanese as fallback', () => {
  const result = parseSections(
    section('labels', { coffee: values, title: { fr: 'Titre', en: 'Title' } }),
  );
  assert.deepEqual(
    findTranslations(result, 'coffee').map(({ key }) => key),
    ['coffee'],
  );
  assert.deepEqual(
    findTranslations(result, 'CAFÉ').map(({ key }) => key),
    ['coffee'],
  );
  assert.deepEqual(
    findTranslations(result, 'コーヒー').map(({ key }) => key),
    ['coffee'],
  );
  assert.deepEqual(findTranslations(result, '.*'), []);
  const related = parseSections(
    section('labels', { coffee: values, coffeeLabel: values }),
  );
  assert.deepEqual(
    findTranslations(related, 'coffee').map(({ key }) => key),
    ['coffee', 'coffeeLabel'],
  );
  assert.deepEqual(
    formatTranslation(result.entries.get('coffee')).split('\n'),
    [
      'coffee src/i18n/sections/labels.ts:2',
      '  fr: "Café {x}"',
      '  en: "Coffee {x}" [validated: english-reviewer]',
      '  ja: "コーヒー {x}" [validated: japanese-reviewer]',
    ],
  );
  assert.match(
    formatTranslation(result.entries.get('title')),
    /ja: \(missing; falls back to en\)/,
  );
});

for (const [name, source, message] of [
  ['missing export', 'const labels = {};', /expected only export const/],
  [
    'incorrect export name',
    'export const other = {};',
    /expected only export const labels/,
  ],
  ['mutable export', 'export let labels = {};', /expected only export const/],
  [
    'extra statement',
    'throw new Error(); export const labels = {};',
    /expected only export const/,
  ],
  [
    'wrapped object',
    'export const labels = {} as const;',
    /expected an object literal/,
  ],

  [
    'missing fr',
    "export const labels = { coffee: { en: 'Coffee' } };",
    /fr and en are required/,
  ],
  [
    'duplicate language',
    "export const labels = { coffee: { fr: 'Café', en: 'Coffee', 'en': 'Tea' } };",
    /duplicate property en/,
  ],
  [
    'spread',
    'export const labels = { ...other };',
    /static property assignment/,
  ],
  [
    'computed key',
    "export const labels = { ['coffee']: { fr: 'Café', en: 'Coffee' } };",
    /static property assignment/,
  ],
  [
    'call',
    "export const labels = { coffee: { fr: 'Café', en: translate() } };",
    /expected a string literal/,
  ],
  [
    'syntax error',
    'export const labels = { coffee: ; };',
    /invalid TypeScript/,
  ],
  [
    'empty string',
    "export const labels = { coffee: { fr: '', en: 'Coffee' } };",
    /nonempty string/,
  ],
  [
    'extra language',
    "export const labels = { coffee: { fr: 'Café', en: 'Coffee', es: 'Café' } };",
    /unknown language es/,
  ],
  [
    'duplicate key',
    'export const labels = { coffee: {}, coffee: {} };',
    /duplicate property coffee/,
  ],
]) {
  test(`rejects section shape: ${name}`, () => {
    const files = section('labels', { coffee: values });
    files.set(pathOf('labels.ts'), source);
    assert.throws(() => parseSections(files), message);
  });
}

test('placeholders compare names and multiplicity without depending on order', () => {
  const copy = { fr: '{x} {y} {x}', en: '{y} {x} {x}', ja: '{x} {x} {y}' };
  assert.deepEqual(inventory(copy).warnings, []);
  assert.throws(
    () => inventory({ ...copy, en: '{x} {y}' }),
    /coffee.en: placeholder names or multiplicity/,
  );
  assert.throws(
    () => inventory({ ...copy, ja: '{x} {x} {z}' }),
    /coffee.ja: placeholder names or multiplicity/,
  );
  assert.throws(
    () => inventory({ ...copy, fr: '{{x}} {y} {x}' }),
    /malformed placeholder braces/,
  );
});

test('rejects U+00B7 in all languages, including when escaped in TypeScript', () => {
  for (const language of ['fr', 'en', 'ja']) {
    assert.throws(
      () => inventory({ ...values, [language]: `${values[language]}\u00b7` }),
      /U\+00B7/,
    );
  }
  const files = section('labels', { coffee: values });
  files.set(
    pathOf('labels.ts'),
    "export const labels = { coffee: { fr: 'Café {x}', en: 'Coffee\\u00b7 {x}' } };",
  );
  assert.throws(() => parseSections(files), /U\+00B7/);
});

test('enforces the section/sidecar, key, and present-language bijections', () => {
  const files = section('labels', { coffee: values });
  assert.throws(
    () =>
      parseSections(
        new Map([[pathOf('labels.ts'), files.get(pathOf('labels.ts'))]]),
      ),
    /missing sidecar/,
  );
  assert.throws(
    () => parseSections(new Map([...files, [statusPath('orphan'), '{}']])),
    /sidecar without a section/,
  );
  assert.throws(
    () =>
      parseSections(
        new Map([...files, ...section('more', { coffee: values })]),
      ),
    /duplicate key coffee/,
  );
  assert.throws(
    () => parseSections(section('labels', { coffee: values }, {})),
    /missing coffee/,
  );
  assert.throws(
    () =>
      parseSections(
        section(
          'labels',
          { coffee: values },
          { coffee: statuses(values), tea: {} },
        ),
      ),
    /unexpected tea/,
  );
  const noJapanese = { fr: values.fr, en: values.en };
  assert.throws(() => inventory(noJapanese, statuses(values)), /unexpected ja/);
  assert.throws(() => inventory(values, statuses(noJapanese)), /missing ja/);
  const record = JSON.stringify(statuses(values));
  files.set(
    statusPath('labels'),
    `{"coffee":${record},"\\u0063offee":${record}}`,
  );
  assert.throws(() => parseSections(files), /duplicate property coffee/);
});

test('requires valid status, the correct role, and properly shaped fingerprints', () => {
  const review = statuses(values);
  assert.throws(
    () =>
      inventory(values, { ...review, en: { ...review.en, status: 'draft' } }),
    /status must be ai or validated/,
  );
  assert.throws(
    () =>
      inventory(values, {
        ...review,
        en: { ...review.en, reviewer: 'japanese-reviewer' },
      }),
    /expected reviewer english-reviewer/,
  );
  assert.throws(
    () =>
      inventory(values, {
        ...review,
        ja: { ...review.ja, reviewer: 'english-reviewer' },
      }),
    /expected reviewer japanese-reviewer/,
  );
  assert.throws(
    () => inventory(values, { ...review, en: { ...review.en, status: 'ai' } }),
    /unexpected reviewer/,
  );
  assert.throws(
    () =>
      inventory(values, {
        ...review,
        ja: { ...review.ja, source_sha256: 'abc' },
      }),
    /lowercase SHA-256/,
  );
  assert.throws(
    () =>
      inventory(values, {
        ...review,
        ja: { ...review.ja, value_sha256: sha256(values.ja) },
      }),
    /unexpected value_sha256/,
  );
});

test('fingerprints use exact decoded UTF-8, and only a stale source is advisory', () => {
  const copy = { ...values, fr: 'Cafe\u0301 {x}', en: 'COFFEE {x}' };
  const review = statuses(values);
  assert.equal(
    review.en.source_sha256,
    createHash('sha256')
      .update(Buffer.from([67, 97, 102, 195, 169, 32, 123, 120, 125]))
      .digest('hex'),
  );
  const { warnings, entries } = inventory(copy, review);
  assert.deepEqual(warnings, [
    `${statusPath('labels')}: coffee.en: stale source_sha256`,
    `${statusPath('labels')}: coffee.ja: stale source_sha256`,
  ]);
  assert.deepEqual(entries.get('coffee').values, copy);
  assert.deepEqual(entries.get('coffee').statuses, review);
  assert.match(
    formatTranslation(entries.get('coffee')),
    /\[validated: english-reviewer\] \(stale source\)/,
  );
  assert.deepEqual(
    inventory({ ...values, ja: '別の表記 {x}' }, review).warnings,
    [],
  );
  assert.deepEqual(
    validatedChanges(
      inventory(),
      inventory({ ...values, fr: copy.fr }, review),
    ),
    [],
  );
});

test('preserves surrounding whitespace in values and hashes it as-is', () => {
  const copy = {
    fr: ` ${values.fr}\n`,
    en: `\t${values.en} `,
    ja: `${values.ja} `,
  };
  const result = inventory(copy);
  assert.deepEqual(result.entries.get('coffee').values, copy);
  assert.deepEqual(result.warnings, []);
  assert.equal(inventory(copy, statuses(values)).warnings.length, 2);
});

test('changed validated targets must move to ai even if their source hash is fresh', () => {
  const base = inventory();
  const edited = { fr: 'Thé {x}', en: 'Tea {x}', ja: 'お茶 {x}' };
  assert.equal(validatedChanges(base, inventory(edited)).length, 2);
  assert.deepEqual(
    validatedChanges(base, inventory(values, statuses(values), 'moved')),
    [],
  );
  assert.deepEqual(
    validatedChanges(
      inventory(values, statuses(values, 'ai', 'ai')),
      inventory(edited, statuses(edited, 'ai', 'ai')),
    ),
    [],
  );
  assert.deepEqual(
    validatedChanges(base, inventory(edited, statuses(edited, 'ai', 'ai'))),
    [],
  );
  assert.deepEqual(
    validatedChanges(base, inventory(edited, statuses(values, 'ai', 'ai'))),
    [],
  );
  assert.match(
    validatedChanges(
      base,
      inventory(edited, statuses(edited, 'ai', 'validated')),
    ).join('\n'),
    /coffee.ja: changed validated value must be set to ai/,
  );
});

test('changing a validated value without a status record reports a clear error', () => {
  const base = inventory();
  const current = inventory({ ...values, en: 'Tea {x}' });
  const entry = current.entries.get('coffee');
  delete entry.statuses.en;

  assert.deepEqual(validatedChanges(base, current), [
    `${entry.path}:${entry.line}: coffee.en: changed validated value has no status record; set it to ai`,
  ]);
});

test('removals are detected by key and language, permitting removal of unvalidated values', () => {
  const base = inventory();
  const noJapanese = { fr: values.fr, en: values.en };
  assert.match(
    validatedChanges(base, inventory(noJapanese)).join('\n'),
    /coffee.ja: previously validated value was removed/,
  );
  const keyRemoved = parseSections(section('labels', { tea: noJapanese }));
  assert.equal(validatedChanges(base, keyRemoved).length, 2);
  const ai = inventory(values, statuses(values, 'ai', 'ai'));
  assert.deepEqual(validatedChanges(ai, inventory(noJapanese)), []);
  assert.deepEqual(validatedChanges(ai, keyRemoved), []);
});

test('delta uses origin/main merge base, not origin/main tip or HEAD, and reads working tree values', () =>
  withTemporaryDirectory((root) => {
    const git = (...args) =>
      execFileSync('git', ['--no-pager', ...args], {
        cwd: root,
        encoding: 'utf8',
        stdio: ['ignore', 'pipe', 'pipe'],
      }).trim();
    const directory = join(root, 'frontend');
    const write = (copy, reviews) =>
      writeSectionFiles(
        directory,
        section(
          'drink-labels',
          { coffee: copy },
          { coffee: reviews },
          'drinkLabels',
        ),
      );
    const commit = (message) => {
      git('add', '--', 'frontend');
      git('-c', 'commit.gpgSign=false', 'commit', '-qm', message);
      return git('rev-parse', 'HEAD');
    };
    git('init', '-q');
    git('config', 'user.name', 'Synthetic Reviewer');
    git('config', 'user.email', 'reviewer@example.test');
    mkdirSync(join(root, 'no-hooks'));
    git('config', 'core.hooksPath', 'no-hooks');
    write(values, statuses(values));
    const fork = commit('baseline');
    assert.throws(
      () => readMergeBase(directory),
      /Could not resolve the merge base/,
    );
    const mainCopy = { ...values, en: 'Main {x}' };
    write(mainCopy, statuses(mainCopy));
    git('update-ref', 'refs/remotes/origin/main', commit('advance main'));
    git('checkout', '--detach', '-q', fork);
    const featureCopy = { ...values, en: 'Feature {x}' };
    write(featureCopy, statuses(featureCopy));
    commit('advance feature');
    write(values, statuses(values));
    const { base, inventory: previous } = readMergeBase(directory);
    assert.equal(base, fork);
    const gitListing = execFileSync(
      'git',
      [
        '--no-pager',
        'ls-tree',
        '-r',
        '--name-only',
        base,
        '--',
        sectionsDirectory,
        statusDirectory,
      ],
      {
        cwd: directory,
        encoding: 'utf8',
      },
    )
      .trim()
      .split(/\r?\n/);
    assert.deepEqual(gitListing, [
      pathOf('drink-labels.ts'),
      statusPath('drink-labels'),
    ]);
    assert.equal(previous.entries.get('coffee').values.en, values.en);
    assert.equal(
      previous.entries.get('coffee').statuses.en.status,
      'validated',
    );
    assert.deepEqual(validatedChanges(previous, readSections(directory)), []);
    write(featureCopy, statuses(values));
    assert.match(
      validatedChanges(previous, readSections(directory)).join('\n'),
      /coffee.en: changed validated value/,
    );
    write(featureCopy, statuses(featureCopy, 'ai'));
    assert.deepEqual(validatedChanges(previous, readSections(directory)), []);
  }));
