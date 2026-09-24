import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/temperatureScale.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports, Math, Number, Object });

const { buildTemperatureScale } = exports;

function assertContainsEveryValue(input) {
  const scale = buildTemperatureScale(input);
  assert.ok(scale);
  const values = Object.values(input).filter((value) => typeof value === 'number');
  for (const value of values) {
    assert.ok(value >= scale.domain[0] && value <= scale.domain[1], `${value} outside ${scale.domain}`);
    const position = scale.project(value);
    assert.ok(position >= 7 && position <= 93, `${value} projected to ${position}`);
  }
}

test('target and one observed value use a guarded readable domain', () => {
  assertContainsEveryValue({ target: 10, average: 6.8 });
});

test('target and daily minimum, average and maximum all fit', () => {
  assertContainsEveryValue({ target: 10, minimum: 6.2, average: 6.8, maximum: 7.4 });
});

test('observations far below and far above target remain visible', () => {
  assertContainsEveryValue({ target: 20, average: -4 });
  assertContainsEveryValue({ target: 4, average: 31 });
});

test('tightly clustered values do not create an absurd zoom', () => {
  const scale = buildTemperatureScale({ target: 10, minimum: 9.9, average: 10.05, maximum: 10.1 });
  assert.ok(scale.domain[1] - scale.domain[0] >= 4);
});

test('a scale works without a target and for identical values', () => {
  assertContainsEveryValue({ minimum: 12, average: 12, maximum: 12 });
});

test('scientific zero remains a factual value', () => {
  const scale = buildTemperatureScale({ target: 2, average: 0 });
  assert.ok(scale.domain[0] <= 0 && scale.domain[1] >= 0);
  assert.ok(scale.project(0) >= 7);
});

test('single factual value gets human-readable ticks', () => {
  const scale = buildTemperatureScale({ average: 8.2 });
  assert.ok(scale.ticks.length >= 4 && scale.ticks.length <= 7);
  assert.ok(scale.ticks.every(Number.isFinite));
});

test('empty input has no scale', () => {
  assert.equal(buildTemperatureScale({}), null);
});
