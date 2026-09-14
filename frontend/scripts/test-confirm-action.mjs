import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/confirmActionResolver.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports });

test('a first confirmation request is accepted and stays pending', () => {
  const resolver = exports.createPendingResolver();
  const values = [];
  assert.equal(resolver.request((value) => values.push(value)), true);
  assert.equal(resolver.isPending(), true);
  assert.deepEqual(values, []);
});

test('a second request is declined without orphaning the first resolver', () => {
  const resolver = exports.createPendingResolver();
  const first = [];
  const second = [];
  assert.equal(resolver.request((value) => first.push(value)), true);
  assert.equal(resolver.request((value) => second.push(value)), false);
  assert.equal(resolver.settle(true), true);
  assert.deepEqual(first, [true]);
  assert.deepEqual(second, []);
});

test('settling resolves exactly once', () => {
  const resolver = exports.createPendingResolver();
  const values = [];
  resolver.request((value) => values.push(value));
  assert.equal(resolver.settle(false), true);
  assert.equal(resolver.settle(true), false);
  assert.deepEqual(values, [false]);
  assert.equal(resolver.isPending(), false);
});

test('a new confirmation can be requested after the previous one settles', () => {
  const resolver = exports.createPendingResolver();
  resolver.request(() => {});
  resolver.settle(true);
  assert.equal(resolver.request(() => {}), true);
});

test('a confirmation still pending when its owner unmounts resolves as cancelled', () => {
  const resolver = exports.createPendingResolver();
  const values = [];
  assert.equal(resolver.request((value) => values.push(value)), true);
  // The hook settles its own resolver in its unmount cleanup.
  assert.equal(resolver.settle(false), true);
  assert.deepEqual(values, [false]);
  assert.equal(resolver.isPending(), false);
});

test('a later confirmation opens and resolves normally after an unmount cancellation', () => {
  const resolver = exports.createPendingResolver();
  resolver.request(() => {});
  resolver.settle(false);
  const values = [];
  assert.equal(resolver.request((value) => values.push(value)), true);
  assert.equal(resolver.isPending(), true);
  assert.equal(resolver.settle(true), true);
  assert.deepEqual(values, [true]);
});

test('cancelling one consumer leaves another consumer pending', () => {
  const first = exports.createPendingResolver();
  const second = exports.createPendingResolver();
  const firstValues = [];
  const secondValues = [];
  first.request((value) => firstValues.push(value));
  second.request((value) => secondValues.push(value));
  assert.equal(first.settle(false), true);
  assert.equal(first.isPending(), false);
  assert.equal(second.isPending(), true);
  assert.deepEqual(firstValues, [false]);
  assert.deepEqual(secondValues, []);
  assert.equal(second.settle(true), true);
  assert.deepEqual(secondValues, [true]);
});
