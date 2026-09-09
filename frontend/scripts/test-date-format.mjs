import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/dateFormat.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports, Intl, Date });

const frLabels = { todayAt: 'Aujourd’hui à', yesterdayAt: 'Hier à' };
const enLabels = { todayAt: 'Today at', yesterdayAt: 'Yesterday at' };

function localIso(year, month, day, hour, minute) {
  return new Date(year, month - 1, day, hour, minute).toISOString();
}

function normalizeSpaces(value) {
  return value.replace(/\s/g, ' ');
}

test('formats today and yesterday by local calendar boundaries in French', () => {
  const today = exports.formatRelativeDateTime(
    localIso(2026, 9, 9, 14, 5),
    frLabels,
    new Date(2026, 8, 9, 23, 50),
    'fr-FR',
  );
  const yesterday = exports.formatRelativeDateTime(
    localIso(2026, 9, 8, 23, 58),
    frLabels,
    new Date(2026, 8, 9, 0, 3),
    'fr-FR',
  );

  assert.equal(today.relative, 'Aujourd’hui à 14:05');
  assert.equal(yesterday.relative, 'Hier à 23:58');
});

test('formats recent days, weeks and months in French', () => {
  const now = new Date(2026, 8, 9, 12, 0);

  assert.equal(
    normalizeSpaces(exports.formatRelativeDateTime(
      localIso(2026, 9, 6, 12, 0), frLabels, now, 'fr-FR',
    ).relative),
    'Il y a 3 j',
  );
  assert.equal(
    normalizeSpaces(exports.formatRelativeDateTime(
      localIso(2026, 8, 26, 12, 0), frLabels, now, 'fr-FR',
    ).relative),
    'Il y a 2 sem.',
  );
  assert.equal(
    normalizeSpaces(exports.formatRelativeDateTime(
      localIso(2026, 6, 9, 12, 0), frLabels, now, 'fr-FR',
    ).relative),
    'Il y a 3 mois',
  );
});

test('uses English labels and relative units for the English interface', () => {
  const now = new Date(2026, 8, 9, 18, 0);

  assert.equal(
    exports.formatRelativeDateTime(localIso(2026, 9, 9, 9, 30), enLabels, now, 'en-GB').relative,
    'Today at 09:30',
  );
  assert.equal(
    exports.formatRelativeDateTime(localIso(2026, 9, 8, 17, 42), enLabels, now, 'en-GB').relative,
    'Yesterday at 17:42',
  );
  assert.equal(
    exports.formatRelativeDateTime(localIso(2026, 9, 6, 12, 0), enLabels, now, 'en-GB').relative,
    '3 days ago',
  );
});

test('preserves the exact local date and time alongside the relative label', () => {
  const formatted = exports.formatRelativeDateTime(
    localIso(2026, 9, 9, 14, 5),
    frLabels,
    new Date(2026, 8, 9, 18, 0),
    'fr-FR',
  );

  assert.match(formatted.exact, /09\/09\/2026/);
  assert.match(formatted.exact, /14:05/);
});
