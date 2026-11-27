import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { boxAst } from './app-operation-test-harness.mjs';

function loadUtility(name, dependencies = {}) {
  const source = readFileSync(
    new URL(`../src/utils/${name}.ts`, import.meta.url),
    'utf8',
  );
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
  });
  const exports = {};
  runInNewContext(outputText, {
    exports,
    require: (path) => dependencies[path],
  });
  return exports;
}

const dateFormat = loadUtility('dateFormat');
const { formatDisplayCalendarDate, formatIsoWeekDateLabel } = dateFormat;
const { findMeasurementForWeek, formatMeasurementCount, getIsoWeekStart } =
  loadUtility('boxMeasurement', {
    './dateFormat': dateFormat,
  });

for (const value of [
  undefined,
  null,
  '',
  ' \t ',
  'not-a-date',
  '2026-2-03',
  '2026-02-30',
  '2026-13-01',
  '2026-01-00',
  '2026-12-31T25:00:00Z',
  '2026-12-31T00:00:00+25:00',
  '2026-02-30T00:00:00Z',
  '2026-12-31junk',
  '2026-12-31 ',
]) {
  test(`rejects an invalid ISO week date: ${JSON.stringify(value)}`, () => {
    assert.equal(getIsoWeekStart(value), null);
    assert.equal(formatIsoWeekDateLabel(value), null);
    assert.equal(formatDisplayCalendarDate(value), null);
  });
}

test('keeps Monday starts and week labels across ISO week-year boundaries', () => {
  for (const [date, monday, label] of [
    ['2024-02-29', '2024-02-26', 'S9 29 février 2024'],
    ['2025-12-29', '2025-12-29', 'S1 29 décembre 2025'],
    ['2026-12-31', '2026-12-28', 'S53 31 décembre 2026'],
    ['2027-01-01', '2026-12-28', 'S53 1 janvier 2027'],
    ['2027-01-04', '2027-01-04', 'S1 4 janvier 2027'],
  ]) {
    assert.equal(getIsoWeekStart(date), monday);
    assert.equal(formatIsoWeekDateLabel(date), label);
  }
  assert.equal(
    formatIsoWeekDateLabel('2027-01-01T00:30:00+14:00'),
    'S53 1 janvier 2027',
  );
  assert.equal(getIsoWeekStart('2027-01-01T00:30:00+14:00'), null);
});

test('the box weekly lookup skips invalid API dates and never matches an invalid reference', () => {
  const invalidReading = { measured_on: '' };
  const zeroReading = { measured_on: '2026-12-31', polyp_count: 0 };
  assert.equal(
    findMeasurementForWeek([invalidReading, zeroReading], '2027-01-01'),
    zeroReading,
  );
  assert.equal(findMeasurementForWeek([invalidReading], '2027-01-01'), null);
  assert.equal(findMeasurementForWeek([invalidReading, zeroReading], ''), null);
});

function TestMetric({ value }) {
  return React.createElement('strong', null, value);
}

test('the box summary renders the existing no-date fallback without hiding zero counts', () => {
  function findSummary(node) {
    if (
      ts.isJsxElement(node) &&
      node.openingElement.tagName.getText() === 'section'
    ) {
      const props = node.openingElement.attributes.properties;
      if (
        props.some(
          (prop) =>
            ts.isJsxAttribute(prop) &&
            prop.initializer?.getText().includes('last-reading-card'),
        )
      )
        return node;
    }
    return ts.forEachChild(node, findSummary);
  }

  const summary = findSummary(boxAst);
  assert.ok(summary);
  const { outputText } = ts.transpileModule(`(${summary.getText()});`, {
    compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 },
  });
  const latestMeasurement = {
    measured_on: '2026-02-30',
    polyp_count: 0,
    ephyrae_count: 0,
  };
  const markup = renderToStaticMarkup(
    runInNewContext(outputText, {
      React,
      weeklyMeasurement: findMeasurementForWeek(
        [latestMeasurement],
        '2026-03-02',
      ),
      showWeeklyMeasurementSummary: false,
      lastComment: undefined,
      box: { latest_measurement: latestMeasurement },
      formatDisplayCalendarDate,
      formatMeasurementCount,
      Metric: TestMetric,
      t: (key) => key,
    }),
  );

  assert.ok(markup.includes('<span>noDate</span>'));
  assert.equal(markup.match(/<strong>0<\/strong>/g)?.length, 2);
});
