import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';
import { appHarness, tick } from './app-operation-test-harness.mjs';

const source = readFileSync(new URL('../src/utils/subculture.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports });
const plain = (value) => JSON.parse(JSON.stringify(value));

test('committed subculture retains children when parent refresh fails and retries only the read', async () => {
  const h = appHarness();
  const result = { children: [{ id: 9, global_code: 'CHILD-9', current_polyp_state: { polyp_count: 0, revision: 'child-state', source: null } }] };
  const payload = { expected_current_state_revision: 'parent-state', reason: '', notes: '', children: [{ thermal_zone_id: 2, allocated_polyps: 0, copy_origin: true, notes: '' }] };
  const pending = h.context.createSubculture(7, payload);
  assert.equal(h.requests[0].payload, payload);
  h.requests[0].resolve(result);
  await tick();
  assert.equal(h.state.data.boxes[0], result.children[0]);
  h.requests[1].reject(new Error('parent refresh offline'));
  assert.equal(await pending, result);
  const recovery = h.state.recovery();
  assert.equal(h.requests[2].url, '/api/boxes/7/');
  assert.equal(h.requests[2].method, 'apiGet');
  h.requests[2].resolve({ id: 7 });
  await recovery;
  assert.equal(h.state.recovery, null);
  assert.equal(h.requests.filter(request => request.method === 'apiPost').length, 1);
});

test('allocation parser preserves explicit zero and the backend integer bound', () => {
  assert.equal(exports.parseAllocatedPolyps('0'), 0);
  assert.equal(exports.parseAllocatedPolyps('000'), 0);
  assert.equal(exports.parseAllocatedPolyps('42'), 42);
  assert.equal(exports.parseAllocatedPolyps('2147483647'), 2147483647);
});

for (const value of ['', ' ', ' 0', '0 ', '-1', '-0', '+1', '1.5', '1.0', '1e3', 'NaN', 'Infinity', '2147483648', '999999999999999999999']) {
  test(`allocation parser rejects ${JSON.stringify(value)} without coercion`, () => {
    assert.equal(exports.parseAllocatedPolyps(value), null);
  });
}

test('allocation summary accepts known zero and complete consumption', () => {
  assert.deepEqual(plain(exports.summarizeSubcultureAllocation(0, ['0'])), {
    available: 0, allocated: 0, remaining: 0, complete: true, overAllocated: false,
  });
  assert.deepEqual(plain(exports.summarizeSubcultureAllocation(12, ['5', '7'])), {
    available: 12, allocated: 12, remaining: 0, complete: true, overAllocated: false,
  });
});

test('blank and invalid drafts have no fabricated total or remainder', () => {
  for (const values of [[], [''], ['0', ''], ['2', '1.5']]) {
    const summary = exports.summarizeSubcultureAllocation(10, values);
    assert.equal(summary.allocated, null);
    assert.equal(summary.remaining, null);
    assert.equal(summary.complete, false);
  }
});

test('optional allocations retain exact arithmetic only when all counts are known', () => {
  for (const values of [[''], ['', ''], ['0', ''], ['30', '']]) {
    assert.deepEqual(plain(exports.summarizeSubcultureAllocation(50, values)), {
      available: 50, allocated: null, remaining: null, complete: false, overAllocated: false,
    });
  }
  assert.deepEqual(plain(exports.summarizeSubcultureAllocation(50, ['30', '0'])), {
    available: 50, allocated: 30, remaining: 20, complete: true, overAllocated: false,
  });
});

test('unknown stock is not zero, even with an explicit zero allocation', () => {
  assert.deepEqual(plain(exports.summarizeSubcultureAllocation(null, ['0'])), {
    available: null, allocated: 0, remaining: null, complete: true, overAllocated: false,
  });
});

test('over-allocation is detected in complete and partial drafts', () => {
  const complete = exports.summarizeSubcultureAllocation(3, ['2', '2']);
  assert.equal(complete.overAllocated, true);
  assert.equal(complete.remaining, -1);
  const partial = exports.summarizeSubcultureAllocation(3, ['4', '']);
  assert.equal(partial.overAllocated, true);
  assert.equal(partial.remaining, null);
});

test('only the backend assigns identities and allocation limits match its contract', () => {
  assert.equal(exports.MAX_SUBCULTURE_CHILDREN, 20);
  assert.equal(exports.MAX_ALLOCATED_POLYPS, 2147483647);
  assert.equal(exports.suggestChildIdentity, undefined);
  assert.doesNotMatch(source, /global_code|box_number|Math\.max|latest_measurement/);
});
